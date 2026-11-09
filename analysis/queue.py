"""Analysis queue: enqueue, claim, stale-reset, and worker heartbeat."""

import logging
import os

from django.db import IntegrityError, transaction
from django.utils import timezone

from analysis.models import AnalysisJob, WorkerHeartbeat
from analysis.profiles import resolve_profile
from chessdata.models import Game

logger = logging.getLogger("analysis.queue")

STALE_RUNNING_SECONDS = 600  # a "running" job older than this is re-queued


def enqueue_games(
    games, *, depth=None, threshold_cp=None, priority=0, use_profiles=True
) -> tuple[int, int]:
    """Enqueue games for analysis. Returns (created, skipped).

    Without explicit depth/threshold_cp, each game gets its time-class
    profile (bullet/blitz/rapid/daily defaults).
    """
    created = skipped = 0
    for game in games:
        if use_profiles and (depth is None or threshold_cp is None):
            profile = resolve_profile(game.time_class)
            job_depth = depth if depth is not None else profile["depth"]
            job_threshold = (
                threshold_cp if threshold_cp is not None else profile["threshold_cp"]
            )
        else:
            job_depth = depth
            job_threshold = threshold_cp
        try:
            with transaction.atomic():
                AnalysisJob.objects.create(
                    game=game,
                    depth=job_depth,
                    threshold_cp=job_threshold,
                    priority=priority,
                )
            created += 1
        except IntegrityError:
            skipped += 1  # an active job for this game already exists
    return created, skipped


def enqueue_queryset(games_queryset, **kwargs) -> tuple[int, int]:
    """Enqueue a queryset (ordered by id for a stable row order)."""
    return enqueue_games(games_queryset.order_by("uuid"), **kwargs)


def claim_next_job() -> AnalysisJob | None:
    """Atomically claim the highest-priority queued job (FIFO within priority)."""
    with transaction.atomic():
        job = (
            AnalysisJob.objects.select_related("game")
            .filter(status=AnalysisJob.JobStatus.QUEUED)
            .order_by("-priority", "created_at")
            .select_for_update()
            .first()
        )
        if job is None:
            return None
        job.status = AnalysisJob.JobStatus.RUNNING
        job.started_at = timezone.now()
        job.save(update_fields=["status", "started_at"])
        return job


def reset_stale_running(max_age_seconds: int = STALE_RUNNING_SECONDS) -> int:
    """Re-queue jobs stuck in RUNNING (worker died mid-game).

    max_age_seconds=0 reclaims every running job (used at worker startup —
    a single-worker queue has no other consumer).
    """
    cutoff = timezone.now() - timezone.timedelta(seconds=max_age_seconds)
    stale = AnalysisJob.objects.filter(
        status=AnalysisJob.JobStatus.RUNNING, started_at__lt=cutoff
    )
    count = stale.update(status=AnalysisJob.JobStatus.QUEUED, started_at=None)
    if count:
        logger.info("Re-queued %d stale running job(s)", count)
    return count


def queue_summary() -> dict:
    """Counts per job status (for the web UI)."""
    return {
        status: AnalysisJob.objects.filter(status=status).count()
        for status, _label in AnalysisJob.JobStatus.choices
    }


# --- worker heartbeat -------------------------------------------------------


def beat_heartbeat(current_game=None) -> WorkerHeartbeat:
    heartbeat, _ = WorkerHeartbeat.objects.get_or_create(pk=1)
    heartbeat.pid = os.getpid()
    heartbeat.last_beat_at = timezone.now()
    if heartbeat.started_at is None:
        heartbeat.started_at = timezone.now()
    heartbeat.current_game = current_game
    heartbeat.save(
        update_fields=["pid", "last_beat_at", "started_at", "current_game"]
    )
    return heartbeat


def stop_heartbeat() -> None:
    WorkerHeartbeat.objects.filter(pk=1).update(last_beat_at=None, current_game=None)


def worker_alive(max_age_seconds: int = 30) -> bool:
    heartbeat = WorkerHeartbeat.objects.filter(pk=1).first()
    if heartbeat is None or heartbeat.last_beat_at is None:
        return False
    age = (timezone.now() - heartbeat.last_beat_at).total_seconds()
    return age <= max_age_seconds
