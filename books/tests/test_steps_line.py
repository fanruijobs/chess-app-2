"""Line-drawn board pages (woodpecker style): frame detection, board gate,
turn markers, extraction with a fake OCR."""

import chess
import cv2
import numpy as np
import pytest

from books.cv.steps import (
    apply_turn,
    detect_turn_marker,
    extract_line_page,
    is_line_board,
    line_slots,
)

pytestmark = pytest.mark.django_db

FEN = "r6r/1pp3k1/1b6/p2P1p2/P1N1pn2/2P2PP1/BP5P/4RR1K w - - 0 1"

BOARD_W = BOARD_H = 404
# realistic 200dpi page positions: 2 columns x 1 row at printed-page coords
COL_XS = [151, 773]
ROW_YS = [257]


def _board(page, x0, y0, pieces=2):
    x1, y1 = x0 + BOARD_W, y0 + BOARD_H
    cv2.rectangle(page, (x0, y0), (x1, y1), 0, 3)  # solid frame
    # a couple of filled "pieces" (ink sanity for the gate)
    for k in range(pieces):
        cx, cy = x0 + 60 + 90 * k, y0 + 150
        cv2.rectangle(page, (cx, cy), (cx + 30, cy + 30), 0, -1)
    return x1, y1


def _filled_triangle(page, x, y, side):
    """Solid downward triangle of the printed size (~34x30 px)."""
    cv2.fillPoly(
        page, [np.array([[x, y], [x + 34, y], [x + 17, y + 30]])], 0
    )
    return side


def _outline_triangle(page, x, y):
    """Outlined upward triangle (apex at top)."""
    cv2.polylines(
        page, [np.array([[x, y], [x + 34, y], [x + 17, y + 30]])], True, 0, 2
    )


def line_page(turns=None) -> np.ndarray:
    """Synthetic 200dpi woodpecker page: 2 boards side by side."""
    page = np.full((1890, 1339), 255, dtype=np.uint8)
    x1, y1 = _board(page, COL_XS[0], ROW_YS[0])
    x2, y2 = _board(page, COL_XS[1], ROW_YS[0])
    for index, turn in enumerate(turns or []):
        if index == 0:
            x, y = x1 + 8, ROW_YS[0] + (10 if turn == "b" else BOARD_H - 40)
            if turn == "b":
                _filled_triangle(page, x, y, turn)
            else:
                _outline_triangle(page, x, y)
        else:
            x, y = x2 + 8, ROW_YS[0] + (10 if turn == "b" else BOARD_H - 40)
            if turn == "b":
                _filled_triangle(page, x, y, turn)
            else:
                _outline_triangle(page, x, y)
    return page


def line_profile(**steps_overrides):
    return {
        "numbering": {"mode": "slots", "format": "p{page:03d}-{slot:02d}"},
        "steps": {"prefer": "ocr", "turn_markers": True, **steps_overrides},
    }


def test_line_slots_finds_board_columns_and_rows():
    gray = line_page()
    cols, rows, slots = line_slots(gray)
    assert [c[0] for c in cols] == pytest.approx(COL_XS, abs=4)
    assert [r[0] for r in rows] == pytest.approx(ROW_YS, abs=4)
    assert len(slots) == 2


def test_line_slots_empty_page_yields_nothing():
    gray = np.full((1890, 1339), 255, dtype=np.uint8)
    # short caption strokes must not pair up
    for k in range(30):
        x = 100 + 40 * k
        cv2.rectangle(gray, (x, 400), (x + 12, 440), 0, 2)
    cols, rows, slots = line_slots(gray)
    assert cols == [] and rows == [] and slots == []


def test_is_line_board():
    page = line_page()
    x, y = COL_XS[0] - 2, ROW_YS[0] - 2
    crop = page[y:y + BOARD_H + 5, x:x + BOARD_W + 5]
    assert is_line_board(crop)
    assert not is_line_board(np.full((400, 400), 255, dtype=np.uint8))
    assert not is_line_board(np.full((400, 400), 0, dtype=np.uint8))
    assert not is_line_board(np.zeros((30, 30), dtype=np.uint8))


def test_detect_turn_marker():
    gray = line_page(["b", "w"])
    cols, rows, slots = line_slots(gray)
    right = cols[1][0]  # gap of the first board ends at the next column
    assert detect_turn_marker(gray, slots[0].bbox, right_edge=right) == "b"
    assert detect_turn_marker(gray, slots[1].bbox) == "w"
    plain = line_page()
    cols, rows, slots = line_slots(plain)
    assert detect_turn_marker(plain, slots[0].bbox, right_edge=right) is None


def test_apply_turn():
    assert apply_turn(FEN, "b").split(" ")[1] == "b"
    assert apply_turn(FEN, "w").split(" ")[1] == "w"
    assert apply_turn("placement-only", "b") == "placement-only"


def test_extract_line_page_with_fake_ocr(monkeypatch):
    from books import ocr

    page = line_page(["b", "w"])
    img = cv2.cvtColor(page, cv2.COLOR_GRAY2BGR)

    def fake_batch(crops):
        return [FEN for _ in crops]

    monkeypatch.setattr(ocr, "batch_fens", fake_batch)
    results = extract_line_page(img, line_profile(), page_number=33)
    assert len(results) == 2
    assert results[0].fen.split(" ")[1] == "b"
    assert results[1].fen.split(" ")[1] == "w"
    assert results[0].number == "p033-01"
    assert chess.Board(results[0].fen)  # legal
