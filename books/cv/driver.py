"""Page -> extractions: the full pipeline driver."""

import logging
from dataclasses import dataclass

import chess
import cv2

from books.cv import calibrate, fen as fen_utils
from books.cv.classify import extract_glyph
from books.cv.grid import detect_diagrams, erase_grid_lines, warped_grid_lines
from books.cv.recognize import recognize_glyph
from books.cv.squares import split_board

logger = logging.getLogger("books.cv")


class CalibrationError(Exception):
    """The book has no (or an incomplete) calibration profile."""


def gray_view(img) -> "np.ndarray":
    import numpy as np

    if img.ndim == 2:
        return img
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


@dataclass
class ExtractionResult:
    fen: str
    legal: bool
    confidence: float
    bbox: list  # [x, y, w, h] original px
    cells: list  # 64 piece codes (rank 8 first), None = empty, "?" = unknown
    mean_corr: float
    unknown_count: int
    notes: list[str]
    number: str | None = None  # printed number read from above the diagram
    slot_index: int | None = None  # 1-based grid position (steps layouts)


def extract_page(img, profile: dict | None,
                 page_number: int | None = None) -> list[ExtractionResult]:
    """Extract every diagram from a page image using the calibrated profile.

    When the profile carries a stored page LAYOUT (fixed diagram slots), the
    slots are used as candidates alongside the generic detection — weak
    pages still yield every diagram. Steps books (numbering mode "slots")
    take their own path (books.cv.steps) with positional numbering.
    """
    is_steps = bool(
        profile
        and profile.get("numbering", {}).get("mode") in ("slots", "printed")
    )
    if not profile or (not profile.get("templates") and not is_steps):
        raise CalibrationError("book has no calibration profile")

    if is_steps:
        from books.cv import steps

        return steps.extract_steps_page(img, profile, page_number=page_number)

    empty_max_ink = profile.get("empty", {}).get("max_ink", 0.04)
    baseline = profile.get("style", {}).get("baseline", False)

    # OCR bridge first when a layout exists: the neural recognizer reads the
    # pieces far better than the CV templates on real book scans
    if profile.get("layout"):
        from books.cv.layout import layout_diagrams

        try:
            from books import ocr

            column_major = profile.get("numbering", {}).get("order") == "column_major"
            slots = layout_diagrams(
                gray_view(img), profile["layout"], column_major=column_major
            )
            margin = int((profile.get("steps") or {}).get("crop_margin", 100))
            crops = [ocr.crop_slot(img, slot.bbox, margin=margin) for slot in slots]
            fens = ocr.batch_fens(crops)
            ocr_results = []
            for slot_index, (slot, fen) in enumerate(zip(slots, fens), start=1):
                if not fen:
                    continue
                # "number detected but no board": slots that hold only a
                # printed number (or caption) OCR to an EMPTY board — those
                # are not puzzles and must be skipped entirely
                try:
                    piece_count = len(chess.Board(fen.split(" ")[0]).piece_map())
                except ValueError:
                    piece_count = -1
                if piece_count <= 0:
                    logger.info("skipping slot without a board: %s", slot.bbox)
                    continue
                # validate: junk slots (captions, empty areas) produce FENs
                # without both kings — flag them for review
                _fen, legal, reasons = fen_utils.validate_fen(fen)
                # the book's OWN numbering: read the printed number above
                # the diagram (no arithmetic — the book skips numbers)
                number = None
                try:
                    from books.cv.number_ocr import load_templates, read_number

                    templates = load_templates(profile)
                    if templates is not None:
                        number = read_number(
                            gray_view(img), slot.bbox, templates
                        )
                except Exception as exc:  # noqa: BLE001 - best-effort
                    logger.warning("number OCR failed: %s", exc)
                ocr_results.append(
                    ExtractionResult(
                        fen=fen, legal=legal,
                        confidence=0.9 if legal else 0.3,
                        bbox=list(slot.bbox), cells=[], mean_corr=1.0,
                        unknown_count=0, notes=reasons, number=number,
                        slot_index=slot_index,
                    )
                )
            if ocr_results:
                return ocr_results
        except Exception as exc:  # noqa: BLE001 - OCR is best-effort
            logger.warning("OCR extraction failed: %s", exc)

    diagrams = detect_diagrams(img)
    if profile.get("layout") and not diagrams:
        # layout slots as fallback candidates for the CV path
        from books.cv.layout import layout_diagrams
        from books.cv.grid import _validated

        diagrams = _validated(layout_diagrams(gray_view(img), profile["layout"]),
                              gray_view(img))
    results = []
    for diagram in diagrams:
        lines = warped_grid_lines(diagram.board_img)
        if lines is not None:
            vx, hy = lines
            board = diagram.board_img
            if baseline:
                # baseline-style pieces sit ON the grid lines; erase the
                # (dotted) lines so they don't leak into the square patches
                board = erase_grid_lines(board, vx, hy)
            patches = split_board(board, vx=vx, hy=hy, baseline=baseline)
        else:
            patches = split_board(diagram.board_img, baseline=baseline)
        flat_patches = [p for rank in patches for p in rank]  # rank 8 first
        cells = []
        corrs = []
        unknown = 0
        for square_index, patch in enumerate(flat_patches):
            glyph = extract_glyph(patch, empty_max_ink=empty_max_ink)
            code, corr = recognize_glyph(glyph, profile, square_index)
            cells.append(code)
            corrs.append(corr)
            if code == "?":
                unknown += 1

        matrix = [[cells[r * 8 + c] for c in range(8)] for r in range(8)]
        fen, legal, reasons = fen_utils.build_fen(matrix)
        # "number detected but no board": skip entirely, never a puzzle
        # (a '?' cell is an UNKNOWN, not a piece — it renders as empty)
        if not any(code for code in cells if code is not None and code != "?"):
            logger.info("skipping slot without a board: %s", diagram.bbox)
            continue
        mean_corr = sum(corrs) / len(corrs) if corrs else 0.0
        confidence = 0.40 * legal + 0.35 * mean_corr + 0.25 * (1 - unknown / 64)
        x, y, w, h = diagram.bbox
        results.append(
            ExtractionResult(
                fen=fen,
                legal=legal,
                confidence=round(confidence, 3),
                bbox=[x, y, w, h],
                cells=cells,
                mean_corr=round(mean_corr, 3),
                unknown_count=unknown,
                notes=reasons,
            )
        )
    return results
