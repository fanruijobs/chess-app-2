"""Image/PDF loading for the CV pipeline."""

from pathlib import Path

import cv2
import numpy as np


def load_image(path) -> np.ndarray:
    """Load an image file as a BGR ndarray."""
    img = cv2.imread(str(path))
    if img is None:
        raise ValueError(f"cannot read image: {path}")
    return img


def render_pdf_page(path, page_number: int, dpi: int = 200) -> np.ndarray:
    """Render one page of a PDF as a BGR ndarray."""
    import pymupdf

    doc = pymupdf.open(str(path))
    try:
        page = doc[page_number]  # 0-based
        pix = page.get_pixmap(dpi=dpi)
        png = pix.tobytes("png")
    finally:
        doc.close()
    buf = np.frombuffer(png, dtype=np.uint8)
    return cv2.imdecode(buf, cv2.IMREAD_COLOR)
