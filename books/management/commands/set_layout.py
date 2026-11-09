"""Calibrate a book's page layout from one clean page (PDF page number)."""

import logging

from django.core.management.base import BaseCommand, CommandError

from books.cv import detect_diagrams, render_pdf_page
from books.cv.layout import infer_layout
from books.models import Book, CalibrationProfile

logger = logging.getLogger("books.cv")


class Command(BaseCommand):
    help = (
        "Calibrate a book's fixed page layout (diagram slots) from one clean "
        "page of its PDF. The slots are then reused on every page, so weak "
        "pages still yield every diagram. With --steps, detects the solid-"
        "fill 3x4 board grid of Steps-workbook pages instead of the generic "
        "diagram detector, and stores positional numbering (p<page>-<slot>)."
    )

    def add_arguments(self, parser):
        parser.add_argument("book_slug")
        parser.add_argument("pdf_path")
        parser.add_argument("pdf_page", type=int, help="1-based PDF page number")
        parser.add_argument("--steps", action="store_true",
                            help="Steps workbook mode: solid-fill 3x4 grid + p<page>-<slot> numbering")
        parser.add_argument("--first-page", type=int, default=1,
                            help="steps: the PDF page (1-based) that is printed as page 1 — "
                            "books have variable front matter")
        parser.add_argument("--threshold", type=int, default=230,
                            help="steps: gray threshold for grid band detection")
        parser.add_argument("--margin", type=int, default=30,
                            help="steps: OCR crop margin (must stay inside the gutters)")
        parser.add_argument("--verbose", action="store_true", help="debug logging")

    def handle(self, *args, **options):
        logging.getLogger("books.cv").setLevel(
            logging.DEBUG if options["verbose"] else logging.INFO
        )
        book = Book.objects.filter(slug=options["book_slug"]).first()
        if book is None:
            raise CommandError(f"No book with slug {options['book_slug']!r}.")

        img = render_pdf_page(options["pdf_path"], options["pdf_page"] - 1, dpi=150)
        profile, _ = CalibrationProfile.objects.get_or_create(book=book)

        if options["steps"]:
            from books.cv.driver import gray_view
            from books.cv.steps import steps_bands

            col_bands, row_bands = steps_bands(
                gray_view(img), threshold=options["threshold"]
            )
            if len(col_bands) < 2 or len(row_bands) < 2:
                raise CommandError(
                    "Grid not detected on this page — pick a puzzle page "
                    f"(got {len(col_bands)} column(s), {len(row_bands)} row(s))."
                )
            height, width = img.shape[:2]
            profile.data["layout"] = {
                "columns": [[x0 / width, x1 / width] for x0, x1 in col_bands],
                "rows": [[y0 / height, y1 / height] for y0, y1 in row_bands],
            }
            profile.data["numbering"] = {
                "mode": "slots",
                "format": "p{page:03d}-{slot:02d}",
            }
            profile.data["steps"] = {
                "threshold": options["threshold"],
                "crop_margin": options["margin"],
            }
            profile.data["first_page"] = options["first_page"]
            profile.save(update_fields=["data", "updated_at"])
            self.stdout.write(
                self.style.SUCCESS(
                    f"Stored steps layout for {book.slug}: "
                    f"{len(col_bands)} column(s) x {len(row_bands)} row(s) "
                    f"({len(col_bands) * len(row_bands)} slots), numbering "
                    f"p{{page:03d}}-{{slot:02d}} (first_page={options['first_page']})."
                )
            )
            return

        diagrams = detect_diagrams(img)
        if not diagrams:
            raise CommandError("No diagrams detected on this page — pick another.")
        layout = infer_layout(diagrams, page_shape=img.shape)
        if layout is None:
            raise CommandError("Detected diagrams don't form a regular grid.")

        profile.data["layout"] = layout
        profile.save(update_fields=["data", "updated_at"])
        self.stdout.write(
            self.style.SUCCESS(
                f"Stored layout for {book.slug}: {len(layout['rows'])} row(s) x "
                f"{len(layout['columns'])} column(s) "
                f"({len(layout['rows']) * len(layout['columns'])} diagrams per page)."
            )
        )
