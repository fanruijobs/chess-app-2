from datetime import datetime

from django.core.management.base import BaseCommand, CommandError

from analysis.management._common import resolve_games
from analysis.pdf import render_game_pdf
from chessdata.models import Game


def _parse_date(value: str) -> datetime:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise CommandError(f"Invalid date {value!r}, expected YYYY-MM-DD.") from exc


class Command(BaseCommand):
    help = (
        "Export turning points of games as a printable PDF (6 positions per "
        "A4 page, 2 columns x 3 rows). Target is a chess.com username or "
        "comma-separated game uuids."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "target", help="username (e.g. magnuscarlsen) or uuid1,uuid2,..."
        )
        parser.add_argument("--lost-only", action="store_true", help="only lost games")
        parser.add_argument(
            "--time-class",
            default=None,
            help="only these game types (comma-separated: bullet,blitz,rapid,daily)",
        )
        parser.add_argument(
            "--from", dest="from_date", metavar="YYYY-MM-DD", default=None,
            help="only games played on or after this date",
        )
        parser.add_argument(
            "--to", dest="to_date", metavar="YYYY-MM-DD", default=None,
            help="only games played on or before this date",
        )
        parser.add_argument(
            "--answers", choices=("inline", "end", "none"), default="inline",
            help="inline: answers under each position (default); "
            "end: positions only, one answer key after all positions; "
            "none: positions only, no answers at all",
        )
        parser.add_argument(
            "--masked", action="store_true",
            help="deprecated alias for --answers end",
        )
        parser.add_argument(
            "--out",
            default=None,
            help="output file (default {target}_turning_points.pdf; '-' for stdout)",
        )

    def handle(self, *args, **options):
        games = resolve_games(options["target"])
        if options["lost_only"]:
            games = games.filter(result=Game.Result.LOSS)
        if options["time_class"]:
            classes = [c.strip() for c in options["time_class"].split(",")]
            games = games.filter(time_class__in=classes)
        if options["from_date"]:
            games = games.filter(end_time__date__gte=_parse_date(options["from_date"]))
        if options["to_date"]:
            games = games.filter(end_time__date__lte=_parse_date(options["to_date"]))
        games = list(games)

        if not games:
            raise CommandError("No games found for this target/filter.")

        answers = "end" if options["masked"] else options["answers"]
        pdf_bytes = render_game_pdf(games, answers=answers)
        suffix = "" if answers == "inline" else f"_{answers}"
        out = options["out"] or f"{options['target']}_turning_points{suffix}.pdf"
        if out == "-":
            self.stdout.buffer.write(pdf_bytes)
        else:
            with open(out, "wb") as f:
                f.write(pdf_bytes)
            self.stdout.write(
                self.style.SUCCESS(
                    f"Wrote {len(games)} games to {out} ({len(pdf_bytes)} bytes)."
                )
            )
