"""Piece recognition: template correlation against a per-book profile."""

import cv2
import numpy as np

from books.cv.classify import Glyph
from books.cv.features import DOWNSAMPLE
from books.cv.squares import square_color

# correlation thresholds for template matching
CORR_CONFIRM = 0.75
CORR_WEAK = 0.55


def normalize_mask(mask: np.ndarray, size: int = DOWNSAMPLE) -> np.ndarray:
    """Translation-invariant glyph normalization.

    Crop the mask to its ink bounding box and rescale aspect-preserving into
    a `size` x `size` canvas — the same piece yields the same normalized shape
    regardless of where it sits in its square.
    """
    ys, xs = np.where(mask)
    if len(xs) == 0:
        return np.zeros((size, size))
    top, bottom, left, right = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
    crop = mask[top:bottom, left:right].astype(np.float64)
    scale = min(size / crop.shape[0], size / crop.shape[1])
    resized = cv2.resize(
        crop, (max(1, int(crop.shape[1] * scale)), max(1, int(crop.shape[0] * scale))),
        interpolation=cv2.INTER_AREA,
    )
    canvas = np.zeros((size, size))
    y0 = (size - resized.shape[0]) // 2
    x0 = (size - resized.shape[1]) // 2
    canvas[y0:y0 + resized.shape[0], x0:x0 + resized.shape[1]] = resized
    return canvas


def _corr(patch: np.ndarray, template: np.ndarray) -> float:
    """Normalized cross-correlation between a downsampled patch and a template."""
    a = patch.astype(np.float64).ravel()
    b = np.asarray(template, dtype=np.float64).ravel()
    if a.std() < 1e-6 or b.std() < 1e-6:
        return 0.0
    return float(np.corrcoef(a, b)[0, 1])


def recognize_glyph(glyph: Glyph | None, profile: dict,
                    square_index: int = 0) -> tuple[str | None, float]:
    """Classify one square: returns (piece_code | None, correlation).

    piece_code is None for an empty square, "?" for an unrecognized glyph.
    Templates are gated twice: by the square color (pieces render differently
    on light/dark squares, and calibration clusters per square color) and by
    the piece color (the union mask erases color, so the glyph's dark-mask
    solidity decides: rings are white pieces, blobs are black).
    """
    if glyph is None:
        return None, 1.0

    templates = profile["templates"]  # cluster id -> mean normalized grid
    patch = normalize_mask(glyph.mask)
    glyph_color = "w" if glyph.ink_ratio >= 0.35 else "b"
    square = square_color(square_index)

    # gate strictly first, then widen: calibration pages rarely cover every
    # (piece, square-color) combination, so fall back to the piece-color gate
    # only, then to all templates
    for gate_mode in ("both", "color", "none"):
        best_id, best_corr = None, -1.0
        for cluster_id, template in templates.items():
            info = profile["clusters"].get(cluster_id, {})
            label = info.get("label") or ""
            if gate_mode in ("both", "color") and label and label[0] != glyph_color:
                continue
            if gate_mode == "both" and info.get("square_color") != square:
                continue
            corr = _corr(patch, np.asarray(template))
            if corr > best_corr:
                best_id, best_corr = cluster_id, corr
        if best_id is not None:
            break

    if best_id is None:
        return "?", 0.0
    label = profile["clusters"].get(best_id, {}).get("label")
    if label is None or best_corr < CORR_WEAK:
        return "?", best_corr
    return label, best_corr
