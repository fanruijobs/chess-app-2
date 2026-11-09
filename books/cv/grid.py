"""Chessboard detection on book pages.

Primary: contour blobs propose regions, cv2.findChessboardCornersSB finds the
7x7 inner corners inside each region, and the board quad is extrapolated one
square outward. Fallback: quadrilateral contours (bordered diagrams) and
HoughLinesP axis-aligned rectangles (borderless diagrams).
"""

import logging
from dataclasses import dataclass

import cv2
import numpy as np

logger = logging.getLogger("books.cv")

BOARD_SIZE = 640  # warped board resolution (square)
MIN_REGION_AREA_FRACTION = 0.01  # of the page area


@dataclass
class Diagram:
    quad: np.ndarray  # 4x2 float32 in ORIGINAL page px (TL, TR, BR, BL)
    bbox: tuple[int, int, int, int]  # x, y, w, h in original px
    board_img: np.ndarray  # 640x640 grayscale warped board

    @property
    def center(self) -> tuple[float, float]:
        x, y, w, h = self.bbox
        return x + w / 2, y + h / 2


def _warp(gray: np.ndarray, quad: np.ndarray) -> np.ndarray:
    dst = np.array(
        [[0, 0], [BOARD_SIZE - 1, 0], [BOARD_SIZE - 1, BOARD_SIZE - 1], [0, BOARD_SIZE - 1]],
        dtype=np.float32,
    )
    matrix = cv2.getPerspectiveTransform(quad.astype(np.float32), dst)
    return cv2.warpPerspective(gray, matrix, (BOARD_SIZE, BOARD_SIZE))


def _propose_regions(binary: np.ndarray, img_area: int, scale: float) -> list[tuple[int, int, int, int]]:
    """Candidate board regions via ink-density mapping (original-px coords).

    A chessboard has ~50% ink density uniformly across its area (dark squares
    and pieces), while text is sparse. Downsample the binary into blocks,
    keep dense blocks, and take their connected components as regions. This is
    robust to the dark-square network fragmenting around pieces.
    """
    block = 30
    height, width = binary.shape
    grid_h, grid_w = height // block + 1, width // block + 1
    dense = np.zeros((grid_h, grid_w), dtype=np.uint8)
    for by in range(grid_h):
        for bx in range(grid_w):
            cell = binary[by * block:(by + 1) * block, bx * block:(bx + 1) * block]
            if cell.size and cell.mean() / 255.0 > 0.25:
                dense[by, bx] = 1
    dense = cv2.morphologyEx(dense, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))

    count, labels = cv2.connectedComponents(dense)
    rects = []
    for label in range(1, count):
        ys, xs = np.where(labels == label)
        x0, y0 = xs.min() * block, ys.min() * block
        x1 = (xs.max() + 1) * block
        y1 = (ys.max() + 1) * block
        if (x1 - x0) * (y1 - y0) < MIN_REGION_AREA_FRACTION * img_area * scale * scale:
            continue
        rects.append([x0 / scale, y0 / scale, (x1 - x0) / scale, (y1 - y0) / scale])

    # merge overlapping rectangles (greedy)
    merged: list[list[float]] = []
    for rect in sorted(rects, key=lambda r: -r[2] * r[3]):
        placed = False
        for m in merged:
            ix = max(0, min(m[0] + m[2], rect[0] + rect[2]) - max(m[0], rect[0]))
            iy = max(0, min(m[1] + m[3], rect[1] + rect[3]) - max(m[1], rect[1]))
            if ix * iy > 0.3 * rect[2] * rect[3]:
                x0 = min(m[0], rect[0]); y0 = min(m[1], rect[1])
                m[0], m[1] = x0, y0
                m[2] = max(m[0] + m[2], rect[0] + rect[2]) - x0
                m[3] = max(m[1] + m[3], rect[1] + rect[3]) - y0
                placed = True
                break
        if not placed:
            merged.append(list(rect))
    return [(int(x), int(y), int(w), int(h)) for x, y, w, h in merged]


def _strongest_peaks(signal: np.ndarray, count: int, min_gap: int) -> list[int]:
    """Indices of the `count` strongest peaks separated by >= min_gap."""
    chosen = []
    for index in np.argsort(signal)[::-1]:
        if all(abs(index - c) >= min_gap for c in chosen):
            chosen.append(int(index))
            if len(chosen) == count:
                break
    return sorted(chosen)


def _fit_grid_lines(candidates: list[int], height: int, span_min: int = 200) -> list[int] | None:
    """Best 9-line fit with near-uniform spacing (8 equal intervals).

    Piece silhouettes can outrank true grid lines among the strongest peaks;
    the real grid is the subset that divides its own span into 8 equal parts.
    Boundary candidates are restricted to the outer bands (the board fills
    its density region), which keeps piece-row edges from masquerading as
    boundaries.
    """
    candidates = sorted(set(candidates))
    tops = [c for c in candidates if c < 0.18 * height]
    bottoms = [c for c in candidates if c > 0.82 * height]
    best = None
    for top in tops:
        for bottom in bottoms:
            span = bottom - top
            if span < span_min:
                continue
            step = span / 8.0
            lines = [top]
            cost = 0.0
            for k in range(1, 8):
                target = top + k * step
                nearest = min(candidates, key=lambda c: abs(c - target))
                distance = abs(nearest - target)
                if distance > step * 0.30:
                    break
                lines.append(nearest)
                cost += distance
            if len(lines) == 8:
                lines.append(bottom)
                if best is None or cost < best[1]:
                    best = (lines, cost)
    return best[0] if best else None


def _grid_lines_quad(patch: np.ndarray, offset: tuple[float, float]) -> np.ndarray | None:
    """Board quad from the square grid lines (color-contrast peaks per axis).

    At a true square boundary every line of the board flips light<->dark, so
    the per-column contrast summed over all columns peaks there; piece edges
    flip only in the columns a piece occupies.
    """
    if patch.size == 0:
        return None
    col_signal = _boundary_contrast_signal(patch, axis=1)  # peaks at vertical lines
    row_signal = _boundary_contrast_signal(patch, axis=0)  # peaks at horizontal lines

    min_gap = max(10, patch.shape[0] // 20)
    ph, pw = patch.shape
    # the density region is a tight board proxy: its edges anchor the fit
    col_candidates = _strongest_peaks(col_signal, 20, min_gap) + [0, pw - 1]
    row_candidates = _strongest_peaks(row_signal, 20, min_gap) + [0, ph - 1]
    vx = _fit_grid_lines(col_candidates, pw)
    hy = _fit_grid_lines(row_candidates, ph)
    if vx is None or hy is None:
        return None

    x0, y0 = offset
    return np.array(
        [
            [x0 + vx[0], y0 + hy[0]],
            [x0 + vx[-1], y0 + hy[0]],
            [x0 + vx[-1], y0 + hy[-1]],
            [x0 + vx[0], y0 + hy[-1]],
        ],
        dtype=np.float32,
    )


def _detect_in_region(gray: np.ndarray, region: tuple[int, int, int, int]) -> np.ndarray | None:
    """Grid-line detection inside a density region -> board quad (original px)."""
    x, y, w, h = region
    pad = int(max(w, h) * 0.05)
    x0, y0 = max(0, x - pad), max(0, y - pad)
    x1, y1 = min(gray.shape[1], x + w + pad), min(gray.shape[0], y + h + pad)
    patch = gray[y0:y1, x0:x1]
    if patch.size == 0:
        return None
    return _grid_lines_quad(patch, (float(x0), float(y0)))


def _phase_search_axis(signal: np.ndarray, spacing_center: float) -> tuple[float, float] | None:
    """Best (spacing, phase) for 9 near-uniform boundary lines.

    The initial warp may compress one axis slightly, so both the spacing
    (within +-5% of the reference) and the phase are searched; the pair whose
    positions carry the most line signal wins. Piece edges sit a few px off
    the true lines and span less than the full board, so the true grid wins.
    """
    best, best_score = None, -1.0
    # wide spacing range: frame padding in the initial quad can compress the
    # board to ~60% of the reference spacing inside the warp
    for spacing in np.arange(spacing_center * 0.6, spacing_center * 1.4, 0.5):
        for phase in range(int(spacing)):
            positions = [int(round(phase + k * spacing)) for k in range(9)]
            if positions[-1] >= len(signal) + spacing:
                continue
            # the board's last line can be cropped by the warp: clamp it to
            # the image edge instead of rejecting the whole phase
            positions = [min(p, len(signal) - 1) for p in positions]
            score = sum(float(signal[p]) for p in positions)
            if score > best_score:
                best, best_score = (spacing, phase), score
    return best


def _boundary_contrast_signal(board_img: np.ndarray, axis: int = 0) -> np.ndarray:
    """Color-contrast across candidate boundaries along one axis.

    At a true square boundary every line of the board flips light<->dark, so
    the per-column intensity contrast summed over all columns peaks there;
    piece edges flip only in the columns a piece occupies. Much more
    discriminative than raw gradients.
    """
    box = 10
    if axis == 1:
        board_img = board_img.T
    smooth = cv2.boxFilter(
        board_img.astype(np.float32), -1, (1, box * 2 + 1), normalize=True
    )
    contrast = np.abs(smooth[box * 2:] - smooth[:-box * 2]).sum(axis=1)
    signal = np.zeros(board_img.shape[0])
    signal[box:-box] = contrast
    return signal


def _uniform(lines: list[int], tolerance: float = 0.15) -> bool:
    """True when all consecutive diffs are within tolerance of the median."""
    diffs = np.diff(lines)
    median = float(np.median(diffs))
    return bool(np.all(np.abs(diffs - median) <= tolerance * median))


def _ink_fraction_signal(binary: np.ndarray, axis: int = 0) -> np.ndarray:
    """Fraction of the perpendicular extent covered by ink per position.

    Grid lines are dotted across ~60%+ of the board; piece rows/columns
    cover far less — this separates grid lines from piece rows where plain
    contrast cannot.
    """
    ink = binary > 0
    if axis == 1:
        ink = ink.T
    signal = np.zeros(ink.shape[0])
    for y in range(ink.shape[0]):
        band = ink[max(0, y - 3):y + 3, :].sum(axis=0) > 0
        signal[y] = band.mean()
    return signal


def erase_grid_lines(board_img: np.ndarray, vx: list[int], hy: list[int],
                     half_width: int = 12) -> np.ndarray:
    """Erase the grid lines (dotted or solid) from a warped board.

    Pieces are extracted from the CLEAN image, so line dots never leak into
    the square patches. Baseline-aligned pieces lose their bottom few pixels
    (they sit on the line) — acceptable; the piece stays recognizable.
    """
    clean = board_img.copy()
    for y in hy:
        clean[max(0, y - half_width):y + half_width, :] = 255
    for x in vx:
        clean[:, max(0, x - half_width):x + half_width] = 255
    return clean


def warped_grid_lines(board_img: np.ndarray) -> tuple[list[int], list[int]] | None:
    """Square grid lines inside an already-warped 640x640 board.

    The initial warp is only approximately aligned; the true square boundaries
    are re-detected here on an ink-fraction signal (grid lines cover most of
    the board's width, piece rows do not), so squares can be split along the
    real lines instead of equal division. When the re-fit is unreliable, each
    axis falls back to the equal split — the initial quad is usually already
    near-exact.
    """
    if board_img.shape[0] != board_img.shape[1]:
        return None
    side = board_img.shape[0]
    blurred = cv2.GaussianBlur(board_img, (5, 5), 0)
    binary = cv2.adaptiveThreshold(
        blurred, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV, blockSize=51, C=10,
    )
    col_signal = _ink_fraction_signal(binary, axis=1)
    row_signal = _ink_fraction_signal(binary, axis=0)
    min_gap = max(8, side // 20)
    vx = _fit_grid_lines(_strongest_peaks(col_signal, 20, min_gap) + [0, side - 1], side)
    if vx is None or not _uniform(vx):
        vx = [side * k // 8 for k in range(9)]

    spacing = (vx[-1] - vx[0]) / 8.0
    result = _phase_search_axis(row_signal, spacing) if spacing > 0 else None
    if result is None:
        hy = _fit_grid_lines(
            _strongest_peaks(row_signal, 20, min_gap) + [0, side - 1], side
        )
        if hy is None or not _uniform(hy):
            hy = [side * k // 8 for k in range(9)]
    else:
        found_spacing, phase = result
        hy = [min(side - 1, int(round(phase + k * found_spacing))) for k in range(9)]
        if not _uniform(hy):
            hy = [side * k // 8 for k in range(9)]
    return vx, hy


def _contour_quads(binary: np.ndarray, img_area: int, scale: float) -> list[np.ndarray]:
    """Fallback: quadrilateral contours (bordered diagrams), original px."""
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    quads = []
    for contour in contours:
        area = cv2.contourArea(contour)
        if area < MIN_REGION_AREA_FRACTION * img_area * scale * scale:
            continue
        peri = cv2.arcLength(contour, True)
        approx = cv2.approxPolyDP(contour, 0.02 * peri, True)
        if len(approx) != 4 or not cv2.isContourConvex(approx):
            continue
        points = approx.reshape(4, 2) / scale
        s = points.sum(axis=1)
        d = np.diff(points, axis=1).ravel()
        quad = np.array(
            [points[np.argmin(s)], points[np.argmin(d)], points[np.argmax(s)], points[np.argmax(d)]],
            dtype=np.float32,
        )
        quads.append(quad)
    return quads


def _axis_aligned_quad(binary: np.ndarray, scale: float) -> np.ndarray | None:
    """Fallback for borderless diagrams: largest axis-aligned ink rectangle."""
    edges = cv2.Canny(binary, 50, 150)
    lines = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=100,
                            minLineLength=binary.shape[1] // 5, maxLineGap=20)
    if lines is None:
        return None
    ys, xs = [], []
    for line in np.asarray(lines):
        line = line.reshape(-1)
        if line.size >= 4:
            x1, y1, x2, y2 = line[:4]
            if abs(x1 - x2) > abs(y1 - y2):  # horizontal-ish
                ys.extend([y1, y2])
            else:
                xs.extend([x1, x2])
    if len(xs) < 4 or len(ys) < 4:
        return None
    left, right = min(xs), max(xs)
    top, bottom = min(ys), max(ys)
    if right - left < 50 or bottom - top < 50:
        return None
    return np.array(
        [[left, top], [right, top], [right, bottom], [left, bottom]], dtype=np.float32
    ) / scale


def _peak_positions(signal: np.ndarray, min_gap: int = 30,
                    min_value: float = 0) -> list[int]:
    """Local maxima of a 1D signal, separated by >= min_gap."""
    peaks = []
    for index in np.argsort(signal)[::-1]:
        index = int(index)
        if signal[index] < min_value:
            continue
        if all(abs(index - p) >= min_gap for p in peaks):
            peaks.append(index)
    return sorted(peaks)


def _split_runs(positions: list[int], tolerance: float = 0.30) -> list[list[int]]:
    """Split positions into runs of near-uniform spacing."""
    if len(positions) < 2:
        return [positions] if positions else []
    diffs = np.diff(positions)
    runs = [[positions[0]]]
    for i, diff in enumerate(diffs):
        if runs[-1]:
            reference = np.mean(np.diff(runs[-1])) if len(runs[-1]) > 1 else diff
            if abs(diff - reference) <= tolerance * reference:
                runs[-1].append(positions[i + 1])
                continue
        runs.append([positions[i + 1]])
    return runs


def _order_diagrams(diagrams: list[Diagram]) -> list[Diagram]:
    """Reading order: rows by y-center proximity, then left to right."""
    if len(diagrams) <= 1:
        return diagrams
    diagrams.sort(key=lambda d: d.center[1])
    rows = []
    current = [diagrams[0]]
    for d in diagrams[1:]:
        if abs(d.center[1] - current[-1].center[1]) < 0.5 * (
            current[-1].bbox[3] + d.bbox[3]
        ) / 2:
            current.append(d)
        else:
            rows.append(current)
            current = [d]
    rows.append(current)
    return [d for row in rows for d in sorted(row, key=lambda d: d.center[0])]


def _board_lines(run: list[int], all_positions: list[int] | None = None) -> list[int]:
    """Normalize a line run to the 9 board grid lines.

    Book diagrams often carry a frame line above and below the grid at the
    same spacing (11 uniformly spaced lines); the central 9 are the board.
    Partial runs (weak scans lose the bottom grid lines where captions sit)
    are extended DOWNWARD by the median spacing — but never onto a detected
    line from another run (the caption), which would warp caption text into
    the board.
    """
    if len(run) == 11:
        return run[1:-1]
    if len(run) == 10:
        return run[1:]
    if len(run) < 9:
        spacing = float(np.median(np.diff(run)))
        extended = list(run)
        while len(extended) < 9:
            candidate = int(round(extended[-1] + spacing))
            if all_positions is not None and any(
                abs(candidate - other) <= 0.5 * spacing for other in all_positions
                if other > extended[-1]
            ):
                break  # the next grid line is covered by a caption line
            extended.append(candidate)
        return extended
    return run[:9]


def _line_ink_segments(binary: np.ndarray, y_line: int) -> list[tuple[int, int]]:
    """Ink x-ranges along one horizontal grid line.

    Grid lines on scans are often dotted/dashed, so near segments (gap <=
    20px) are merged into the line's continuous runs.
    """
    row = binary[max(0, y_line - 3):y_line + 3, :].sum(axis=0) > 3
    segments = []
    start = None
    for x, ink in enumerate(row):
        if ink and start is None:
            start = x
        elif not ink and start is not None:
            segments.append((start, x))
            start = None
    if start is not None:
        segments.append((start, len(row)))
    if len(segments) <= 1:
        return segments
    merged = [list(segments[0])]
    for seg_start, seg_end in segments[1:]:
        if seg_start - merged[-1][1] <= 20:
            merged[-1][1] = seg_end
        else:
            merged.append([seg_start, seg_end])
    return [(s, e) for s, e in merged]


def detect_line_diagrams(gray: np.ndarray) -> list[Diagram]:
    """Line-drawing style (white board, thin grid lines, pieces).

    Diagram rows are found globally (row-contrast peaks form near-uniform
    runs). The horizontal grid lines are the strongest signal, so each
    diagram's column span comes from the ink extents ALONG those lines
    (clustered by overlap) — robust to weak/fragmented vertical lines and
    anisotropic scans.
    """
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    binary = cv2.adaptiveThreshold(
        blurred, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV, blockSize=51, C=10,
    )
    row_sig = _boundary_contrast_signal(gray, axis=0)
    threshold = max(200, row_sig.max() * 0.25)
    ys = _peak_positions(row_sig, min_gap=20, min_value=threshold)
    # partial runs (6-8 lines) are accepted and extrapolated: pages vary in
    # puzzle count and weak scans lose grid lines near captions
    y_runs = [r for r in _split_runs(ys) if 6 <= len(r) <= 12]

    diagrams = []
    for y_run in y_runs:
        lines = _board_lines(y_run, all_positions=ys)
        spacing_y = float(np.mean(np.diff(lines)))

        # ink extents along every board line. Segments are first clustered
        # WITHIN a line (a gap > 60px separates diagram columns), then the
        # line-columns are clustered across lines by overlap; the diagram's
        # edges are the MEDIANS of its line-column edges (outlier segments on
        # a single line cannot shift the median).
        line_columns: list[list[list[int]]] = []  # per line: list of [left, right]
        for y_line in lines:
            segments = _line_ink_segments(binary, y_line)
            per_line = []
            for seg_start, seg_end in segments:
                if per_line and seg_start - per_line[-1][1] <= 60:
                    per_line[-1][1] = seg_end
                else:
                    per_line.append([seg_start, seg_end])
            line_columns.append(per_line)

        columns: list[dict] = []  # {"lefts": [...], "rights": [...]}
        for per_line in line_columns:
            for seg_start, seg_end in per_line:
                for column in columns:
                    if seg_start < column["rights"][-1] and seg_end > column["lefts"][-1]:
                        column["lefts"].append(seg_start)
                        column["rights"].append(seg_end)
                        break
                else:
                    columns.append({"lefts": [seg_start], "rights": [seg_end]})

        for column in columns:
            left = int(np.median(column["lefts"]))
            right = int(np.median(column["rights"]))
            if right - left < 5 * spacing_y:
                continue
            # a chess diagram is roughly SQUARE (its rows and columns share
            # the cell size): text blocks (tables of contents, indices) also
            # produce uniform row lines, so reject non-square candidates
            aspect = (right - left) / max(1, lines[-1] - lines[0])
            if not (0.75 <= aspect <= 1.35):
                continue
            quad = np.array(
                [
                    [left, lines[0]],
                    [right, lines[0]],
                    [right, lines[-1]],
                    [left, lines[-1]],
                ],
                dtype=np.float32,
            )
            x, y, w, h = cv2.boundingRect(quad)
            board_img = _warp(gray, quad)
            diagrams.append(Diagram(quad=quad, bbox=(int(x), int(y), int(w), int(h)),
                                    board_img=board_img))
    return diagrams


def detect_diagrams(img: np.ndarray) -> list[Diagram]:
    """Detect all chess diagrams on a page, in reading order.

    Returns Diagram objects whose bboxes are in ORIGINAL image pixels.
    """
    height, width = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    img_area = height * width

    # BOTH detection styles run: dark-square boards (density path) and
    # line-drawing scans (line path). Candidates are then validated: a true
    # diagram has a resolvable grid in the warp AND actual pieces inside its
    # cells (tables of contents also produce uniform line runs, but no pieces)
    line_candidates = detect_line_diagrams(gray)

    scale = 1.0
    if max(height, width) > 2000:
        scale = 2000 / max(height, width)
        small = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    else:
        small = gray

    blurred = cv2.GaussianBlur(small, (5, 5), 0)
    binary = cv2.adaptiveThreshold(
        blurred, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV, blockSize=51, C=10,
    )
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel, iterations=2)

    quads = []
    for region in _propose_regions(binary, img_area, scale):
        quad = _detect_in_region(small, region)
        if quad is None:
            # grid lines not found: fall back to the density region itself
            # (the checkerboard pattern makes it a tight board proxy)
            x, y, w, h = region
            quad = np.array(
                [[x, y], [x + w, y], [x + w, y + h], [x, y + h]], dtype=np.float32
            )
        quads.append(quad)

    if not quads:
        quads = _contour_quads(binary, img_area, scale)
    if not quads:
        fallback = _axis_aligned_quad(binary, scale)
        if fallback is not None:
            quads = [fallback]
    if not quads:
        logger.info("no board quads detected on page")
        return []

    diagrams = []
    for quad in quads:
        x, y, w, h = cv2.boundingRect(quad)
        board_img = _warp(gray, quad)
        diagrams.append(Diagram(quad=quad, bbox=(int(x), int(y), int(w), int(h)),
                                board_img=board_img))
    # density results first: when both paths find the same board (synthetic
    # boards have dark squares), the density quad is the exact one
    candidates = diagrams + line_candidates
    kept = _validated(candidates, gray)

    # layout inference: puzzle books repeat a fixed grid of diagrams per
    # page; when a band is missing (weak scan or captions over the grid
    # lines), its quad is synthesized from the detected bands' regular
    # spacing and the page's column extents
    if len(kept) >= 2:
        band_ys = sorted({d.center[1] for d in kept})
        if len(band_ys) >= 2:
            gaps = np.diff(band_ys)
            median_gap = float(np.median(gaps))
            if median_gap > 100 and all(abs(g - median_gap) <= 0.4 * median_gap for g in gaps):
                column_rects: list[list[int]] = []
                for d in kept:
                    x0, y0, w, h = d.bbox
                    rect = [x0, x0 + w]
                    for col in column_rects:
                        if abs(col[0] - x0) < 50:
                            break
                    else:
                        column_rects.append(rect)
                top = min(band_ys)
                if top - median_gap > 80:  # a band above is missing
                    target = top - median_gap
                    for x0, x1 in column_rects:
                        quad = np.array(
                            [[x0, target], [x1, target],
                             [x1, target + (kept[0].bbox[3])], [x0, target + kept[0].bbox[3]]],
                            dtype=np.float32,
                        )
                        x, y, w, h = cv2.boundingRect(quad)
                        kept.append(Diagram(
                            quad=quad, bbox=(int(x), int(y), int(w), int(h)),
                            board_img=_warp(gray, quad),
                        ))
    return _order_diagrams(_validated(kept, gray))


def _validated(candidates: list[Diagram], gray: np.ndarray) -> list[Diagram]:
    """Keep candidates that contain a real chessboard.

    A true diagram has a resolvable grid in the warp AND pieces inside its
    cells; uniform text rows (tables of contents, indices) fail the piece
    check, and spurious density regions fail the grid check.
    """
    from books.cv.classify import extract_glyph
    from books.cv.squares import split_board

    kept = []
    for diagram in candidates:
        lines = warped_grid_lines(diagram.board_img)
        if lines is None:
            continue
        vx, hy = lines
        flat = [p for rank in split_board(diagram.board_img, vx=vx, hy=hy) for p in rank]
        occupied = sum(1 for patch in flat if extract_glyph(patch) is not None)
        # a chess position has pieces in >= 2 cells; text tables have either
        # none or dotted leaders in nearly every cell (> 40)
        if 2 <= occupied <= 40:
            kept.append(diagram)

    # dedupe: the two detection paths can find the same board twice
    unique = []
    for diagram in kept:
        for other in unique:
            ix = max(0, min(diagram.bbox[0] + diagram.bbox[2], other.bbox[0] + other.bbox[2])
                     - max(diagram.bbox[0], other.bbox[0]))
            iy = max(0, min(diagram.bbox[1] + diagram.bbox[3], other.bbox[1] + other.bbox[3])
                     - max(diagram.bbox[1], other.bbox[1]))
            if ix * iy > 0.5 * diagram.bbox[2] * diagram.bbox[3]:
                break
        else:
            unique.append(diagram)
    return unique
