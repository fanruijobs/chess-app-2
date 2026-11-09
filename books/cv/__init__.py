"""Local CV pipeline: book page images -> chess positions (FEN)."""

from books.cv.calibrate import build_profile_data, cluster_board
from books.cv.driver import CalibrationError, ExtractionResult, extract_page
from books.cv.grid import detect_diagrams
from books.cv.io import load_image, render_pdf_page
from books.cv.steps import extract_steps_page, is_full_board, steps_bands, steps_slots

__all__ = [
    "CalibrationError",
    "ExtractionResult",
    "build_profile_data",
    "cluster_board",
    "detect_diagrams",
    "extract_page",
    "extract_steps_page",
    "is_full_board",
    "load_image",
    "render_pdf_page",
    "steps_bands",
    "steps_slots",
]
