"""Export verified puzzles as a FEN-only PGN (position setup, no mainline)."""

import io

import chess
import chess.pgn
from django.core.management.base import BaseCommand, CommandError

from books.models import Book, Puzzle


class Command(BaseCommand):
    help = (
        "Export verified puzzles as PGN. Each game carries the FEN in the "
        "setup — openable in any chess GUI. (Solution lines were removed: "
        "the training system analyzes on the fly.)"
    )

    def add_arguments(self, parser):
        parser.add_argument("book_slug", nargs="?", default=None,
                            help="book slug (default: all books)")
        parser.add_argument("--out", default=None, help="output file (default stdout)")
        parser.add_argument("--include-unverified", action="store_true",
                            help="also export extracted/review puzzles (default: verified only)")

    def handle(self, *args, **options):
        puzzles = Puzzle.objects.select_related("book", "page")
        if not options["include_unverified"]:
            puzzles = puzzles.filter(status=Puzzle.Status.VERIFIED)
        if options["book_slug"]:
            book = Book.objects.filter(slug=options["book_slug"]).first()
            if book is None:
                raise CommandError(f"No book with slug {options['book_slug']!r}.")
            puzzles = puzzles.filter(book=book)
        puzzles = list(puzzles)
        if not puzzles:
            raise CommandError("No verified puzzles to export.")

        buffer = io.StringIO()
        for puzzle in puzzles:
            game = chess.pgn.Game()
            game.headers["Event"] = puzzle.book.title
            game.headers["Site"] = (
                f"page {puzzle.page.page_number}" if puzzle.page and puzzle.page.page_number else "?"
            )
            if puzzle.number:
                game.headers["Puzzle"] = puzzle.number
            game.headers["Date"] = puzzle.created_at.strftime("%Y.%m.%d")
            game.setup(chess.Board(puzzle.fen))
            buffer.write(str(game))
            buffer.write("\n\n")

        if options["out"]:
            with open(options["out"], "w") as f:
                f.write(buffer.getvalue())
            self.stdout.write(
                self.style.SUCCESS(f"Wrote {len(puzzles)} puzzles to {options['out']}.")
            )
        else:
            self.stdout.write(buffer.getvalue())
