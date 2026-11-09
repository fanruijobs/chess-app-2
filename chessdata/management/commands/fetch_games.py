import logging

from django.core.management.base import BaseCommand, CommandError

from chessdata.services.chesscom import (
    ChessComError,
    HiddenProfileError,
    PlayerNotFoundError,
    sync_player,
)


class Command(BaseCommand):
    help = "Fetch recent chess.com games for a username into the local DB."

    def add_arguments(self, parser):
        parser.add_argument("username", help="chess.com username")
        parser.add_argument(
            "--months", type=int, default=3, help="number of monthly archives to fetch (default 3)"
        )
        parser.add_argument(
            "--limit", type=int, default=None, help="stop after storing this many games"
        )
        parser.add_argument(
            "--rated-only", action="store_true", help="only store rated games"
        )
        parser.add_argument(
            "--include-variants", action="store_true",
            help="also store non-standard variants (crazyhouse, chess960, ...)",
        )
        parser.add_argument(
            "--dry-run", action="store_true",
            help="print the games that would be stored, write nothing",
        )
        parser.add_argument("--verbose", action="store_true", help="debug logging")

    def handle(self, *args, **options):
        logging.getLogger("chessdata.sync").setLevel(
            logging.DEBUG if options["verbose"] else logging.INFO
        )

        def print_row(fields):
            self.stdout.write(
                f"{fields['uuid']} | {fields['white_username']} ({fields['white_rating']}) "
                f"vs {fields['black_username']} ({fields['black_rating']}) | "
                f"{fields['result']} | {fields['end_time']:%Y-%m-%d} | {fields['url']}"
            )

        try:
            report = sync_player(
                options["username"],
                months=options["months"],
                limit=options["limit"],
                rated_only=options["rated_only"],
                include_variants=options["include_variants"],
                dry_run=options["dry_run"],
                print_row=print_row,
            )
        except PlayerNotFoundError as exc:
            raise CommandError(str(exc)) from exc
        except HiddenProfileError as exc:
            raise CommandError(str(exc)) from exc
        except ChessComError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(self.style.SUCCESS(str(report)))
        if report.messages:
            for message in report.messages:
                self.stdout.write(message)
