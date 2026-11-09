"""Steps-workbook pages: solid-fill boards on a fixed 3x4 slot grid.

Unlike the Polgar book (white boards, thin dotted grid lines), Steps pages
print boards as SOLID-FILLED squares — light squares ~255, dark squares
~208-223 gray, solid black silhouette pieces, thin frame, no coordinates and
no printed numbers. The low square contrast defeats `detect_diagrams` (its
adaptive threshold sees nothing); the reliable signal is the row/column ink
profile at a threshold just above the dark squares (~230), which yields clean
column/row bands for the diagram grid. Numbering is positional: the profile's
`numbering.format` template (e.g. "p{page:02d}-{slot:02d}") with the slot's
1-based grid position (right, then down).
"""

import logging
from dataclasses import dataclass

import chess
import cv2
import numpy as np

from books.cv import fen as fen_utils
from books.cv.classify import extract_glyph
from books.cv.driver import ExtractionResult, gray_view
from books.cv.grid import _warp, erase_grid_lines, warped_grid_lines
from books.cv.layout import layout_diagrams
from books.cv.recognize import recognize_glyph
from books.cv.squares import split_board

logger = logging.getLogger("books.cv")

DEFAULT_THRESHOLD = 230  # gray cut: dark squares 208-223, light ~255
MIN_COL_WIDTH = 80  # board columns are ~266px wide at 150dpi
MIN_ROW_HEIGHT = 100  # board rows are ~266px tall; text lines ~15px
MERGE_GAP = 10  # px — small profile dips inside a band are merged
BAND_FLOOR = 0.06  # absolute floor for the ink signal (a single board column
# dilutes to ~0.12 on a 3-column page — keep the floor well below that)
BAND_FLOOR_FRACTION = 0.35  # relative to the page's max signal
MIN_GAP_LINES = 6  # white gap lines per axis (some boards' lines are
# obscured by pieces; the checkerboard correlation covers those)
BOARD_CORR_CUT = 0.4  # checkerboard correlation of the 8x8 cell means
# (piece-heavy boards at import dpi measure ~0.45; cover art/text <= 0.10,
# 5x5 partials ~0.16 — the gap-line branch covers page-4-style boards)
DARK_SQUARE_CUT = 235  # 8x8 cell mean below this = a solid-filled dark square
# (white line-drawn boards stay well above — they must not pass via gap lines)
INKED_CELL_CUT = 190  # cell mean below this = a piece is present


def steps_bands(gray, threshold=DEFAULT_THRESHOLD):
    """(col_bands, row_bands): [[start, end], ...] px extents of the slot grid.

    Variable row counts come out naturally (partial pages yield fewer bands).
    """
    binary = gray < threshold

    def bands(signal, min_width):
        floor = max(BAND_FLOOR, BAND_FLOOR_FRACTION * float(signal.max()))
        runs = []
        start = None
        for i, v in enumerate(signal):
            if v > floor:
                if start is None:
                    start = i
            elif start is not None:
                runs.append([start, i])
                start = None
        if start is not None:
            runs.append([start, len(signal)])
        merged = []
        for run in runs:
            if merged and run[0] - merged[-1][1] < MERGE_GAP:
                merged[-1][1] = run[1]
            else:
                merged.append(run)
        return [[s, e] for s, e in merged if e - s >= min_width]

    return bands(binary.mean(axis=0), MIN_COL_WIDTH), bands(
        binary.mean(axis=1), MIN_ROW_HEIGHT
    )


@dataclass
class Slot:
    """One grid cell (row-major) of a steps puzzle page."""

    bbox: tuple[int, int, int, int]  # x, y, w, h in original px
    index: int  # 1-based grid position (right, then down)


def steps_slots(gray, layout) -> list[Slot]:
    """Row-major Slot list for a stored layout (same ordering as the import)."""
    return [
        Slot(bbox=d.bbox, index=i)
        for i, d in enumerate(layout_diagrams(gray, layout), start=1)
    ]


def steps_slots_from_bands(col_bands, row_bands) -> list[Slot]:
    """Row-major Slot list from detected band extents (per-page geometry)."""
    return [
        Slot(bbox=(x0, y0, x1 - x0, y1 - y0), index=1 + row * len(col_bands) + col)
        for row, (y0, y1) in enumerate(row_bands)
        for col, (x0, x1) in enumerate(col_bands)
    ]


def _expand_short_bands(bands, page_side):
    """Expand clipped bands to the page's full board size.

    The target is the MEDIAN band size (dpi-independent) — some pages' boards
    extend beyond their ink-dense band, and the import renders at a different
    dpi than calibration. Clamped between adjacent bands and the page edges.
    """
    target = float(np.median([end - start for start, end in bands]))
    for i, (start, end) in enumerate(bands):
        if end - start >= 0.9 * target:
            continue
        center = (start + end) / 2
        low_limit = (bands[i - 1][0] + bands[i - 1][1]) / 2 if i > 0 else 0
        high_limit = (
            (bands[i + 1][0] + bands[i + 1][1]) / 2 if i + 1 < len(bands) else page_side
        )
        new_start = max(low_limit, center - target / 2)
        new_end = min(high_limit, new_start + target)
        bands[i] = [int(new_start), int(new_end)]
    return bands


def _plausible_bands(col_bands, row_bands, page_shape=None) -> bool:
    """Accept per-page bands only when they form a real grid: artist drawings
    fragment the profiles into junk bands (narrow slivers) — those pages fall
    back to the stored layout.

    Grids are 3 columns (steps/horsehead standard pages) or 4 columns
    (horsehead chapter pages, 4x3); rows 1-4 (partial pages exist). Size
    floors are dpi-aware fractions of the page side.
    """
    if len(col_bands) not in (3, 4) or not 1 <= len(row_bands) <= 4:
        return False
    if len(col_bands) == 4 and len(row_bands) not in (2, 3):
        return False
    ph, pw = (page_shape or (1, 1))[:2]
    # board columns are ~266px at 150dpi; rows ~266px (some pages ~190px)
    min_col = max(200, 0.15 * pw)
    min_row = max(150, 0.12 * ph)
    if not all(e - s >= min_col for s, e in col_bands):
        return False
    if not all(e - s >= min_row for s, e in row_bands):
        return False
    return True


def _gap_line_count(profile: np.ndarray) -> int:
    """Count bright full-extent strips in a min-intensity profile.

    Steps boards are solid-filled squares separated by thin WHITE gap lines
    that run the full board extent; a column/row minimum over the crop is
    bright (~255) only where such a line exists for its whole length.
    """
    smoothed = cv2.GaussianBlur(
        profile.astype(float).reshape(-1, 1), (9, 1), 0
    ).ravel()
    bright = smoothed > (np.median(smoothed) + 15)
    runs = []
    start = None
    for i, is_bright in enumerate(bright):
        if is_bright and start is None:
            start = i
        elif not is_bright and start is not None:
            runs.append((start + i) // 2)
            start = None
    if start is not None:
        runs.append((start + len(bright)) // 2)
    merged = []
    for run in runs:
        if merged and run - merged[-1] < 20:
            merged[-1] = run
        else:
            merged.append(run)
    return len(merged)


def is_full_board(crop, *, strict: bool = False) -> bool:
    """True when the tight slot crop is a full 8x8 board.

    Two complementary signals, either of which identifies a board: the white
    gap lines between the solid-filled squares (>= MIN_GAP_LINES per axis —
    obscured by pieces on some boards), and the checkerboard correlation of
    the 8x8 cell means (>= BOARD_CORR_CUT — robust on gray-cast pages where
    the gaps don't read as white). Cover art and text fail both; a 5x5
    partial board's lines don't span a full axis and its cells don't
    correlate with an 8x8 split.

    The gap-line signal alone is not enough: white line-drawn boards (e.g.
    a 5x5 sub-board chapter's diagrams) also produce bright strips between
    their dark frame lines — and a misaligned crop of one can even pass the
    raw correlation. Solid-fill boards additionally have DARK squares
    forming the checkerboard pattern, so both branches additionally require
    the dark-cell mask to correlate with an 8x8 checkerboard: >= 0.40 for
    an aligned slot crop, >= 0.45 for a SHIFTED candidate (white 5x5 crops
    measure up to ~0.41 only when shifted — the stricter bar keeps them
    out while real solid-fill boards measure >= 0.55).
    """
    if crop is None or crop.size == 0 or min(crop.shape) < 60:
        return False
    side_y, side_x = crop.shape
    board_img = _warp(
        crop,
        np.array(
            [[0, 0], [side_x - 1, 0], [side_x - 1, side_y - 1], [0, side_y - 1]],
            dtype=np.float32,
        ),
    )
    means = np.array(
        [[float(p.mean()) for p in row] for row in split_board(board_img)]
    ).ravel()
    template = np.array(
        [1.0 if (i // 8 + i % 8) % 2 == 0 else -1.0 for i in range(64)]
    )
    vals = means - means.mean()
    denom = float(np.linalg.norm(vals)) * float(np.linalg.norm(template))
    # ABSOLUTE correlation: a board whose crop is phase-shifted by one square
    # correlates NEGATIVELY but just as strongly (e.g. -0.7 on shifted pages)
    corr = abs(float(vals @ template / denom) if denom else 0.0)
    # dark squares must form the checkerboard pattern, not just be numerous:
    # a white 5x5 sub-board's frame lines + captions are dark but don't
    # alternate as solid-fill squares do. This holds for BOTH branches —
    # real solid-fill boards measure >= 0.55 (steps book data), 5x5 white
    # boards <= ~0.36, so 0.40 separates them while still admitting
    # piece-heavy solid boards (horsehead partial pages measure ~0.40-0.55).
    dark_mask = (means < DARK_SQUARE_CUT).astype(float)
    dark_vals = dark_mask - dark_mask.mean()
    dark_denom = float(np.linalg.norm(dark_vals)) * float(np.linalg.norm(template))
    dark_corr = abs(float(dark_vals @ template / dark_denom) if dark_denom else 0.0)
    by_lines = (
        _gap_line_count(crop.min(axis=0)) >= MIN_GAP_LINES
        and _gap_line_count(crop.min(axis=1)) >= MIN_GAP_LINES
    )
    # no piece-presence check here: boards with light/small pieces measure
    # above the ink cut — the OCR's piece-count check downstream drops the
    # genuinely empty ones
    dark_cut = 0.45 if strict else 0.40
    return (corr >= BOARD_CORR_CUT or by_lines) and dark_corr >= dark_cut


def _result(slot: Slot, profile: dict, fen: str, legal: bool, notes: list,
            page_number: int | None, **extra) -> ExtractionResult:
    number_format = profile.get("numbering", {}).get(
        "format", "p{page:03d}-{slot:02d}"
    )
    # the PDF page printed as "page 1" — steps books have variable front matter
    first_page = int(profile.get("first_page") or 1)
    page = max(0, (page_number or 0) - first_page + 1)
    return ExtractionResult(
        fen=fen,
        legal=legal,
        bbox=list(slot.bbox),
        number=number_format.format(page=page, slot=slot.index),
        slot_index=slot.index,
        notes=notes,
        **extra,
    )


def _printed_number(gray: np.ndarray, bbox, profile: dict,
                    band: dict | None = None) -> str | None:
    """The printed number above a slot's top-left corner (mode "printed").

    None when the profile carries no digit templates or the band is
    unreadable — the caller then leaves the number unset (never guessed).
    """
    from books.cv.number_ocr import load_templates, read_number

    templates = load_templates(profile)
    if templates is None:
        return None
    return read_number(gray, bbox, templates,
                       band=band if band is not None else profile.get("number_band"))


def _ocr_slot(img, slot: Slot, profile: dict,
              page_number: int | None) -> ExtractionResult | None:
    from books import ocr

    margin = int(profile.get("steps", {}).get("crop_margin", 30))
    fen = ocr.batch_fens([ocr.crop_slot(img, slot.bbox, margin=margin)])[0]
    if not fen:
        return None
    try:
        piece_count = len(chess.Board(fen.split(" ")[0]).piece_map())
    except ValueError:
        piece_count = -1
    if piece_count <= 0:
        logger.info("steps slot %02d: OCR found no pieces", slot.index)
        return None
    _fen, legal, reasons = fen_utils.validate_fen(fen)
    return _result(
        slot, profile, fen, legal, reasons, page_number,
        confidence=0.9 if legal else 0.3, cells=[], mean_corr=1.0,
        unknown_count=0,
    )


def _cv_slot(gray, slot: Slot, profile: dict,
             page_number: int | None) -> ExtractionResult | None:
    empty_max_ink = profile.get("empty", {}).get("max_ink", 0.04)
    baseline = profile.get("style", {}).get("baseline", False)
    x, y, w, h = slot.bbox
    tight = gray[y:y + h, x:x + w]
    board_img = _warp(
        tight,
        np.array(
            [[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]], dtype=np.float32
        ),
    )
    lines = warped_grid_lines(board_img)
    if lines is not None:
        vx, hy = lines
        board = erase_grid_lines(board_img, vx, hy) if baseline else board_img
        patches = split_board(board, vx=vx, hy=hy, baseline=baseline)
    else:
        patches = split_board(board_img, baseline=baseline)
    flat_patches = [p for rank in patches for p in rank]  # rank 8 first
    cells, corrs, unknown = [], [], 0
    for square_index, patch in enumerate(flat_patches):
        glyph = extract_glyph(patch, empty_max_ink=empty_max_ink)
        code, corr = recognize_glyph(glyph, profile, square_index)
        cells.append(code)
        corrs.append(corr)
        if code == "?":
            unknown += 1
    if not any(code for code in cells if code is not None and code != "?"):
        logger.info("steps slot %02d: CV found no pieces", slot.index)
        return None
    matrix = [[cells[r * 8 + c] for c in range(8)] for r in range(8)]
    fen, legal, reasons = fen_utils.build_fen(matrix)
    mean_corr = sum(corrs) / len(corrs) if corrs else 0.0
    confidence = 0.40 * legal + 0.35 * mean_corr + 0.25 * (1 - unknown / 64)
    return _result(
        slot, profile, fen, legal, reasons, page_number,
        confidence=round(confidence, 3), cells=cells,
        mean_corr=round(mean_corr, 3), unknown_count=unknown,
    )


def extract_steps_page(img, profile: dict,
                       page_number: int | None = None) -> list[ExtractionResult]:
    """Extract a steps page: fixed slot grid, per-slot board gate, then OCR
    (or CV) recognition. Non-board slots (drawings, text, 5x5 partials) are
    skipped entirely — their numbers are never renumbered.

    `profile["ocr_only_pages"]` maps page numbers to per-page slot layouts
    whose boards defeat the gate (piece-heavy partial pages with grids that
    don't match the stored layout) — those slots are OCR'd directly, no gate.

    `profile["steps"]["board_style"] == "line"` switches to the woodpecker
    path (extract_line_page): per-page frame detection instead of the solid
    fill gate, with printed-number and turn-marker reading.
    """
    steps_cfg = profile.get("steps", {})
    if steps_cfg.get("board_style") == "line":
        return extract_line_page(img, profile, page_number=page_number)
    prefer = steps_cfg.get("prefer", "ocr")
    gray = gray_view(img)
    ocr_only = (profile.get("ocr_only_pages") or {}).get(str(page_number))
    if ocr_only is not None:
        results = []
        for slot in steps_slots(gray, ocr_only):
            result = _ocr_slot(img, slot, profile, page_number)
            if result is not None:
                results.append(result)
        if profile.get("numbering", {}).get("mode") == "printed":
            for result in results:
                result.number = _printed_number(gray, result.bbox, profile)
        return results
    # pages shift their grids slightly (and some have fewer/shorter rows) —
    # detect the bands per page, fall back to the stored layout
    col_bands, row_bands = steps_bands(
        gray, threshold=int(steps_cfg.get("threshold", DEFAULT_THRESHOLD))
    )
    if _plausible_bands(col_bands, row_bands, page_shape=gray.shape):
        col_bands = _expand_short_bands(col_bands, gray.shape[1])
        row_bands = _expand_short_bands(row_bands, gray.shape[0])
    else:
        # fall back to the stored layout, expressed as pixel bands so the
        # region-OCR fallback below has valid extents
        logger.info(
            "steps bands not plausible (%d cols x %d rows) — using stored layout",
            len(col_bands), len(row_bands),
        )
        height, width = gray.shape
        col_bands = [[int(f0 * width), int(f1 * width)]
                     for f0, f1 in profile["layout"]["columns"]]
        row_bands = [[int(f0 * height), int(f1 * height)]
                     for f0, f1 in profile["layout"]["rows"]]
    slots = steps_slots_from_bands(col_bands, row_bands)
    results = []
    # the region-OCR fallback may only run on pages that demonstrably HAVE
    # boards (text pages must stay empty) — decided once, up front
    def _slot_has_board(slot):
        x, y, w, h = slot.bbox
        tight = gray[y:y + h, x:x + w]
        if tight.size == 0 or min(tight.shape) < 60:
            return False
        return is_full_board(tight) or _shifted_board_slot(gray, slot) is not None

    # region OCR may only run on pages that demonstrably HAVE at least one
    # board: text pages and 5x5-sub-board chapters (which fail the gate
    # everywhere) must yield nothing at all. Partial puzzle pages (few
    # gated slots) recover their remaining boards through the region fallback.
    page_has_boards = sum(1 for s in slots if _slot_has_board(s)) >= 1
    for slot in slots:
        slot_index = slot.index
        x, y, w, h = slot.bbox
        tight = gray[y:y + h, x:x + w]
        if not is_full_board(tight):
            # bands can sit slightly off the boards — retry with shifted crops
            slot = _shifted_board_slot(gray, slot)
        if slot is None:
            # last resort: let the OCR localize the board inside the whole
            # band region (some pages' boards sit far off the detected bands).
            # Only on board pages, and only slots with actual ink (empty
            # areas can never hide a board).
            if prefer == "ocr" and page_has_boards and float(tight.mean()) < 248:
                region = _band_region(col_bands, row_bands, slot_index, gray)
                if region is not None:
                    result = _ocr_region(img, region, slot_index, profile,
                                         page_number)
                    if result is not None:
                        results.append(result)
                    else:
                        logger.info("steps slot %02d: no board found, skipped",
                                    slot_index)
            continue
        result = None
        if prefer == "ocr":
            result = _ocr_slot(img, slot, profile, page_number)
        if result is None and profile.get("templates"):
            result = _cv_slot(gray, slot, profile, page_number)
        if result is not None:
            results.append(result)
    if profile.get("numbering", {}).get("mode") == "printed":
        # the book's OWN printed number is authoritative — the positional
        # guess must never leak into the DB. Unreadable bands stay unset.
        for result in results:
            result.number = _printed_number(gray, result.bbox, profile)
    # NOTE: no line-drawn "white board" fallback here — in horsehead the
    # white-board pages are the 5x5 (d4-h8) sub-board chapters, which must
    # be skipped entirely (their grids don't pass the 8x8 gate).
    return results


def _band_region(col_bands, row_bands, slot_index: int, gray) -> tuple | None:
    """The whole band region around a slot (expanded to neighbor midpoints)."""
    if not col_bands or not row_bands:
        return None
    col = (slot_index - 1) % len(col_bands)
    row = (slot_index - 1) // len(col_bands)
    if row >= len(row_bands):
        return None
    x0, x1 = col_bands[col]
    y0, y1 = row_bands[row]
    left = (col_bands[col - 1][0] + col_bands[col - 1][1]) / 2 if col > 0 else 0
    right = (
        (col_bands[col + 1][0] + col_bands[col + 1][1]) / 2
        if col + 1 < len(col_bands) else gray.shape[1]
    )
    top = (row_bands[row - 1][0] + row_bands[row - 1][1]) / 2 if row > 0 else 0
    bottom = (
        (row_bands[row + 1][0] + row_bands[row + 1][1]) / 2
        if row + 1 < len(row_bands) else gray.shape[0]
    )
    return int(left), int(top), int(right - left), int(bottom - top)


def _ocr_region(img, region, slot_index: int, profile: dict,
                page_number: int | None) -> ExtractionResult | None:
    """OCR a generous region and keep whatever board the model finds in it."""
    from books import ocr

    fen = ocr.batch_fens([ocr.crop_slot(img, region, margin=0)])[0]
    if not fen:
        return None
    try:
        piece_count = len(chess.Board(fen.split(" ")[0]).piece_map())
    except ValueError:
        piece_count = -1
    if piece_count <= 0:
        logger.info("steps slot %02d: region OCR found no board", slot_index)
        return None
    _fen, legal, reasons = fen_utils.validate_fen(fen)
    number_format = profile.get("numbering", {}).get(
        "format", "p{page:03d}-{slot:02d}"
    )
    first_page = int(profile.get("first_page") or 1)
    page = max(0, (page_number or 0) - first_page + 1)
    return ExtractionResult(
        fen=fen, legal=legal,
        confidence=0.9 if legal else 0.3,
        bbox=list(region), cells=[], mean_corr=1.0,
        unknown_count=0, notes=reasons,
        number=number_format.format(page=page, slot=slot_index),
        slot_index=slot_index,
    )


def _shifted_board_slot(gray, slot: Slot) -> Slot | None:
    """Reject, or relocate the slot if a shifted crop contains the board.

    Shifted candidates must pass the STRICTER gate: a white 5x5 sub-board's
    shifted crop can squeeze past the aligned bar (dark corr ~0.41) but not
    the shifted one.
    """
    x, y, w, h = slot.bbox
    if is_full_board(gray[y:y + h, x:x + w]):
        return slot
    logger.info("steps slot %02d: no 8x8 board at band crop, searching shifts",
                slot.index)
    for dx in (-w // 4, w // 4, -w // 8, w // 8):
        for dy in (-h // 4, h // 4, -h // 8, h // 8):
            nx, ny = x + dx, y + dy
            if nx < 0 or ny < 0 or nx + w > gray.shape[1] or ny + h > gray.shape[0]:
                continue
            if is_full_board(gray[ny:ny + h, nx:nx + w], strict=True):
                return Slot(bbox=(nx, ny, w, h), index=slot.index)
    return None


# --------------------------------------------------------------------------
# Line-drawn boards (woodpecker style): white squares, thin/faint inner
# grid, but a SOLID BLACK FRAME around each board. Left/right pages drift
# (mirrored binding margins), so the slot grid is found per page from the
# full-extent solid frame lines — the only structures whose ink spans an
# entire page column/row at >= ~80% density.
# --------------------------------------------------------------------------

COLUMN_FRAC_CUT = 0.20  # page-column dark fraction: board frames stack
# vertically (3 rows => ~0.65 of the page), so even a single board's frame
# (~0.22) clears this; tall solid artwork edges (~0.42) pair with nothing
MIN_FRAME_RUN = 350  # a frame is a solid dark run of ~406px at 200dpi;
# caption/header strokes measure <= 40px and are rejected by run length
ROW_FRAC_CUT = 0.8  # within a column strip a frame row spans its full width
FRAME_MERGE = 6  # px — a frame prints as 2-3 adjacent dark columns
FRAME_MAX_WIDTH = 14  # px at 200dpi — frame lines are thin
FRAME_MIN_GAP = 250  # px between a board's two frame lines (board ~410px)
FRAME_MAX_GAP = 620
MIN_BOARD_SIDE = 200  # px — boards are ~410px at 200dpi

MARKER_INK_CUT = 150  # gray threshold for triangle/board ink
MARKER_MIN_AREA = 60  # px — triangles are ~500-2000px of ink
MARKER_EDGE_FRACTION = 0.4  # marker hugs the board's right frame
MARKER_TOP_FRACTION = 0.45  # above this = upper corner (black)
MARKER_BOTTOM_FRACTION = 0.55  # below this = lower corner (white)


def _solid_lines(profile, cut, *, max_width=FRAME_MAX_WIDTH, merge=FRAME_MERGE):
    """Center positions of solid full-extent lines in a 1-D dark-fraction
    profile: runs above `cut` (merged within `merge` px, thin enough to be
    a line rather than a filled block)."""
    runs = []
    start = None
    for i, v in enumerate(profile >= cut):
        if v and start is None:
            start = i
        elif not v and start is not None:
            runs.append((start, i))
            start = None
    if start is not None:
        runs.append((start, len(profile)))
    merged = []
    for s, e in runs:
        if merged and s - merged[-1][1] <= merge:
            merged[-1] = (merged[-1][0], e)
        else:
            merged.append((s, e))
    return [(s + e) // 2 for s, e in merged if e - s <= max_width]


def line_frames(gray) -> tuple[list[int], list[int]]:
    """Column frame line positions from the full-page profile.

    A candidate column clears the dark-fraction floor AND carries a solid
    dark run of board-frame length along y (caption/header strokes measure
    <= 40px). Adjacent frame columns merge into one center position. Rows
    are found per column strip in line_slots — a frame row spans only its
    own column, so the page-wide row profile cannot see it.
    """
    dark = gray < MARKER_INK_CUT
    frac = dark.mean(axis=0)
    positions = []
    for x in np.where(frac >= COLUMN_FRAC_CUT)[0]:
        band = dark[:, max(0, x - 1):x + 2].any(axis=1)
        best = cur = 0
        for v in band:
            cur = cur + 1 if v else 0
            best = max(best, cur)
        if best >= MIN_FRAME_RUN:
            positions.append(int(x))
    merged = []
    for p in positions:
        if merged and p - merged[-1][-1] <= FRAME_MERGE:
            merged[-1].append(p)
        else:
            merged.append([p])
    return [sum(group) // len(group) for group in merged], []


def line_slots(gray) -> tuple[list[list[int]], list[list[int]], list[Slot]]:
    """(col_bands, row_bands, slots) from solid frame pairs on the page.

    Columns come from the full-page profile (frames stack vertically); rows
    from the FIRST column strip's profile (a frame row spans the strip's
    full width, captions only ~35% of it). Consecutive frame lines whose
    separation matches a board size form a band; partial pages simply yield
    fewer bands, and stray rules/text never pair up — divider pages yield
    nothing.
    """
    col_lines, _ = line_frames(gray)
    cols = [[a, b] for a, b in zip(col_lines, col_lines[1:])
            if FRAME_MIN_GAP <= b - a <= FRAME_MAX_GAP]
    rows = []
    if cols:
        dark = gray < MARKER_INK_CUT
        x0, x1 = cols[0]
        strip_frac = dark[:, max(0, x0 - 2):x1 + 2].mean(axis=1)
        row_lines = _solid_lines(strip_frac, ROW_FRAC_CUT)
        rows = [[a, b] for a, b in zip(row_lines, row_lines[1:])
                if FRAME_MIN_GAP <= b - a <= FRAME_MAX_GAP]
    slots = []
    for row, (y0, y1) in enumerate(rows):
        for col, (x0, x1) in enumerate(cols):
            if x1 - x0 < MIN_BOARD_SIDE or y1 - y0 < MIN_BOARD_SIDE:
                continue
            # pad past both frame lines (the pair centers can sit a pixel
            # inside the frame, cropping it out of the bbox)
            slots.append(
                Slot(
                    bbox=(x0 - 2, y0 - 2, x1 - x0 + 5, y1 - y0 + 5),
                    index=1 + row * len(cols) + col,
                )
            )
    return cols, rows, slots


def is_line_board(crop) -> bool:
    """True when the slot crop is a woodpecker-style line-drawn board:
    frame lines at the crop's edges, white squares inside."""
    if crop is None or crop.size == 0 or min(crop.shape) < 60:
        return False
    ink = (crop < MARKER_INK_CUT).mean()
    if not (0.02 <= ink <= 0.5):  # white board with pieces; not cover art
        return False
    dark = crop < MARKER_INK_CUT
    col_frac = dark.mean(axis=0)
    row_frac = dark.mean(axis=1)
    H, W = crop.shape

    def edge_ok(profile, side):
        solid = _solid_lines(profile, ROW_FRAC_CUT)
        # solid frame line near the edge, or a faint one (dotted-frame
        # print artifacts measure ~0.08-0.12 at the edge columns)
        if side == "start":
            return any(p <= 8 for p in solid) or bool((profile[:5] >= 0.04).any())
        return any(len(profile) - p <= 8 for p in solid) or bool(
            (profile[-5:] >= 0.04).any()
        )

    return (
        edge_ok(col_frac, "start") and edge_ok(col_frac, "end")
        and edge_ok(row_frac, "start") and edge_ok(row_frac, "end")
    )


def detect_turn_marker(gray, slot_bbox, right_edge: int | None = None) -> str | None:
    """'b'/'w' from the turn triangle in the gap right of the board.

    The marker hugs the board's RIGHT frame: a FILLED downward triangle at
    the upper corner means black to move, an OUTLINED upward triangle at the
    lower corner means white. Other gap content (the next column's printed
    number) sits away from the frame and is ignored. None when no marker
    is found.
    """
    import cv2

    x, y, w, h = slot_bbox
    if right_edge is None:
        right_edge = gray.shape[1]
    x1 = min(right_edge, gray.shape[1])
    gap = gray[y:y + h, x + w:x1]
    if gap.size == 0 or gap.shape[1] < 30:
        return None
    binary = (gap < MARKER_INK_CUT).astype(np.uint8)
    count, labels = cv2.connectedComponents(binary)
    for label in range(1, count):
        ys, xs = np.where(labels == label)
        if len(ys) < MARKER_MIN_AREA:
            continue
        if xs.min() > MARKER_EDGE_FRACTION * gap.shape[1]:
            continue  # not adjacent to the board — the neighbour's number
        y_center = (ys.min() + ys.max()) / 2 / gap.shape[0]
        if y_center < MARKER_TOP_FRACTION:
            return "b"
        if y_center > MARKER_BOTTOM_FRACTION:
            return "w"
    return None


def apply_turn(fen: str, turn: str) -> str:
    """Rewrite the FEN's side-to-move field."""
    parts = fen.split(" ")
    if len(parts) < 2:
        return fen
    parts[1] = turn
    return " ".join(parts)


def extract_line_page(img, profile: dict,
                      page_number: int | None = None) -> list[ExtractionResult]:
    """Woodpecker-style pages: per-page frame-detected slots -> OCR.

    Numbering mode "printed": the book's own number is read from the gap
    left of the board; the turn marker (triangle) in the right gap decides
    the FEN's side to move. Empty/text pages have no frame pairs and yield
    nothing.
    """
    from books import ocr

    gray = gray_view(img)
    cols, rows, slots = line_slots(gray)
    if not slots:
        return []
    results = []
    for slot in slots:
        x, y, w, h = slot.bbox
        tight = gray[y:y + h, x:x + w]
        if not is_line_board(tight):
            logger.info("line slot %02d: no board in frame box, skipped", slot.index)
            continue
        result = _ocr_slot(img, slot, profile, page_number)
        if result is None:
            continue
        # the printed number lives in the LEFT gap (band geometry in the
        # profile); the turn marker in the RIGHT gap
        if profile.get("numbering", {}).get("mode") == "printed":
            result.number = _printed_number(gray, result.bbox, profile)
        if profile.get("steps", {}).get("turn_markers"):
            # slot.index is 1-based row-major — recover the column
            col_index = (slot.index - 1) % len(cols)
            right_edge = (
                cols[col_index + 1][0] if col_index + 1 < len(cols)
                else gray.shape[1]
            )
            turn = detect_turn_marker(gray, slot.bbox, right_edge=right_edge)
            if turn is not None:
                result.fen = apply_turn(result.fen, turn)
            else:
                result.notes = list(result.notes) + ["turn marker missing"]
        results.append(result)
    return results
