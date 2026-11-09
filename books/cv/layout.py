"""Book-specific layout: fixed diagram slots, calibrated from one good page.

Scanned puzzle books repeat the same page layout (columns x rows of diagram
slots) throughout; individual pages can be too weak for per-page detection,
but the slots measured once from a clean page apply to every page.
"""

import numpy as np

from books.cv.grid import Diagram, _warp


def infer_layout(diagrams: list[Diagram], page_shape=None) -> dict:
    """Cluster detected diagrams into a regular slot grid.

    Returns {"columns": [[x0, x1], ...], "rows": [[y0, y1], ...]} as FRACTIONS
    of the page size (dpi-independent), or None when the detected diagrams
    don't form a regular grid.
    """
    if len(diagrams) < 2:
        return None
    bboxes = [d.bbox for d in diagrams]
    # cluster column x0s (within 60px) and row y0s (within 60px)
    col_x0s = sorted({b[0] for b in bboxes})
    row_y0s = sorted({b[1] for b in bboxes})

    def cluster(values, tolerance=60):
        groups = []
        for v in values:
            for group in groups:
                if abs(v - group[0]) <= tolerance:
                    group.append(v)
                    break
            else:
                groups.append([v])
        return [int(np.median(g)) for g in groups]

    col_centers = cluster(col_x0s)
    row_centers = cluster(row_y0s)
    if len(col_centers) < 1 or len(row_centers) < 1:
        return None

    widths = [b[2] for b in bboxes]
    heights = [b[3] for b in bboxes]
    width = int(np.median(widths))
    height = int(np.median(heights))
    ph, pw = (page_shape or (1, 1))[:2]
    return {
        "columns": [[c / pw, (c + width) / pw] for c in col_centers],
        "rows": [[r / ph, (r + height) / ph] for r in row_centers],
    }


def layout_diagrams(gray, layout: dict, column_major: bool = False) -> list[Diagram]:
    """Diagram candidates from the stored slot layout (fractions of the page).

    Reading order is row-major by default; `column_major` numbers downward
    first, then rightward (Polgar 5334: the left column holds #1-3).
    """
    ph, pw = gray.shape[:2]
    slots = []
    for y0, y1 in layout["rows"]:
        for x0, x1 in layout["columns"]:
            slots.append((x0, y0, x1, y1))
    if column_major:
        n_cols = len(layout["columns"])
        slots = sorted(
            slots, key=lambda s: (s[0], s[1])
        )  # (column x, row y) -> down each column, then right
        # regroup: iterate columns, then rows within a column
        ordered = []
        for col_start in sorted({s[0] for s in slots}):
            for s in slots:
                if s[0] == col_start:
                    ordered.append(s)
        slots = ordered
    diagrams = []
    for x0, y0, x1, y1 in slots:
        px0, py0 = int(x0 * pw), int(y0 * ph)
        px1, py1 = int(x1 * pw), int(y1 * ph)
        quad = np.array(
            [[px0, py0], [px1, py0], [px1, py1], [px0, py1]], dtype=np.float32
        )
        diagrams.append(
            Diagram(
                quad=quad,
                bbox=(px0, py0, px1 - px0, py1 - py0),
                board_img=_warp(gray, quad),
            )
        )
    return diagrams
