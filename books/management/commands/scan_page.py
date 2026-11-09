"""Extract diagrams from a single page image/PDF page into puzzles."""

import logging

from django.core.management.base import BaseCommand, CommandError

from books.cv import CalibrationError
from books.models import Book
from books.services import load_page_source, process_page

logger = logging.getLogger("books.cv")


class Command(BaseCommand):
    help = "Extract diagrams from one page photo (or the first page of a PDF)."

    def add_arguments(self, parser):
        parser.add_argument("book_slug")
        parser.add_argument("image_path")
        parser.add_argument("--page-number", type=int, default=None)
        parser.add_argument("--review-threshold", type=float, default=None)
        parser.add_argument("--force", action="store_true",
                            help="re-extract verified puzzles too")
        parser.add_argument("--dry-run", action="store_true",
                            help="print the extraction table, write nothing")
        parser.add_argument("--verbose", action="store_true", help="debug logging")

    def handle(self, *args, **options):
        logging.getLogger("books.cv").setLevel(
            logging.DEBUG if options["verbose"] else logging.INFO
        )
        book = Book.objects.filter(slug=options["book_slug"]).first()
        if book is None:
            raise CommandError(f"No book with slug {options['book_slug']!r}.")

        sources = load_page_source(options["image_path"])
        source_file, img = sources[0]  # scan_page takes the first page

        try:
            if options["dry_run"]:
                from books.services import get_profile
                from books.cv import extract_page

                results = extract_page(img, get_profile(book))
                for i, result in enumerate(results, start=1):
                    status = "review" if (result.confidence < 0.8 or not result.legal) else "ok"
                    self.stdout.write(
                        f"[{i}] {result.fen} conf={result.confidence} {status} "
                        f"{'; '.join(result.notes)}"
                    )
                self.stdout.write(f"dry run: {len(results)} diagram(s), nothing written")
                return
            page, results, counts = process_page(
                book, img, source_file=source_file,
                page_number=options["page_number"], force=options["force"],
                review_threshold=options["review_threshold"],
            )
        except CalibrationError as exc:
            raise CommandError(str(exc)) from exc

        for i, result in enumerate(results, start=1):
            self.stdout.write(f"[{i}] {result.fen} conf={result.confidence}")
        created, updated, skipped = counts
        self.stdout.write(
            self.style.SUCCESS(
                f"page {page.page_number or page.pk}: created={created} "
                f"updated={updated} skipped={skipped}"
            )
        )
