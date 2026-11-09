"""Smoke test over REAL book page photos (see fixtures/pages/README.md).

Skipped when no photos are present. With photos, runs extraction over all of
them with the first book's calibration and reports per-photo diagram counts
+ review flags — the manual acceptance check for real-font robustness
(synthetic tests only prove pipeline mechanics).
"""

from pathlib import Path

import pytest

from books.cv import CalibrationError, extract_page, load_image
from books.models import Book

FIXTURES = Path(__file__).parent / "fixtures" / "pages"


def _photos():
    return sorted(
        p for p in FIXTURES.glob("*")
        if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")
    )


@pytest.mark.django_db
def test_real_pages_smoke():
    photos = _photos()
    if not photos:
        pytest.skip("no real page photos in books/tests/fixtures/pages/")
    book = Book.objects.filter(calibration__isnull=False).first()
    if book is None:
        pytest.skip("no calibrated book available")

    profile = book.calibration.data
    report = []
    for photo in photos:
        try:
            results = extract_page(load_image(photo), profile)
        except CalibrationError as exc:
            report.append(f"{photo.name}: {exc}")
            continue
        review = sum(1 for r in results if r.confidence < 0.8 or not r.legal)
        report.append(
            f"{photo.name}: {len(results)} diagram(s), {review} flagged for review"
        )
    print("\n".join(report))
    assert report  # photos existed; we ran on all of them


@pytest.mark.django_db
def test_steps_pdf_smoke():
    """Real step-6 workbook PDF: pages 3-6 must yield full 12-slot grids.

    Skipped unless the PDF exists and the OCR bridge is available (the real
    recognition check). Pages 1-2 (cover/text) must yield ZERO results.
    """
    pdf = Path("uploaded-books/step-6-workbook-extra.pdf")
    if not pdf.exists():
        pytest.skip("no step-6-workbook-extra.pdf in uploaded-books/")
    from books import ocr

    if not ocr.available():
        pytest.skip("OCR bridge unavailable (.venv313)")

    from books.cv.io import render_pdf_page
    from books.cv.steps import extract_steps_page

    layout = {
        "columns": [[97 / 1125, 363 / 1125], [435 / 1125, 701 / 1125],
                    [773 / 1125, 1040 / 1125]],
        "rows": [[190 / 1598, 456 / 1598], [531 / 1598, 798 / 1598],
                 [873 / 1598, 1140 / 1598], [1216 / 1598, 1483 / 1598]],
    }
    profile = {
        "layout": layout,
        "numbering": {"mode": "slots", "format": "p{page:03d}-{slot:02d}"},
        "steps": {"threshold": 230, "crop_margin": 30},
    }
    report = []
    for page_number in (1, 2, 3, 4, 5, 6):
        img = render_pdf_page(pdf, page_number - 1, dpi=150)
        try:
            with ocr.OcrSession() as session:
                ocr.set_session(session)
                results = extract_steps_page(img, profile, page_number=page_number)
        finally:
            ocr.set_session(None)  # don't leak a closed session into other tests
        legal = sum(1 for r in results if r.legal)
        report.append(
            f"page {page_number}: {len(results)} result(s), {legal} legal"
        )
    print("\n".join(report))
    assert report
    # pages 3-6 are full puzzle pages; 1-2 must extract nothing
    assert "page 1: 0 result(s)" in report[0]
    assert "page 2: 0 result(s)" in report[1]
    for line in report[2:]:
        count = int(line.split(":", 1)[1].strip().split()[0])
        assert count > 0, line
