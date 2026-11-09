"""Batch-import book pages from a directory of images or a PDF."""

import logging
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from books.cv import CalibrationError
from books.models import Book, PageImage
from books.services import load_page_source, process_page, slots_per_page

logger = logging.getLogger("books.cv")


class Command(BaseCommand):
    help = (
        "Import book pages from a directory of images or a scanned PDF. "
        "Each page is extracted with the book's calibration profile and its "
        "diagrams become puzzles (idempotent: re-imports update in place and "
        "never touch verified puzzles without --force)."
    )

    def add_arguments(self, parser):
        parser.add_argument("book_slug")
        parser.add_argument("path", help="directory of images, or a PDF file")
        parser.add_argument("--page-number-start", type=int, default=None,
                            help="PDF: the first page to import (1-based, as shown "
                            "in a reader); numbering starts at puzzle 1 there")
        parser.add_argument("--review-threshold", type=float, default=None)
        parser.add_argument("--force", action="store_true",
                            help="re-extract verified puzzles too")
        parser.add_argument("--limit", type=int, default=None,
                            help="import at most N pages")
        parser.add_argument("--skip-existing", action="store_true",
                            help="skip pages already imported (same source_file)")
        parser.add_argument("--dry-run", action="store_true",
                            help="extract and print, write nothing")
        parser.add_argument("--log-file", default=None,
                            help="append processing log lines to this file")
        parser.add_argument("--verbose", action="store_true", help="debug logging")

    def handle(self, *args, **options):
        logger = logging.getLogger("books.cv")
        logger.setLevel(logging.DEBUG if options["verbose"] else logging.INFO)
        if options["log_file"]:
            handler = logging.FileHandler(options["log_file"])
            handler.setFormatter(
                logging.Formatter("%(asctime)s %(levelname)s %(message)s")
            )
            logger.addHandler(handler)
        book = Book.objects.filter(slug=options["book_slug"]).first()
        if book is None:
            raise CommandError(f"No book with slug {options['book_slug']!r}.")

        path = Path(options["path"])
        if path.is_dir():
            images = sorted(
                p for p in path.iterdir()
                if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")
            )
            from books.cv import load_image

            number_start = options["page_number_start"] or 1
            sources = []
            for i, image_path in enumerate(images, start=number_start):
                try:
                    page_number = int(Path(image_path.name).stem.split("_")[0])
                except ValueError:
                    page_number = i
                sources.append(
                    (image_path.name, load_image(str(image_path)), page_number)
                )
        else:
            # PDF: page-number-start is the FIRST PDF page to import (1-based,
            # as displayed in a reader); earlier pages (covers, front matter)
            # are skipped and numbering starts at puzzle 1 on that page
            start_page = options["page_number_start"] or 1
            sources = []
            for pdf_index, (source_file, img) in enumerate(
                load_page_source(str(path)), start=1
            ):
                if pdf_index < start_page:
                    continue
                sources.append((source_file, img, pdf_index))

        if options["limit"]:
            sources = sources[: options["limit"]]

        slots = slots_per_page(book)
        # numbering base = the book's first puzzle page (stored in the
        # calibration profile); page-number-start only selects where the
        # import begins
        profile = getattr(book, "calibration", None)
        first_page = (profile.data or {}).get("first_page")
        if first_page is None:
            first_page = options["page_number_start"] or 1
        # printed-number books must never get a positional guess (their
        # pages hold a varying number of diagrams — the arithmetic is wrong)
        if (profile.data or {}).get("numbering", {}).get("mode") == "printed":
            first_page = None

        def number_start_for(page_number):
            if first_page is None or slots is None or page_number is None:
                return None
            return (page_number - first_page) * slots + 1

        try:
            from books import ocr

            with ocr.OcrSession() as session:
                ocr.set_session(session)
                created, updated, skipped = self._process(
                    options, book, sources, number_start_for
                )
            ocr.set_session(None)
        except CalibrationError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(
            self.style.SUCCESS(
                f"import done: created={created} updated={updated} skipped={skipped}"
            )
        )

    def _process(self, options, book, sources, number_start_for):
        profile = getattr(book, "calibration", None)
        ignore_pages = set((profile.data or {}).get("ignore_pages", []))
        total_created = total_updated = total_skipped = total_failed = 0
        for i, (source_file, img, page_number) in enumerate(sources, start=1):
            if page_number in ignore_pages:
                self.stdout.write(f"skip {source_file} (page in ignore list)")
                logger.info("skip %s (page %d in ignore list)", source_file, page_number)
                continue
            if options["skip_existing"] and PageImage.objects.filter(
                book=book, source_file=source_file
            ).exists():
                self.stdout.write(f"skip {source_file} (already imported)")
                logger.info("skip %s (already imported)", source_file)
                continue
            if options["dry_run"]:
                from books.cv import extract_page
                from books.services import get_profile

                results = extract_page(img, get_profile(book))
                review = sum(1 for r in results if r.confidence < 0.8 or not r.legal)
                self.stdout.write(
                    f"dry run {i}/{len(sources)} {source_file}: "
                    f"{len(results)} diagram(s), {review} for review"
                )
                continue
            logger.info("processing %s (%d/%d)", source_file, i, len(sources))
            try:
                page, results, counts = process_page(
                    book, img, source_file=source_file,
                    page_number=page_number, force=options["force"],
                    review_threshold=options["review_threshold"],
                    number_start=number_start_for(page_number),
                )
            except Exception as exc:  # noqa: BLE001 - one bad page must not kill the batch
                total_failed += 1
                logger.exception("FAILED %s: %s", source_file, exc)
                self.stderr.write(f"FAILED {source_file}: {exc}")
                continue
            created, updated, skipped = counts
            total_created += created
            total_updated += updated
            total_skipped += skipped
            message = (
                f"processed {i}/{len(sources)} {source_file}: "
                f"{len(results)} diagram(s) "
                f"(created={created} updated={updated} skipped={skipped})"
            )
            self.stdout.write(message)
            logger.info(message)
        logger.info(
            "batch done: created=%d updated=%d skipped=%d failed=%d",
            total_created, total_updated, total_skipped, total_failed,
        )
        return total_created, total_updated, total_skipped
