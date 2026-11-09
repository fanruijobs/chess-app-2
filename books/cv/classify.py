"""Per-square glyph extraction: empty detection, polarity, solidity."""

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass(eq=False)  # ndarray fields make __eq__ ambiguous
class Glyph:
    patch: np.ndarray  # grayscale patch (square)
    mask: np.ndarray  # union ink mask (dark | light) — consistent piece shape
    polarity: int  # 1 = mostly dark ink, -1 = mostly light ink
    solidity: float  # union-ink pixels / glyph bounding-box area
    fg_ratio: float  # union-ink pixels / patch pixels
    light_ratio: float  # light-ink pixels / patch pixels
    dark_solidity: float  # dark-mask solidity: rings (white pieces) vs blobs (black)
    ink_ratio: float  # light/dark ink ratio — the robust white/black signal


def _ink_masks(patch: np.ndarray) -> tuple[np.ndarray, np.ndarray, float, float]:
    """Dark and light ink masks + their ratios.

    White pieces are outline+fill: on light squares only the dark outline is
    visible, on dark squares the light fill shows — the UNION of both masks
    is the same piece silhouette either way. Black pieces produce dark ink
    only, so light_ratio is the white/black color signal.
    """
    # background = the mode of the patch CORNERS: pieces almost never reach
    # the corners, so they reliably show the square color (the global mode
    # and the median are corrupted by large pieces)
    corners = np.array(
        [patch[0, 0], patch[0, -1], patch[-1, 0], patch[-1, -1]]
    )
    background = float(np.bincount(corners.astype(int), minlength=256).argmax())
    delta = max(10.0, 0.25 * np.abs(float(np.median(np.abs(patch - background)))))
    dark = patch < (background - delta)
    light = patch > (background + delta)
    dark_ratio = float(dark.sum()) / dark.size
    light_ratio = float(light.sum()) / light.size
    return dark, light, dark_ratio, light_ratio


def extract_glyph(patch: np.ndarray, empty_max_ink: float = 0.04) -> Glyph | None:
    """Extract the piece glyph from one square patch, or None when empty.

    White pieces are drawn as thin outlines, so the raw ink ratio is a poor
    emptiness test (rings are ~2%); emptiness is judged by the LARGEST
    connected ink component instead — noise specks are far smaller than any
    real glyph.
    """
    if patch.ndim == 3:
        patch = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY)
    raw_dark, raw_light, dark_ratio, light_ratio = _ink_masks(patch)
    # the union mask is morphologically closed so white details on black
    # pieces don't punch inconsistent holes; dark_solidity below is computed
    # from the RAW dark mask where a white piece's outline stays a ring
    # drop SHORT ink components touching the patch border (on the raw masks):
    # dotted grid lines bleed a few pixels into the square; pieces sit
    # baseline-aligned at the square's bottom and also touch the border, but
    # they are tall — keep those
    raw_mask = raw_dark | raw_light
    count, labels = cv2.connectedComponents(raw_mask.astype(np.uint8))
    height, width = raw_mask.shape
    for label in range(1, count):
        ys, xs = np.where(labels == label)
        if not (ys.min() == 0 or xs.min() == 0
                or ys.max() == height - 1 or xs.max() == width - 1):
            continue
        # border-touching ink: a grid-line dot band has a FLAT top edge
        # (same y across its width), a piece protrudes above the band
        columns = {x: min(ys[xs == x]) for x in np.unique(xs)}
        top_std = float(np.std(list(columns.values()))) if len(columns) > 2 else 0.0
        component_width = xs.max() - xs.min() + 1
        if top_std < 2.5 and component_width > 0.3 * width:
            raw_mask[ys, xs] = False
    dark = raw_mask & raw_dark
    light = raw_mask & raw_light
    kernel = np.ones((3, 3), np.uint8)
    dark = cv2.morphologyEx(dark.astype(np.uint8), cv2.MORPH_CLOSE, kernel).astype(bool)
    light = cv2.morphologyEx(light.astype(np.uint8), cv2.MORPH_CLOSE, kernel).astype(bool)
    mask = dark | light
    fg_ratio = float(mask.sum()) / mask.size
    contours, _ = cv2.findContours(
        mask.astype(np.uint8) * 255, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )
    if not contours:
        return None
    largest = max(contours, key=cv2.contourArea)
    # component-area emptiness test (a glyph covers >= ~0.5% of the square)
    if cv2.contourArea(largest) < 0.005 * mask.size:
        return None
    if fg_ratio < empty_max_ink * 0.05:  # absolute floor: essentially no ink
        return None

    # glyph bounding box from the largest foreground blob
    x, y, w, h = cv2.boundingRect(largest)
    bbox_area = max(1, w * h)
    solidity = float(mask.sum()) / bbox_area

    # dark-mask solidity is the color signal: white pieces are thin outline
    # rings (low solidity), black pieces are solid blobs — independent of the
    # square color (white details on black pieces pollute light_ratio).
    # Uses the RAW dark mask: closing would fill a white piece's ring.
    dark_solidity = 0.0
    dark_contours, _ = cv2.findContours(
        raw_dark.astype(np.uint8) * 255, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )
    if dark_contours:
        dx, dy, dw, dh = cv2.boundingRect(max(dark_contours, key=cv2.contourArea))
        dark_solidity = float(raw_dark.sum()) / max(1, dw * dh)

    # the robust white/black signal: light-ink to dark-ink ratio. White
    # pieces = dark outline + light fill (ratio ~0.4..3), black pieces =
    # dark body + small light details (ratio <~0.3).
    ink_ratio = light_ratio / max(dark_ratio, 1e-6)

    return Glyph(
        patch=patch,
        mask=mask,
        polarity=1 if dark_ratio >= light_ratio else -1,
        solidity=solidity,
        fg_ratio=fg_ratio,
        light_ratio=light_ratio,
        dark_solidity=dark_solidity,
        ink_ratio=ink_ratio,
    )


def solidity_threshold_for(glyphs: list[Glyph]) -> float:
    """Best white/black split over observed solidities (midpoint of the
    two most extreme clusters); fallback 0.5."""
    values = sorted(g.solidity for g in glyphs)
    if len(values) < 2:
        return 0.5
    # simple 2-means over 1D solidity, one pass
    best = (0.5, float("inf"))
    for split in np.linspace(0.1, 0.9, 17):
        low = [v for v in values if v <= split]
        high = [v for v in values if v > split]
        if not low or not high:
            continue
        cost = sum((v - np.mean(low)) ** 2 for v in low) + sum(
            (v - np.mean(high)) ** 2 for v in high
        )
        if cost < best[1]:
            best = (split, cost)
    return round(best[0], 3)
