"""Steps-workbook scanner: bands, board gate, OCR/CV extraction, numbering."""

import chess
import cv2
import numpy as np
import pytest
from django.core.management import call_command
from django.test import override_settings

from books.cv.steps import extract_steps_page, is_full_board, steps_bands
from books.models import Book, CalibrationProfile, PageImage, Puzzle

from books.tests.synthetic import (
    STEPS_BOARD,
    STEPS_COLS,
    STEPS_PAGE,
    STEPS_ROWS,
    build_solid_profile,
    fen_to_solid_board,
    fen_to_steps_page,
)

pytestmark = pytest.mark.django_db

FEN_A = "4k3/8/8/8/8/8/4P3/4K3 w - - 0 1"
FEN_B = "r6k/8/8/8/8/8/8/R6K w - - 0 1"
FEN_C = "8/8/8/8/3Q4/8/8/4k2K w - - 0 1"

POSITIONS = [FEN_A, FEN_B, FEN_C, "8/8/8/8/8/8/5n2/4k2K w - - 0 1"]


def steps_layout():
    h, w = STEPS_PAGE
    return {
        "columns": [[x / w, (x + STEPS_BOARD) / w] for x in STEPS_COLS],
        "rows": [[y / h, (y + STEPS_BOARD) / h] for y in STEPS_ROWS],
    }


def steps_profile(**steps_overrides):
    data = {
        "layout": steps_layout(),
        "numbering": {"mode": "slots", "format": "p{page:03d}-{slot:02d}"},
        "steps": {"threshold": 230, "crop_margin": 30, **steps_overrides},
    }
    return data


def board_aware_fake(fens):
    """Fake OCR: returns the next FEN only for board-like crops (ink-heavy,
    real boards measure ~0.30), None for white/text crops — mimics the real
    model's empty answers."""
    queue = list(fens)

    def fake_batch(crops):
        out = []
        for crop in crops:
            gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
            if (gray < 250).mean() > 0.15:
                out.append(queue.pop(0) if queue else None)
            else:
                out.append(None)
        return out

    return fake_batch


# --- bands ----------------------------------------------------------------


def test_steps_bands_3x4_grid():
    fens = [FEN_A] * 12
    page = fen_to_steps_page(fens)
    gray = cv2.cvtColor(page, cv2.COLOR_BGR2GRAY)
    cols, rows = steps_bands(gray)
    assert len(cols) == 3 and len(rows) == 4
    for band, expected in zip(cols, STEPS_COLS):
        assert abs(band[0] - expected) <= 3 and abs(band[1] - (expected + STEPS_BOARD)) <= 3
    for band, expected in zip(rows, STEPS_ROWS):
        assert abs(band[0] - expected) <= 3 and abs(band[1] - (expected + STEPS_BOARD)) <= 3


def test_steps_bands_partial_rows():
    page = fen_to_steps_page([FEN_A] * 6)  # 2 full rows
    gray = cv2.cvtColor(page, cv2.COLOR_BGR2GRAY)
    cols, rows = steps_bands(gray)
    assert len(cols) == 3 and len(rows) == 2


def test_steps_bands_text_page_rejected():
    page = np.full((STEPS_PAGE[0], STEPS_PAGE[1], 3), 255, dtype=np.uint8)
    cv2.putText(page, "Some instructions text", (200, 400),
                cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 0), 3)
    gray = cv2.cvtColor(page, cv2.COLOR_BGR2GRAY)
    cols, rows = steps_bands(gray)
    assert len(cols) < 2 or len(rows) < 2


# --- board gate ------------------------------------------------------------


def test_is_full_board_accepts_board():
    board = fen_to_solid_board(FEN_A, size=STEPS_BOARD)
    gray = cv2.cvtColor(board, cv2.COLOR_BGR2GRAY)
    assert is_full_board(gray)


def test_is_full_board_rejects_5x5_corner():
    # a printed 5x5 partial board (d4-h8 style) must NOT pass the gate: its
    # gap lines alone look board-ish, but the OCR layer cannot be trusted on
    # it (horsehead's white 5x5 chapters produce hallucinated quadrant FENs)
    board = fen_to_solid_board(FEN_B, size=STEPS_BOARD * 5 // 8)
    gray_board = cv2.cvtColor(board, cv2.COLOR_BGR2GRAY)
    slot = np.full((STEPS_BOARD, STEPS_BOARD), 255, dtype=np.uint8)
    slot[: gray_board.shape[0], -gray_board.shape[1]:] = gray_board
    assert not is_full_board(slot)


def test_is_full_board_rejects_text():
    block = np.full((STEPS_BOARD, STEPS_BOARD), 255, dtype=np.uint8)
    cv2.putText(block, "no board", (20, 130), cv2.FONT_HERSHEY_SIMPLEX, 1.2, 0, 3)
    assert not is_full_board(block)


# --- extraction ------------------------------------------------------------


def test_extract_steps_page_ocr_path(monkeypatch):
    """OCR path: slot_index/number come from grid position; empties skipped."""
    page = fen_to_steps_page([FEN_A] * 12)
    seen = []
    fake_fens = [FEN_B if i in (0, 1, 2, 3) else None for i in range(12)]
    fake_fens_iter = iter(fake_fens)

    def fake_batch(crops):
        seen.extend(crops)
        return [next(fake_fens_iter) for _ in crops]

    monkeypatch.setattr("books.ocr.batch_fens", fake_batch)
    results = extract_steps_page(page, steps_profile(), page_number=3)

    assert len(seen) == 12
    # gutter-safe margin: the tight slot is 266px, +30px each side
    # (±1px float jitter in the fraction-derived bboxes)
    assert all(abs(c.shape[0] - (STEPS_BOARD + 60)) <= 1
               and abs(c.shape[1] - (STEPS_BOARD + 60)) <= 1 for c in seen)

    assert [r.slot_index for r in results] == [1, 2, 3, 4]
    assert [r.number for r in results] == ["p003-01", "p003-02", "p003-03", "p003-04"]
    assert all(r.legal for r in results)
    assert all(r.confidence == 0.9 for r in results)


def test_extract_steps_page_ocr_skips_empty_and_pieceless(monkeypatch):
    """Empty OCR answers and empty boards are skipped entirely."""
    page = fen_to_steps_page([FEN_A] * 3)
    # slot 1 good, slot 2 no answer, slot 3 OCRs to an empty board
    monkeypatch.setattr(
        "books.ocr.batch_fens",
        board_aware_fake([FEN_B, None, "8/8/8/8/8/8/8/8 w - - 0 1"]),
    )
    results = extract_steps_page(page, steps_profile(), page_number=3)
    assert [r.slot_index for r in results] == [1]


def test_extract_steps_page_cv_path():
    """CV fallback on solid boards: legal positions with both kings.

    (Uses the pawn/king position — the outlined SVG pieces of other positions
    are misread by the synthetic CV recognizer; real books calibrate per
    book instead.)
    """
    profile = build_solid_profile(POSITIONS)
    page = fen_to_steps_page([FEN_A])
    results = extract_steps_page(
        page, {**profile, **steps_profile(prefer="cv")}, page_number=3
    )
    assert len(results) == 1
    result = results[0]
    assert result.number == "p003-01" and result.slot_index == 1
    board = chess.Board(result.fen)
    assert result.legal
    assert board.king(chess.WHITE) is not None
    assert board.king(chess.BLACK) is not None
    assert result.confidence > 0.5


def test_extract_steps_page_skips_non_board_slots(monkeypatch):
    """A text block in one slot is skipped, not OCR'd and not numbered."""
    page = fen_to_steps_page([FEN_A] * 2)
    gray = cv2.cvtColor(page, cv2.COLOR_BGR2GRAY)
    x, y = STEPS_COLS[2], STEPS_ROWS[0]  # slot 3 becomes a text block
    page[y:y + STEPS_BOARD, x:x + STEPS_BOARD] = 255
    cv2.putText(page, "drawing", (x + 40, y + 140),
                cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 0), 3)

    seen = []

    def fake_batch(crops):
        seen.extend(crops)
        # the text block is low-ink — mimic the real OCR answering empty
        out = []
        for c in crops:
            gray = cv2.cvtColor(c, cv2.COLOR_BGR2GRAY) if c.ndim == 3 else c
            out.append(FEN_B if (gray < 250).mean() > 0.15 else None)
        return out

    monkeypatch.setattr("books.ocr.batch_fens", fake_batch)
    results = extract_steps_page(page, steps_profile(), page_number=3)
    assert [r.slot_index for r in results] == [1, 2]  # 3 is a text block
    # slot 3 may be probed once more by the region fallback, but never kept
    assert len(seen) >= 2


# --- numbering / upsert ----------------------------------------------------


def test_upsert_steps_numbering():
    from books.cv.driver import ExtractionResult
    from books.services import upsert_puzzles

    book = Book.objects.create(title="Steps 6", slug="steps-6")
    page = PageImage.objects.create(book=book, page_number=3, image="x.png")
    results = [
        ExtractionResult(
            fen=FEN_B, legal=True, confidence=0.9, bbox=[0, 0, 1, 1],
            cells=[], mean_corr=1.0, unknown_count=0, notes=[],
            number=f"p003-0{i}", slot_index=i,
        )
        for i in (1, 3)  # slot 2 was an artist drawing — never a result
    ]
    created, updated, skipped = upsert_puzzles(book, page, results)
    assert created == 2 and updated == 0 and skipped == 0
    rows = {p.diagram_index: p.number for p in page.puzzles.all()}
    assert rows == {1: "p003-01", 3: "p003-03"}


def test_set_layout_steps_command(tmp_path):
    import pymupdf

    page_img = fen_to_steps_page([FEN_A] * 12)
    ok, png = cv2.imencode(".png", page_img)
    doc = pymupdf.open()
    pdf_page = doc.new_page(width=STEPS_PAGE[1], height=STEPS_PAGE[0])
    pdf_page.insert_image(pymupdf.Rect(0, 0, STEPS_PAGE[1], STEPS_PAGE[0]),
                          stream=png.tobytes())
    pdf_path = tmp_path / "book.pdf"
    doc.save(str(pdf_path))
    doc.close()

    book = Book.objects.create(title="Steps 6", slug="steps-6")
    call_command("set_layout", "steps-6", str(pdf_path), "1", "--steps")
    profile = CalibrationProfile.objects.get(book=book)
    assert len(profile.data["layout"]["columns"]) == 3
    assert len(profile.data["layout"]["rows"]) == 4
    assert profile.data["numbering"] == {
        "mode": "slots", "format": "p{page:03d}-{slot:02d}"
    }
    assert profile.data["steps"]["crop_margin"] == 30


def test_set_layout_steps_refuses_text_page(tmp_path):
    import pymupdf

    blank = np.full((STEPS_PAGE[0], STEPS_PAGE[1], 3), 255, dtype=np.uint8)
    ok, png = cv2.imencode(".png", blank)
    doc = pymupdf.open()
    pdf_page = doc.new_page(width=STEPS_PAGE[1], height=STEPS_PAGE[0])
    pdf_page.insert_image(pymupdf.Rect(0, 0, STEPS_PAGE[1], STEPS_PAGE[0]),
                          stream=png.tobytes())
    pdf_path = tmp_path / "blank.pdf"
    doc.save(str(pdf_path))
    doc.close()

    Book.objects.create(title="Steps 6", slug="steps-6")
    with pytest.raises(Exception, match="Grid not detected"):
        call_command("set_layout", "steps-6", str(pdf_path), "1", "--steps")


# --- book detail ordering --------------------------------------------------


def test_book_detail_slots_ordering(client, tmp_path):
    book = Book.objects.create(title="Steps 6", slug="steps-6")
    CalibrationProfile.objects.create(book=book, data=steps_profile())
    p3 = PageImage.objects.create(book=book, page_number=3, image="x.png")
    p4 = PageImage.objects.create(book=book, page_number=4, image="y.png")
    for number, page, index in [("p004-12", p4, 12), ("p003-01", p3, 1),
                                ("p003-02", p3, 2), ("p004-01", p4, 1)]:
        Puzzle.objects.create(book=book, page=page, diagram_index=index,
                              number=number, fen=FEN_A, status=Puzzle.Status.EXTRACTED)
    response = client.get(f"/books/{book.slug}/")
    ordered = [p.number for p in response.context["puzzles"].object_list]
    assert ordered == ["p003-01", "p003-02", "p004-01", "p004-12"]
    # exact-match goto works for positional numbers
    target = Puzzle.objects.get(number="p004-12")
    response = client.get(f"/books/{book.slug}/?goto=p004-12")
    assert response.status_code == 302
    assert response.headers["Location"] == f"/puzzles/{target.pk}/"


# --- directory import ------------------------------------------------------


def test_import_steps_pages(monkeypatch, client, tmp_path):
    book = Book.objects.create(title="Steps 6", slug="steps-6")
    CalibrationProfile.objects.create(book=book, data=steps_profile())

    grid_page = fen_to_steps_page([FEN_A] * 12)
    text_page = np.full((STEPS_PAGE[0], STEPS_PAGE[1], 3), 255, dtype=np.uint8)
    cv2.putText(text_page, "lesson header", (300, 300),
                cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 0), 3)
    cv2.imwrite(str(tmp_path / "03_grid.png"), grid_page)
    cv2.imwrite(str(tmp_path / "04_text.png"), text_page)

    def fake_batch(crops):
        # text-page crops are mostly white — simulate the real OCR returning
        # nothing for them, boards for the grid page
        return [
            FEN_B if (cv2.cvtColor(c, cv2.COLOR_BGR2GRAY) < 250).mean() > 0.15
            else None
            for c in crops
        ]

    monkeypatch.setattr("books.ocr.batch_fens", fake_batch)
    with override_settings(MEDIA_ROOT=str(tmp_path / "media")):
        call_command("import_pages", "steps-6", str(tmp_path))

    puzzles = Puzzle.objects.filter(book=book)
    assert puzzles.count() == 12
    assert set(puzzles.values_list("number", flat=True)) == {
        f"p003-{i:02d}" for i in range(1, 13)
    }
    assert PageImage.objects.filter(book=book).count() == 2
    assert Puzzle.objects.filter(book=book, page__page_number=4).count() == 0
