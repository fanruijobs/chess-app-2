"""Synthetic page images: FEN -> PNG via chess.svg.board -> WeasyPrint -> PyMuPDF.

Validates pipeline MECHANICS (detection, split, cluster, match, FEN build) —
not real-book font robustness, which needs the photos in fixtures/pages/.
"""

import io

import chess
import chess.svg
import cv2
import numpy as np
import pymupdf
from weasyprint import HTML

from books.cv import build_profile_data, cluster_board

BOARD_HTML = (
    '<html><body style="margin:40px; background:white; font-family:serif">{}</body></html>'
)


def fen_to_page(fen: str, *, size: int = 640) -> np.ndarray:
    """Render one FEN as a page-like PNG (BGR ndarray, white background).

    No coordinate labels: they sit outside the board frame and get warped
    into the bottom rank by quad detection (book diagrams lack them too).
    """
    svg = chess.svg.board(chess.Board(fen), size=size, coordinates=False)
    pdf = HTML(string=BOARD_HTML.format(svg)).write_pdf()
    doc = pymupdf.open(stream=pdf, filetype="pdf")
    try:
        pix = doc[0].get_pixmap(dpi=150)
        png = pix.tobytes("png")
    finally:
        doc.close()
    buf = np.frombuffer(png, dtype=np.uint8)
    return cv2.imdecode(buf, cv2.IMREAD_COLOR)


def fen_to_multi_page(fens: list[str], *, size: int = 400) -> np.ndarray:
    """Render several boards side by side on one canvas (reading-order test).

    Boards are composited in numpy directly: WeasyPrint's float/flex layouts
    stack SVG blocks vertically, which defeats the side-by-side test.
    """
    pages = [fen_to_page(fen, size=size) for fen in fens]
    pad = 100
    height = max(p.shape[0] for p in pages) + 2 * pad
    width = sum(p.shape[1] for p in pages) + pad * (len(pages) + 1)
    canvas = np.full((height, width, 3), 255, dtype=np.uint8)
    x = pad
    for page in pages:
        y = (height - page.shape[0]) // 2
        canvas[y:y + page.shape[0], x:x + page.shape[1]] = page
        x += page.shape[1] + pad
    return canvas


def fen_to_solid_board(fen: str, *, size: int = 266) -> np.ndarray:
    """One FEN rendered as a SOLID-FILLED board (Steps style).

    Light squares white, dark squares light-gray (~214 at render), standard
    SVG pieces — the geometry of the real step-6 workbook pages.
    """
    svg = chess.svg.board(
        chess.Board(fen), size=size * 2, coordinates=False,
        colors={"square light": "#ffffff", "square dark": "#d7d7d7"},
    )
    pdf = HTML(string=BOARD_HTML.format(svg)).write_pdf()
    doc = pymupdf.open(stream=pdf, filetype="pdf")
    try:
        pix = doc[0].get_pixmap(dpi=150)
        png = pix.tobytes("png")
    finally:
        doc.close()
    buf = np.frombuffer(png, dtype=np.uint8)
    img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    ys, xs = np.where(gray < 250)
    img = img[ys.min():ys.max() + 1, xs.min():xs.max() + 1]  # board frame box
    img = cv2.resize(img, (size, size), interpolation=cv2.INTER_AREA)
    # the real book separates its solid-filled squares with thin WHITE lines;
    # overlay them so the gap-line board detector sees the same structure
    for k in range(1, 8):
        at = round(k * size / 8)
        img[:, at - 1:at + 1] = 255
        img[at - 1:at + 1, :] = 255
    return img


# grid geometry of the real book at 150 dpi (page 3): 3 columns x 4 rows
STEPS_COLS = [97, 435, 773]
STEPS_ROWS = [190, 531, 873, 1216]
STEPS_BOARD = 266
STEPS_PAGE = (1598, 1125)  # (height, width) of the rendered page at 150 dpi


def fen_to_steps_page(fens: list[str]) -> np.ndarray:
    """Compose up to 12 solid boards on a steps-style 3x4 grid (white canvas)."""
    height, width = STEPS_PAGE
    canvas = np.full((height, width, 3), 255, dtype=np.uint8)
    for i, fen in enumerate(fens):
        row, col = divmod(i, 3)
        if row >= 4:
            raise ValueError("max 12 boards per steps page")
        board = fen_to_solid_board(fen, size=STEPS_BOARD)
        x, y = STEPS_COLS[col], STEPS_ROWS[row]
        canvas[y:y + board.shape[0], x:x + board.shape[1]] = board
    return canvas


def build_solid_profile(positions: list[str], *, cut: float = 0.35) -> dict:
    """build_profile, but for solid-fill steps boards (found via steps_bands)."""
    from books.cv import steps
    from books.cv.classify import extract_glyph
    from books.cv.cluster import agglomerative
    from books.cv.features import glyph_features
    from books.cv.grid import _warp
    from books.cv.recognize import normalize_mask
    from books.cv.squares import split_board, square_color

    entries: list[tuple[str, np.ndarray, str]] = []  # (square_color, glyph, true_code)
    for fen in positions:
        page = fen_to_steps_page([fen])
        gray = cv2.cvtColor(page, cv2.COLOR_BGR2GRAY)
        # single board at slot 1 — crop it directly (band detection needs a
        # full grid of boards to beat its signal floor)
        x0, x1 = STEPS_COLS[0], STEPS_COLS[0] + STEPS_BOARD
        y0, y1 = STEPS_ROWS[0], STEPS_ROWS[0] + STEPS_BOARD
        tight = gray[y0:y1, x0:x1]
        board = _warp(
            tight,
            np.array(
                [[0, 0], [tight.shape[1] - 1, 0],
                 [tight.shape[1] - 1, tight.shape[0] - 1], [0, tight.shape[0] - 1]],
                dtype=np.float32,
            ),
        )
        flat = [p for rank in split_board(board) for p in rank]
        true = chess.Board(fen)
        for index, patch in enumerate(flat):
            glyph = extract_glyph(patch)
            if glyph is None:
                continue
            square = chess.square(index % 8, 7 - index // 8)
            piece = true.piece_at(square)
            code = ("w" if piece.color else "b") + piece.symbol().upper()
            entries.append((square_color(index), glyph, code))

    clusters: dict = {}
    templates: dict = {}
    for color in ("light", "dark"):
        subset = [e for e in entries if e[0] == color]
        if not subset:
            continue
        glyphs = [g for _, g, _ in subset]
        vectors = np.array([glyph_features(g) for g in glyphs])
        means = vectors.mean(axis=0)
        stds = vectors.std(axis=0)
        standardized = (vectors - means) / np.maximum(stds, 1e-6)
        labels, _ = agglomerative(standardized, cut=cut)

        votes: dict[int, dict[str, int]] = {}
        for position, cluster_id in enumerate(labels):
            votes.setdefault(int(cluster_id), {}).setdefault(subset[position][2], 0)
            votes[int(cluster_id)][subset[position][2]] += 1
        for cluster_id, counts in votes.items():
            label = max(counts, key=counts.get)
            key = f"{label}@{color}"
            clusters.setdefault(
                key, {"label": label, "count": 0, "square_color": color}
            )
            clusters[key]["count"] += sum(counts.values())
            masks = [
                normalize_mask(glyphs[position].mask)
                for position, lab in enumerate(labels)
                if int(lab) == cluster_id
            ]
            templates.setdefault(key, [])
            templates[key].append(np.mean(np.stack(masks), axis=0).round(2).tolist())

    templates = {key: np.mean(np.stack(masks), axis=0).round(2).tolist()
                 for key, masks in templates.items()}

    return {
        "clusters": clusters,
        "templates": templates,
        "empty": {"max_ink": 0.04},
        "solidity_cut": 0.4,
        "features": {"means": [0.0] * 10, "stds": [1.0] * 10},
        "source": {"synthetic": True},
    }


def build_profile(positions: list[str], *, cut: float = 0.35) -> dict:
    """Cluster glyphs across several synthetic boards and auto-label clusters
    from the known ground truth (majority vote per cluster).

    Clustering is global across all pages, per square color (matching
    cluster_board), and the profile dict is assembled directly.
    """
    import chess as _chess

    from books.cv import detect_diagrams
    from books.cv.classify import extract_glyph
    from books.cv.cluster import agglomerative
    from books.cv.features import glyph_features
    from books.cv.recognize import normalize_mask
    from books.cv.squares import split_board, square_color

    entries: list[tuple[str, np.ndarray, str]] = []  # (square_color, glyph, true_code)
    for fen in positions:
        page = fen_to_page(fen)
        diagrams = detect_diagrams(page)
        assert diagrams, f"no diagram detected for {fen}"
        board = diagrams[0].board_img
        flat = [p for rank in split_board(board) for p in rank]
        true = _chess.Board(fen)
        for index, patch in enumerate(flat):
            glyph = extract_glyph(patch)
            if glyph is None:
                continue
            square = _chess.square(index % 8, 7 - index // 8)
            piece = true.piece_at(square)
            code = ("w" if piece.color else "b") + piece.symbol().upper()
            entries.append((square_color(index), glyph, code))

    clusters: dict = {}
    templates: dict = {}
    for color in ("light", "dark"):
        subset = [e for e in entries if e[0] == color]
        if not subset:
            continue
        glyphs = [g for _, g, _ in subset]
        vectors = np.array([glyph_features(g) for g in glyphs])
        means = vectors.mean(axis=0)
        stds = vectors.std(axis=0)
        standardized = (vectors - means) / np.maximum(stds, 1e-6)
        labels, _ = agglomerative(standardized, cut=cut)

        votes: dict[int, dict[str, int]] = {}
        for position, cluster_id in enumerate(labels):
            votes.setdefault(int(cluster_id), {}).setdefault(subset[position][2], 0)
            votes[int(cluster_id)][subset[position][2]] += 1
        for cluster_id, counts in votes.items():
            label = max(counts, key=counts.get)
            key = f"{label}@{color}"
            clusters.setdefault(
                key, {"label": label, "count": 0, "square_color": color}
            )
            clusters[key]["count"] += sum(counts.values())
            masks = [
                normalize_mask(glyphs[position].mask)
                for position, lab in enumerate(labels)
                if int(lab) == cluster_id
            ]
            templates.setdefault(key, [])
            templates[key].append(np.mean(np.stack(masks), axis=0).round(2).tolist())

    templates = {key: np.mean(np.stack(masks), axis=0).round(2).tolist()
                 for key, masks in templates.items()}

    return {
        "clusters": clusters,
        "templates": templates,
        "empty": {"max_ink": 0.04},
        "solidity_cut": 0.4,
        "features": {"means": [0.0] * 10, "stds": [1.0] * 10},
        "source": {"synthetic": True},
    }
