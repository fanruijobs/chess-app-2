import logging
import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from analysis.engine import EngineManager, EngineMissingError, analyze_game
from analysis.llm import LlmConfigError, enrich_tp
from analysis.management._common import resolve_games
from analysis.models import AnalysisRun, TurningPoint
from chessdata.models import Game


class Command(BaseCommand):
    help = (
        "Analyze games with Stockfish and detect turning points. "
        "Target is a chess.com username or comma-separated game uuids."
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
            "--threshold", type=int,
            default=settings.DEFAULT_TURNING_POINT_THRESHOLD_CP,
            help=f"turning-point eval drop in centipawns (default {settings.DEFAULT_TURNING_POINT_THRESHOLD_CP})",
        )
        parser.add_argument(
            "--depth", type=int, default=settings.DEFAULT_ENGINE_DEPTH,
            help=f"engine depth per position (default {settings.DEFAULT_ENGINE_DEPTH})",
        )
        parser.add_argument("--force", action="store_true", help="re-analyze analyzed games")
        parser.add_argument("--limit", type=int, default=None, help="analyze at most N games")
        parser.add_argument("--threads", type=int, default=4, help="engine threads")
        parser.add_argument(
            "--llm", action="store_true",
            help="generate DeepSeek LLM explanations for the turning points found",
        )
        parser.add_argument("--verbose", action="store_true", help="debug logging")

    def handle(self, *args, **options):
        level = logging.DEBUG if options["verbose"] else logging.WARNING
        for logger_name in ("analysis.engine", "analysis.llm"):
            logging.getLogger(logger_name).setLevel(level)
        if options["llm"] and not settings.DEEPSEEK_API_KEY:
            raise CommandError(
                "--llm needs DEEPSEEK_API_KEY in the environment (see .env.example)."
            )
        games = resolve_games(options["target"])
        if options["lost_only"]:
            games = games.filter(result=Game.Result.LOSS)
        if options["time_class"]:
            classes = [c.strip() for c in options["time_class"].split(",")]
            games = games.filter(time_class__in=classes)
        if options["limit"] is not None:
            games = games[: options["limit"]]
        games = list(games)

        if not games:
            self.stdout.write("No games to analyze.")
            return

        started = time.monotonic()
        analyzed = skipped = failed = 0
        total_turning_points = 0
        llm_done = llm_failed = 0

        try:
            with EngineManager(threads=options["threads"]) as engine:
                for i, game in enumerate(games, start=1):
                    run = analyze_game(
                        game,
                        engine,
                        depth=options["depth"],
                        threshold_cp=options["threshold"],
                        force=options["force"],
                    )
                    if run is None:
                        skipped += 1
                        continue
                    if run.status == AnalysisRun.RunStatus.FAILED:
                        failed += 1
                        self.stderr.write(
                            f"FAILED {game.uuid}: {run.error[:200]}"
                        )
                        continue
                    analyzed += 1
                    total_turning_points += run.turning_points.count()
                    if options["llm"]:
                        for tp in run.turning_points.all():
                            enriched = enrich_tp(tp)
                            if enriched is None:
                                continue
                            if enriched.llm_status == TurningPoint.LlmStatus.DONE:
                                llm_done += 1
                            else:
                                llm_failed += 1
                    elapsed = time.monotonic() - started
                    per_game = elapsed / i
                    eta = per_game * (len(games) - i)
                    self.stdout.write(
                        f"analyzed {i}/{len(games)} uuid={game.uuid[:8]} "
                        f"plies={run.total_plies} elapsed={elapsed:.0f}s eta={eta:.0f}s"
                    )
        except EngineMissingError as exc:
            raise CommandError(str(exc)) from exc
        except LlmConfigError as exc:
            raise CommandError(str(exc)) from exc

        summary = (
            f"analyzed={analyzed} skipped={skipped} failed={failed} "
            f"turning_points={total_turning_points}"
        )
        if options["llm"]:
            summary += f" llm_explanations={llm_done} llm_failures={llm_failed}"
        self.stdout.write(self.style.SUCCESS(summary))
