"""Load a book's puzzles straight from a CSV of FENs (no scanning/OCR).

For books whose positions already exist in machine-readable form — e.g. the
corrected Woodpecker-2 exercise list — there is nothing to extract, so this
skips the whole CV/OCR pipeline and writes Puzzle rows directly.

The CSV needs a number column (`number` or `exercise`) and a `fen` column;
anything else (side_to_move, pdf_page, game) is ignored. Re-running is safe:
rows are matched on (book, number) and updated in place.
"""

import csv
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from books.models import Book, Puzzle

NUMBER_COLUMNS = ("number", "exercise")


class Command(BaseCommand):
    help = "Create/update a book's puzzles from a CSV of FENs."

    def add_arguments(self, parser):
        parser.add_argument("book_slug")
        parser.add_argument("csv_path")
        parser.add_argument("--title", default=None,
                            help="book title when creating the book")
        parser.add_argument("--status", default=Puzzle.Status.EXTRACTED,
                            choices=[c for c, _ in Puzzle.Status.choices],
                            help="status for new rows (default: extracted)")
        parser.add_argument("--dry-run", action="store_true",
                            help="parse and report, write nothing")

    def handle(self, *args, **options):
        path = Path(options["csv_path"])
        if not path.exists():
            raise CommandError(f"No such file: {path}")

        with path.open(newline="", encoding="utf-8-sig") as fh:
            reader = csv.DictReader(fh)
            columns = reader.fieldnames or []
            number_col = next((c for c in NUMBER_COLUMNS if c in columns), None)
            if number_col is None or "fen" not in columns:
                raise CommandError(
                    f"{path.name}: need a {'/'.join(NUMBER_COLUMNS)} column and a "
                    f"'fen' column; found {columns}"
                )
            rows = [(r[number_col].strip(), r["fen"].strip())
                    for r in reader if r[number_col].strip() and r["fen"].strip()]

        if not rows:
            raise CommandError(f"{path.name}: no usable rows.")
        numbers = [n for n, _ in rows]
        dupes = sorted({n for n in numbers if numbers.count(n) > 1})
        bad = [(n, f) for n, f in rows if not self._legal(f)]
        self.stdout.write(
            f"{path.name}: {len(rows)} rows, numbers {numbers[0]}..{numbers[-1]}"
        )
        if dupes:
            raise CommandError(f"duplicate numbers in CSV: {dupes[:10]}")
        if bad:
            raise CommandError(
                f"{len(bad)} unparseable FEN(s), first: {bad[0]}"
            )

        if options["dry_run"]:
            self.stdout.write(self.style.WARNING("dry run — nothing written."))
            return

        slug = options["book_slug"]
        with transaction.atomic():
            book = Book.objects.filter(slug=slug).first()
            if book is None:
                if not options["title"]:
                    raise CommandError(
                        f"No book {slug!r} — pass --title to create it."
                    )
                book = Book.objects.create(slug=slug, title=options["title"])
                self.stdout.write(f"created book {slug!r} ({book.title})")

            existing = {p.number: p for p in book.puzzles.all()}
            created = updated = 0
            for number, fen in rows:
                note = f"loaded from {path.name}"
                puzzle = existing.get(number)
                if puzzle is None:
                    Puzzle.objects.create(
                        book=book, number=number, fen=fen,
                        status=options["status"], confidence=1.0,
                        extraction_notes=note,
                    )
                    created += 1
                elif puzzle.fen != fen:
                    puzzle.fen = fen
                    puzzle.confidence = 1.0
                    puzzle.extraction_notes = note
                    puzzle.save(update_fields=["fen", "confidence",
                                               "extraction_notes", "updated_at"])
                    updated += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"book {book.slug!r}: {created} created, {updated} updated, "
                f"{len(rows) - created - updated} unchanged "
                f"(total now {book.puzzles.count()})"
            )
        )

    @staticmethod
    def _legal(fen: str) -> bool:
        import chess

        try:
            chess.Board(fen)
        except ValueError:
            return False
        return True
