import logging
import os
import signal
import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from analysis.engine import EngineManager, EngineMissingError, analyze_game
from analysis.llm import LlmConfigError, enrich_tp
from analysis.models import AnalysisJob, AnalysisRun
from analysis.queue import (
    beat_heartbeat,
    claim_next_job,
    reset_stale_running,
    stop_heartbeat,
)

logger = logging.getLogger("analysis.queue")


class Command(BaseCommand):
    help = (
        "Run the analysis worker: claims queued AnalysisJobs and analyzes them "
        "with one shared Stockfish process. Runs forever until interrupted; "
        "use --once for cron-friendly single passes."
    )

    def add_arguments(self, parser):
        parser.add_argument("--threads", type=int, default=4, help="engine threads")
        parser.add_argument(
            "--once", action="store_true",
            help="process queued jobs and exit (use with cron/launchd)",
        )
        parser.add_argument(
            "--max-jobs", type=int, default=None,
            help="with --once: stop after this many jobs",
        )
        parser.add_argument(
            "--poll", type=float, default=5.0,
            help="seconds to sleep when the queue is empty",
        )
        parser.add_argument(
            "--llm", action="store_true",
            help="generate DeepSeek explanations for turning points found",
        )
        parser.add_argument("--verbose", action="store_true", help="debug logging")

    def handle(self, *args, **options):
        level = logging.DEBUG if options["verbose"] else logging.INFO
        for logger_name in ("analysis.engine", "analysis.llm", "analysis.queue"):
            logging.getLogger(logger_name).setLevel(level)
        if options["llm"] and not settings.DEEPSEEK_API_KEY:
            raise CommandError(
                "--llm needs DEEPSEEK_API_KEY in the environment (see .env.example)."
            )

        # A running job at startup is stale by definition: this is a
        # single-worker queue, so reclaim everything and mark orphaned runs.
        reset_stale_running(max_age_seconds=0)
        AnalysisRun.objects.filter(status=AnalysisRun.RunStatus.RUNNING).update(
            status=AnalysisRun.RunStatus.FAILED, error="worker interrupted"
        )
        done = failed = 0

        # Graceful stop: finish the current game, then exit (SIGINT/SIGTERM).
        self._stop = False

        def request_stop(signum, frame):
            logger.info("Stop requested (signal %s) — finishing current job.", signum)
            self._stop = True

        signal.signal(signal.SIGTERM, request_stop)
        signal.signal(signal.SIGINT, request_stop)

        try:
            with EngineManager(threads=options["threads"]) as engine:
                logger.info("Worker started (pid %d), waiting for jobs.", os.getpid())
                while not self._stop:
                    beat_heartbeat()
                    job = claim_next_job()
                    if job is None:
                        if options["once"]:
                            break
                        time.sleep(options["poll"])
                        continue

                    beat_heartbeat(current_game=job.game)
                    logger.info(
                        "Analyzing %s (depth %d, threshold %d)",
                        job.game.uuid[:8], job.depth, job.threshold_cp,
                    )
                    run = analyze_game(
                        job.game,
                        engine,
                        depth=job.depth,
                        threshold_cp=job.threshold_cp,
                        force=True,
                    )
                    if run.status == AnalysisRun.RunStatus.DONE:
                        job.status = AnalysisJob.JobStatus.DONE
                        done += 1
                        if options["llm"]:
                            for tp in run.turning_points.all():
                                enrich_tp(tp)
                    else:
                        job.status = AnalysisJob.JobStatus.FAILED
                        job.error = run.error
                        failed += 1
                        logger.error("FAILED %s: %s", job.game.uuid, run.error[:200])
                    job.finished_at = timezone.now()
                    job.save(update_fields=["status", "error", "finished_at"])
                    logger.info(
                        "Finished %s [%s] (done=%d failed=%d)",
                        job.game.uuid[:8], job.status, done, failed,
                    )
                    if (
                        options["once"]
                        and options["max_jobs"]
                        and done + failed >= options["max_jobs"]
                    ):
                        break
        except EngineMissingError as exc:
            raise CommandError(str(exc)) from exc
        except LlmConfigError as exc:
            raise CommandError(str(exc)) from exc
        finally:
            stop_heartbeat()

        logger.info("Worker stopped: done=%d failed=%d", done, failed)
