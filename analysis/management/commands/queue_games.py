import logging

from django.core.management.base import BaseCommand, CommandError

from analysis.management._common import resolve_games
from analysis.profiles import estimated_seconds_per_game, format_duration
from analysis.queue import enqueue_queryset
from chessdata.models import Game

logger = logging.getLogger("analysis.queue")


class Command(BaseCommand):
    help = (
        "Enqueue games for the analysis worker. "
        "Target is a chess.com username or comma-separated game uuids. "
        "Without --depth/--threshold, each game gets its time-class profile "
        "(bullet 14/250, blitz 16/200, rapid 18/150, daily 20/120)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "target", help="username (e.g. fanrui89) or uuid1,uuid2,..."
        )
        parser.add_argument("--lost-only", action="store_true", help="only lost games")
        parser.add_argument(
            "--time-class",
            default=None,
            help="only these game types (comma-separated: bullet,blitz,rapid,daily)",
        )
        parser.add_argument(
            "--depth", type=int, default=None,
            help="override profile depth for all games",
        )
        parser.add_argument(
            "--threshold", type=int, default=None,
            help="override profile threshold for all games",
        )
        parser.add_argument(
            "--priority", type=int, default=0, help="higher runs first"
        )
        parser.add_argument("--verbose", action="store_true", help="debug logging")

    def handle(self, *args, **options):
        logging.getLogger("analysis.queue").setLevel(
            logging.DEBUG if options["verbose"] else logging.INFO
        )
        games = resolve_games(options["target"])
        if options["lost_only"]:
            games = games.filter(result=Game.Result.LOSS)
        if options["time_class"]:
            classes = [c.strip() for c in options["time_class"].split(",")]
            games = games.filter(time_class__in=classes)
        games = games.order_by("-end_time")

        games = list(games)
        if not games:
            raise CommandError("No games match this filter.")

        created, skipped = enqueue_queryset(
            Game.objects.filter(uuid__in=[g.uuid for g in games]),
            depth=options["depth"],
            threshold_cp=options["threshold"],
            priority=options["priority"],
        )

        total_eta = sum(estimated_seconds_per_game(g) for g in games[:created])
        self.stdout.write(
            self.style.SUCCESS(
                f"enqueued={created} skipped={skipped} "
                f"(skipped = already queued/running) "
                f"estimated_time={format_duration(total_eta)}"
            )
        )
        self.stdout.write(
            "Start the worker with: .venv/bin/python manage.py analyze_worker"
        )
