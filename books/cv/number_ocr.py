"""Book-specific printed puzzle-number recognition (Polgar-style layouts).

The printed number sits above each diagram's top-left corner. Digit glyphs
are matched against templates BOOTSTRAPPED from the auto-assigned numbers
(the reading-order numbering is known-correct), then used to verify every
puzzle: a mismatch means the printed number disagrees with the reading order.
"""

import logging
from dataclasses import dataclass, field

import cv2
import numpy as np

from books.cv.recognize import _corr, normalize_mask

logger = logging.getLogger("books.cv")

# The printed number is LARGE text CENTERED above the diagram (Polgar style):
# roughly the middle third of the slot width, a third of the slot height up.
BAND_TOP_FRACTION = 0.38  # above the slot, relative to slot height
BAND_BOTTOM_FRACTION = 0.16
BAND_LEFT_FRACTION = 0.05
BAND_RIGHT_FRACTION = 0.55

# Books whose number sits in the gutter ABOVE THE TOP-LEFT CORNER of the
# board (tight 3x4/4x3 grids, e.g. horsehead): a narrow band hugging the
# slot's left edge, just above the board's top edge. Fractions of the slot
# width/height (top/bottom negative = above the slot).
DEFAULT_BAND = {
    "left": BAND_LEFT_FRACTION,
    "right": BAND_RIGHT_FRACTION,
    "top": BAND_TOP_FRACTION,
    "bottom": BAND_BOTTOM_FRACTION,
}

# left-aligned band for top-left-corner numbering (stored per profile)
TOP_LEFT_BAND = {"left": -0.02, "right": 0.30, "top": 0.16, "bottom": 0.01}


@dataclass
class DigitTemplates:
    """Mean normalized glyph per digit, built from labeled samples."""

    templates: dict = field(default_factory=dict)
    counts: dict = field(default_factory=dict)

    def add(self, digit: str, glyph_mask) -> None:
        normalized = normalize_mask(glyph_mask)
        if digit in self.templates:
            self.templates[digit] += normalized
            self.counts[digit] += 1
        else:
            self.templates[digit] = normalized.astype(float)
            self.counts[digit] = 1

    def finalize(self) -> None:
        for digit in self.templates:
            self.templates[digit] /= self.counts[digit]

    def recognize(self, glyph_mask) -> tuple[str, float]:
        best, best_corr = "?", -1.0
        for digit, template in self.templates.items():
            corr = _corr(normalize_mask(glyph_mask), template)
            if corr > best_corr:
                best, best_corr = digit, corr
        return best, best_corr


def number_band(gray: np.ndarray, bbox, band: dict | None = None) -> np.ndarray:
    """The area above a diagram slot where the printed number lives.

    `band` overrides the band geometry as fractions of the slot
    ({"left", "right", "top", "bottom"}; top/bottom are measured upward
    from the slot's top edge when negative). Defaults to the Polgar-style
    centered band.
    """
    band = band or DEFAULT_BAND
    x, y, w, h = bbox
    y0 = max(0, int(y - band["top"] * h))
    y1 = max(y0 + 8, int(y - band["bottom"] * h))
    x0 = max(0, int(x + band["left"] * w))
    x1 = min(gray.shape[1], int(x + band["right"] * w))
    if x1 <= x0:  # degenerate crop (slot hugging the page edge)
        return np.zeros((0, 0), dtype=gray.dtype)
    return gray[y0:y1, x0:x1]


def _merge_fragments(glyphs) -> list[tuple]:
    """Merge fragments of one digit that the thin print broke apart.

    Broken rings split two ways: into vertically separated arcs (top and
    bottom of a "0" — x-ranges overlap, y-ranges close but disjoint) or
    into horizontally separated slices (the sides of a "9" — y-ranges
    overlap, x-ranges adjacent). Touching NEIGHBOUR digits overlap in x
    AND y, which satisfies neither rule, so they are never merged.
    """
    merged = []
    for g in sorted(glyphs, key=lambda t: t[0]):
        placed = False
        for i, m in enumerate(merged):
            x0a, y0a, x1a, y1a, mask_a = m
            x0b, y0b, x1b, y1b, mask_b = g
            x_overlap = min(x1a, x1b) - max(x0a, x0b)
            x_gap = max(x0a, x0b) - min(x1a, x1b)  # negative = overlapping
            y_overlap = min(y1a, y1b) - max(y0a, y0b)
            y_gap = max(y0a, y0b) - min(y1a, y1b)  # negative = overlapping
            arc_split = (
                x_overlap >= 0.5 * min(x1a - x0a, x1b - x0b)
                and 0 <= y_gap <= 40
            )
            slice_split = (
                0 <= x_gap <= 6
                and y_overlap >= 0.4 * min(y1a - y0a, y1b - y0b)
            )
            if not (arc_split or slice_split):
                continue
            x0, y0 = min(x0a, x0b), min(y0a, y0b)
            x1, y1 = max(x1a, x1b), max(y1a, y1b)
            combo = np.zeros((y1 - y0 + 1, x1 - x0 + 1), dtype=bool)
            combo[y0a - y0:y1a - y0 + 1, x0a - x0:x1a - x0 + 1] |= mask_a
            combo[y0b - y0:y1b - y0 + 1, x0b - x0:x1b - x0 + 1] |= mask_b
            merged[i] = (x0, y0, x1, y1, combo)
            placed = True
            break
        if not placed:
            merged.append(g)
    return merged


def extract_digit_glyphs(band: np.ndarray, *,
                         merge_fragments: bool = False) -> list[np.ndarray]:
    """Connected ink components in the number band, sized like digits.

    With `merge_fragments`, thin rings that broke into arcs re-join
    (needed for the smaller 3-4 digit numbers of tight layouts).
    """
    blurred = cv2.GaussianBlur(band, (3, 3), 0)
    binary = cv2.adaptiveThreshold(
        blurred, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV, blockSize=31, C=8,
    )
    count, labels = cv2.connectedComponents(binary)
    height, width = band.shape
    glyphs = []
    for label in range(1, count):
        ys, xs = np.where(labels == label)
        h, w = ys.max() - ys.min() + 1, xs.max() - xs.min() + 1
        if h < 0.15 * height or h > 0.9 * height:
            continue  # caption fragments are much smaller than the number
        if w < 4 or w > 0.5 * width:
            continue
        if w > 2.2 * h:
            continue  # too wide for a digit
        mask = labels == label
        glyphs.append(
            (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max()),
             mask[ys.min():ys.max() + 1, xs.min():xs.max() + 1])
        )
    if merge_fragments:
        glyphs = _merge_fragments(glyphs)
    glyphs.sort(key=lambda g: g[0])
    return [g[-1] for g in glyphs]


def read_number(gray: np.ndarray, bbox, templates: DigitTemplates,
                expected: str | None = None, band: dict | None = None) -> str | None:
    """Recognize the printed number above a slot; None when unreadable.

    The number is the caption's LEADING token (Polgar captions start with
    the puzzle number), so only the first `len(expected)` glyphs are read
    when the expected value is known.
    """
    band_img = number_band(gray, bbox, band=band)
    if band_img.size == 0:
        return None
    merge = bool(band and band.get("merge_fragments"))
    glyphs = extract_digit_glyphs(band_img, merge_fragments=merge)
    if not glyphs or not templates.templates:
        return None
    if expected:
        glyphs = glyphs[: len(str(expected))]
        if len(glyphs) != len(str(expected)):
            return None
    digits = []
    for glyph in glyphs:
        digit, corr = templates.recognize(glyph)
        if corr < 0.5:
            return None
        digits.append(digit)
    return "".join(digits)


def bootstrap_templates(samples, band: dict | None = None) -> DigitTemplates:
    """Build digit templates from (gray, bbox, expected_number) samples.

    Pairs the first len(expected) glyphs of each band with the expected
    digits — the caption's leading token is the printed number.
    """
    templates = DigitTemplates()
    merge = bool(band and band.get("merge_fragments"))
    for gray, bbox, expected in samples:
        band_img = number_band(gray, bbox, band=band)
        if band_img.size == 0:
            continue
        glyphs = extract_digit_glyphs(band_img, merge_fragments=merge)
        expected_digits = list(str(expected))
        glyphs = glyphs[: len(expected_digits)]
        if len(glyphs) != len(expected_digits):
            continue  # unreadable band: skip this sample
        for glyph, digit in zip(glyphs, expected_digits):
            templates.add(digit, glyph)
    templates.finalize()
    return templates


def serialize_templates(templates: DigitTemplates) -> dict:
    """JSON-serializable form for the calibration profile."""
    return {
        digit: template.round(2).tolist()
        for digit, template in sorted(templates.templates.items())
    }


def load_templates(profile: dict) -> DigitTemplates | None:
    """Digit templates stored in the profile, or None when absent."""
    stored = (profile or {}).get("number_templates")
    if not stored:
        return None
    templates = DigitTemplates()
    templates.templates = {
        digit: np.asarray(grid, dtype=np.float64) for digit, grid in stored.items()
    }
    return templates
