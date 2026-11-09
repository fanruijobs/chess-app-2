"""Verify printed puzzle numbers against the auto-assigned numbering.

Bootstraps digit templates from the first N pages (whose expected numbers are
known-correct), then reads the printed number above every diagram and flags
mismatches. On `--fix`, mismatched puzzles get the PRINTED number and a note.
"""

import logging

import cv2
from django.core.management.base import BaseCommand, CommandError

from books.cv.number_ocr import bootstrap_templates, read_number
from books.models import Book, PageImage, Puzzle

logger = logging.getLogger("books.cv")


class Command(BaseCommand):
    help = "Verify (and optionally fix) printed puzzle numbers."

    def add_arguments(self, parser):
        parser.add_argument("book_slug")
        parser.add_argument("--bootstrap-pages", type=int, default=20,
                            help="first N pages used to build digit templates")
        parser.add_argument("--fix", action="store_true",
                            help="set the puzzle number to the printed one on mismatch")
        parser.add_argument("--limit", type=int, default=None,
                            help="verify at most N pages")
        parser.add_argument("--verbose", action="store_true", help="debug logging")

    def handle(self, *args, **options):
        logging.getLogger("books.cv").setLevel(
            logging.DEBUG if options["verbose"] else logging.INFO
        )
        book = Book.objects.filter(slug=options["book_slug"]).first()
        if book is None:
            raise CommandError(f"No book with slug {options['book_slug']!r}.")
        profile = getattr(book, "calibration", None)
        if profile and (profile.data or {}).get("numbering", {}).get("mode") == "slots":
            raise CommandError(
                "This book uses positional slot numbering "
                "(p<page>-<slot>) — there are no printed numbers to verify."
            )

        pages = list(
            PageImage.objects.filter(book=book, page_number__isnull=False).order_by(
                "page_number"
            )
        )
        if not pages:
            raise CommandError("No imported pages with page numbers.")

        def samples(page):
            samples = []
            for puzzle in page.puzzles.filter(diagram_bbox__isnull=False):
                if not puzzle.number:
                    continue
                samples.append((page.image.path, puzzle.diagram_bbox, puzzle.number))
            return samples

        # bootstrap from the first pages
        bootstrap = pages[: options["bootstrap_pages"]]
        collected = []
        for page in bootstrap:
            gray = cv2.imread(page.image.path, cv2.IMREAD_GRAYSCALE)
            for path, bbox, expected in samples(page):
                collected.append((gray, bbox, expected))
        templates = bootstrap_templates(collected)
        self.stdout.write(
            f"bootstrapped digit templates from {len(collected)} samples: "
            f"{sorted(templates.templates)}"
        )
        if not templates.templates:
            raise CommandError("Could not bootstrap digit templates.")

        verify = pages
        if options["limit"]:
            verify = verify[: options["limit"]]
        checked = mismatches = fixed = 0
        for page in verify:
            gray = cv2.imread(page.image.path, cv2.IMREAD_GRAYSCALE)
            for puzzle in page.puzzles.filter(diagram_bbox__isnull=False):
                if not puzzle.number:
                    continue
                checked += 1
                printed = read_number(
                    gray, puzzle.diagram_bbox, templates, expected=puzzle.number
                )
                if printed is None or printed == puzzle.number:
                    continue
                mismatches += 1
                self.stdout.write(
                    f"p{page.page_number} #{puzzle.number}: printed={printed}"
                )
                if options["fix"]:
                    puzzle.number = printed
                    note = f"printed number {printed} (was {puzzle.number})"
                    puzzle.extraction_notes = (
                        (puzzle.extraction_notes + "; " if puzzle.extraction_notes else "")
                        + note
                    )
                    puzzle.save(update_fields=["number", "extraction_notes", "updated_at"])
                    fixed += 1
        self.stdout.write(
            self.style.SUCCESS(
                f"checked={checked} mismatches={mismatches} fixed={fixed}"
            )
        )
