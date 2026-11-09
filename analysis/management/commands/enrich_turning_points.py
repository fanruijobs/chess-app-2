import logging

from django.core.management.base import BaseCommand, CommandError

from analysis.llm import LlmConfigError, enrich_tp
from analysis.management._common import resolve_turning_points
from analysis.models import TurningPoint


class Command(BaseCommand):
    help = (
        "Generate DeepSeek LLM explanations for turning points. "
        "Target is a chess.com username or comma-separated game uuids."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "target", help="username (e.g. magnuscarlsen) or uuid1,uuid2,..."
        )
        parser.add_argument(
            "--force", action="store_true", help="regenerate existing explanations"
        )
        parser.add_argument("--limit", type=int, default=None, help="enrich at most N")
        parser.add_argument("--verbose", action="store_true", help="debug logging")

    def handle(self, *args, **options):
        logging.getLogger("analysis.llm").setLevel(
            logging.DEBUG if options["verbose"] else logging.INFO
        )
        try:
            turning_points = resolve_turning_points(options["target"])
        except ValueError as exc:
            raise CommandError(str(exc)) from exc

        if not options["force"]:
            turning_points = turning_points.exclude(
                llm_status=TurningPoint.LlmStatus.DONE
            )
        if options["limit"] is not None:
            turning_points = turning_points[: options["limit"]]
        turning_points = list(turning_points)

        if not turning_points:
            self.stdout.write("No turning points to enrich.")
            return

        done = failed = 0
        try:
            for tp in turning_points:
                enriched = enrich_tp(tp, force=options["force"])
                if enriched is None:
                    continue
                if enriched.llm_status == TurningPoint.LlmStatus.DONE:
                    done += 1
                    self.stdout.write(
                        f"explained {tp.game.uuid[:8]} {tp.move_number} "
                        f"({tp.san_played}) [{tp.llm_model}]"
                    )
                else:
                    failed += 1
                    self.stderr.write(
                        f"FAILED {tp.game.uuid[:8]} {tp.move_number}: {tp.llm_error[:200]}"
                    )
        except LlmConfigError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(self.style.SUCCESS(f"explained={done} failed={failed}"))
