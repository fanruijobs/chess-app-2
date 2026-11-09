"""Page ingestion: image/PDF -> PageImage + Puzzle rows (shared by commands)."""

import logging
from pathlib import Path

import cv2
import numpy as np
from django.conf import settings
from django.core.files.base import ContentFile
from django.utils import timezone

from books.cv import CalibrationError, extract_page, load_image, render_pdf_page
from books.models import Book, PageImage, Puzzle

logger = logging.getLogger("books.cv")


def get_profile(book: Book) -> dict:
    """The book's calibrated profile data, or raise CalibrationError.

    Steps books (positional numbering) need no glyph templates when the OCR
    bridge does the recognition; other books still require calibration.
    """
    profile = getattr(book, "calibration", None)
    is_steps = bool(
        profile
        and (profile.data or {}).get("numbering", {}).get("mode")
        in ("slots", "printed")
    )
    if profile is None or (not profile.data.get("templates") and not is_steps):
        raise CalibrationError(
            f"book {book.slug!r} is not calibrated — run: "
            f".venv/bin/python manage.py calibrate_book {book.slug} <page_photo>"
        )
    return profile.data


def store_page_image(book: Book, img: np.ndarray, *, source_file: str = "",
                     page_number: int | None = None) -> PageImage:
    """Create a PageImage row, or update the existing one for the same page
    (image stored in media/, PNG-encoded)."""
    ok, png = cv2.imencode(".png", img)
    if not ok:
        raise ValueError("failed to encode page image")
    page = (
        PageImage.objects.filter(book=book, page_number=page_number).first()
        if page_number is not None
        else None
    )
    if page is None and source_file:
        page = PageImage.objects.filter(book=book, source_file=source_file).first()
    old_files = []
    if page is None:
        page = PageImage(
            book=book,
            page_number=page_number,
            source_file=source_file,
        )
    else:
        old_files = [f.name for f in (page.image, page.thumbnail) if f]
        page.thumbnail = ""  # stale once the image is replaced; regenerated on save
    page.width = int(img.shape[1])
    page.height = int(img.shape[0])
    page.image.save(f"page_{timezone.now():%Y%m%d%H%M%S}_{page.pk or 'x'}.png",
                    ContentFile(png.tobytes()), save=False)
    page.save()
    for name in old_files:
        try:
            page.image.storage.delete(name)
        except Exception:  # noqa: BLE001 - orphan cleanup is best-effort
            pass
    logger.info(
        "stored page image (%s): page_number=%s, %dx%d",
        "new" if not old_files else "updated",
        page_number, page.width, page.height,
    )
    return page


def upsert_puzzles(book: Book, page: PageImage, results,
                   *, force: bool = False, review_threshold: float | None = None,
                   number_start: int | None = None) -> tuple[int, int, int]:
    """Store extraction results as Puzzle rows. Returns (created, updated, skipped).

    Verified puzzles are never touched unless force. Status: below the review
    threshold or illegal FEN -> review, else extracted. With `number_start`
    the book's own numbering is auto-filled by reading order (puzzle N = the
    N-th diagram across the book).
    """
    threshold = (
        review_threshold
        if review_threshold is not None
        else settings.BOOKS_REVIEW_THRESHOLD
    )
    created = updated = skipped = 0
    for index, result in enumerate(results, start=1):
        status = (
            Puzzle.Status.REVIEW
            if (result.confidence < threshold or not result.legal)
            else Puzzle.Status.EXTRACTED
        )
        fields = {
            "fen": result.fen,
            "confidence": result.confidence,
            "diagram_bbox": result.bbox,
            "piece_cells": result.cells,
            "extraction_notes": "; ".join(result.notes),
        }
        # steps layouts keep the grid position (slot_index) so numbers stay
        # stable across partial pages and re-imports
        diagram_index = getattr(result, "slot_index", None) or index
        try:
            puzzle, was_created = Puzzle.objects.get_or_create(
                book=book, page=page, diagram_index=diagram_index,
                defaults={**fields, "status": status},
            )
        except Exception:  # noqa: BLE001 - constraint race, treat as skipped
            skipped += 1
            continue
        if was_created:
            created += 1
        else:
            if puzzle.status == Puzzle.Status.VERIFIED and not force:
                skipped += 1
                continue
            for key, value in fields.items():
                setattr(puzzle, key, value)
            puzzle.save(update_fields=list(fields) + ["updated_at"])
            updated += 1
        detected_number = getattr(result, "number", None)
        if detected_number and puzzle.number != detected_number:
            # the book's OWN printed number, read by OCR — authoritative
            puzzle.number = detected_number
            puzzle.save(update_fields=["number", "updated_at"])
        elif number_start is not None and not puzzle.number:
            puzzle.number = str(number_start + index - 1)
            puzzle.save(update_fields=["number", "updated_at"])
    return created, updated, skipped


def process_page(book: Book, img: np.ndarray, *, source_file: str = "",
                 page_number: int | None = None, force: bool = False,
                 review_threshold: float | None = None, save_page: bool = True,
                 number_start: int | None = None) -> tuple[PageImage | None, list, tuple[int, int, int]]:
    """Extract a page image and store everything. Returns (page, results, counts)."""
    profile = get_profile(book)
    results = extract_page(img, profile, page_number=page_number)
    if not save_page:
        return None, results, (0, 0, 0)
    page = store_page_image(book, img, source_file=source_file, page_number=page_number)
    counts = upsert_puzzles(book, page, results, force=force,
                            review_threshold=review_threshold,
                            number_start=number_start)
    page.status = PageImage.Status.PROCESSED
    page.extracted_at = timezone.now()
    page.save(update_fields=["status", "extracted_at"])
    return page, results, counts


def slots_per_page(book: Book) -> int | None:
    """Diagrams per page from the book's stored layout (None without one)."""
    profile = getattr(book, "calibration", None)
    layout = (profile.data or {}).get("layout") if profile else None
    if not layout:
        return None
    return len(layout.get("rows", [])) * len(layout.get("columns", []))


def load_page_source(path: str):
    """Load a page image or PDF (all pages) as a list of (source_file, image)."""
    path = Path(path)
    if path.suffix.lower() == ".pdf":
        import pymupdf

        doc = pymupdf.open(str(path))
        try:
            pages = []
            for i in range(len(doc)):
                pix = doc[i].get_pixmap(dpi=200)
                buf = np.frombuffer(pix.tobytes("png"), dtype=np.uint8)
                pages.append((f"{path.name}#page={i + 1}", cv2.imdecode(buf, cv2.IMREAD_COLOR)))
            return pages
        finally:
            doc.close()
    return [(path.name, load_image(str(path)))]
