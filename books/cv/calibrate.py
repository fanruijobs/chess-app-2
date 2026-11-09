"""Calibration: cluster a page's glyphs and build a per-book profile.

Glyphs are clustered SEPARATELY per square color (light/dark): the same piece
renders differently on the two square colors, so mixing them fragments or
merges clusters. Cluster ids are prefixed L/D and unique across both spaces;
recognition gates templates by the glyph's own square color.
"""

import cv2
import numpy as np

from books.cv.classify import Glyph, extract_glyph
from books.cv.cluster import agglomerative
from books.cv.features import glyph_features
from books.cv.recognize import normalize_mask
from books.cv.squares import split_board, square_color

SQUARE_NAMES = [f + str(r) for r in range(8, 0, -1) for f in "abcdefgh"]


def _silhouette_features(mask: np.ndarray) -> np.ndarray:
    """Coarse shape descriptor: height, width, ink density, top-edge profile."""
    ys, xs = np.where(mask)
    if len(xs) == 0:
        return np.zeros(9)
    height = ys.max() - ys.min() + 1
    width = xs.max() - xs.min() + 1
    ink = len(xs) / max(1, height * width)
    columns = np.linspace(xs.min(), xs.max(), 6)
    tops = [ys[xs >= int(c)].min() for c in columns]
    tops = [(t - ys.min()) / max(1, height) for t in tops]
    return np.array([height, width, ink] + tops)


def cluster_board(board_img: np.ndarray, cut: float = 0.35,
                  empty_max_ink: float = 0.04, baseline: bool = False) -> dict:
    """Extract + cluster glyphs from one warped board.

    Returns {"clusters": {id: {count, suggested_color, square_color,
    example_squares}}, "square_clusters": {square_index: cluster_id|None},
    "glyphs": [...64...]} — the input to the calibration CLI/UI.
    """
    from books.cv.grid import erase_grid_lines, warped_grid_lines

    lines = warped_grid_lines(board_img)
    if baseline and lines is not None:
        board_img = erase_grid_lines(board_img, lines[0], lines[1])
    patches = split_board(board_img, baseline=baseline)
    flat_patches = [p for rank in patches for p in rank]  # rank 8 first
    glyphs: list[Glyph | None] = [
        extract_glyph(p, empty_max_ink=empty_max_ink) for p in flat_patches
    ]

    light: list[tuple[int, Glyph]] = []
    dark: list[tuple[int, Glyph]] = []
    for index, glyph in enumerate(glyphs):
        if glyph is None:
            continue
        (light if square_color(index) == "light" else dark).append((index, glyph))

    clusters: dict = {}
    cluster_by_square: dict[int, str | None] = {}
    for square_index, _ in enumerate(glyphs):
        cluster_by_square[square_index] = None

    for prefix, pairs in (("L", light), ("D", dark)):
        if not pairs:
            continue
        subset = [glyph for _, glyph in pairs]
        # silhouette features (height, width, ink density, top-edge profile):
        # robust for fragmented line-drawn pieces where pixel-level
        # correlation between same-type glyphs is low
        vectors = np.stack([_silhouette_features(g.mask) for g in subset])
        means = vectors.mean(axis=0)
        stds = vectors.std(axis=0)
        standardized = (vectors - means) / np.maximum(stds, 1e-6)
        labels, _ = agglomerative(standardized, cut=cut)

        for cluster_id in range(int(labels.max()) + 1):
            positions = [i for i, lab in enumerate(labels) if lab == cluster_id]
            members = [subset[i] for i in positions]
            key = f"{prefix}{cluster_id}"
            clusters[key] = {
                "count": len(members),
                # light/dark ink ratio: white pieces = outline + fill,
                # black pieces = body + small light details
                "suggested_color": (
                    "white"
                    if np.mean([g.ink_ratio for g in members]) >= 0.35
                    else "black"
                ),
                "square_color": "light" if prefix == "L" else "dark",
                "example_squares": [],
            }
        for position, cluster_id in enumerate(labels):
            square_index, glyph = pairs[position]
            key = f"{prefix}{int(cluster_id)}"
            cluster_by_square[square_index] = key
            if len(clusters[key]["example_squares"]) < 6:
                clusters[key]["example_squares"].append(SQUARE_NAMES[square_index])

    return {
        "clusters": clusters,
        "square_clusters": cluster_by_square,
        "glyphs": glyphs,
    }


def build_profile_data(cluster_info: dict, labels_by_cluster: dict,
                       empty_max_ink: float = 0.04,
                       solidity_cut: float = 0.4,
                       source: dict | None = None,
                       baseline: bool = False) -> dict:
    """Build the CalibrationProfile.data payload from cluster labels.

    Clusters sharing a label (e.g. two knights on different square colors
    clustered separately) are merged into one template per (label, square
    color) — single-glyph clusters would otherwise make weak templates.
    """
    glyphs = cluster_info["glyphs"]
    square_clusters = cluster_info["square_clusters"]

    clusters: dict = {}
    templates: dict[str, list] = {}
    for square_index, cluster_id in square_clusters.items():
        if cluster_id is None:
            continue
        label = labels_by_cluster.get(cluster_id)
        if not label:
            continue
        square = cluster_info["clusters"][cluster_id]["square_color"]
        key = f"{label}@{square}"
        clusters.setdefault(key, {"label": label, "count": 0, "square_color": square})
        clusters[key]["count"] += 1
        glyph = glyphs[square_index]
        templates.setdefault(key, []).append(normalize_mask(glyph.mask).astype(float))

    mean_templates = {
        key: np.mean(np.stack(masks), axis=0).round(2).tolist()
        for key, masks in templates.items()
    }

    return {
        "clusters": clusters,
        "templates": mean_templates,
        "empty": {"max_ink": empty_max_ink},
        "solidity_cut": solidity_cut,
        "features": {"means": [0.0] * 10, "stds": [1.0] * 10},
        "style": {"baseline": baseline},
        "source": source or {},
    }
