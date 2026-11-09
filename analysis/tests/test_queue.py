import pytest
from django.core.management import call_command
from django.utils import timezone

from analysis.models import AnalysisJob, AnalysisRun, WorkerHeartbeat
from analysis.queue import (
    beat_heartbeat,
    claim_next_job,
    enqueue_games,
    reset_stale_running,
    stop_heartbeat,
    worker_alive,
)
from chessdata.models import Game, Player

pytestmark = pytest.mark.django_db


@pytest.fixture
def player():
    return Player.objects.create(username="qtest")


@pytest.fixture
def game(player):
    def make(uuid, time_class="blitz"):
        return Game.objects.create(
            uuid=uuid, player=player,
            white_username="A", black_username="B", player_side="w",
            player_result_raw="resigned", result=Game.Result.LOSS,
            pgn="1. e4 1-0", end_time=timezone.now(), url="https://example.com",
            time_class=time_class,
        )

    return make


def test_enqueue_dedupe(game):
    g = game("q1", "blitz")
    assert enqueue_games([g]) == (1, 0)
    assert enqueue_games([g]) == (0, 1)  # active job already exists
    assert AnalysisJob.objects.count() == 1


def test_profiles_applied(game):
    created, _ = enqueue_games(
        [game("q2", "bullet"), game("q3", "blitz"), game("q4", "rapid"), game("q5", "daily")]
    )
    assert created == 4
    expected = {"q2": (14, 250), "q3": (16, 200), "q4": (18, 150), "q5": (20, 120)}
    for uuid, (depth, threshold) in expected.items():
        job = AnalysisJob.objects.get(game_id=uuid)
        assert (job.depth, job.threshold_cp) == (depth, threshold)


def test_explicit_override(game):
    enqueue_games([game("q6", "blitz")], depth=10, threshold_cp=50)
    job = AnalysisJob.objects.get(game_id="q6")
    assert (job.depth, job.threshold_cp) == (10, 50)


def test_claim_priority_then_fifo(game):
    for uuid, prio in [("qa", 0), ("qb", 1), ("qc", 0)]:
        AnalysisJob.objects.create(
            game=game(uuid), depth=18, threshold_cp=150, priority=prio
        )
    assert claim_next_job().game_id == "qb"  # highest priority first
    assert claim_next_job().game_id == "qa"  # FIFO within priority
    assert claim_next_job().game_id == "qc"
    assert claim_next_job() is None


def test_reset_stale_running(game):
    job = AnalysisJob.objects.create(
        game=game("q7"), depth=18, threshold_cp=150, status="running",
        started_at=timezone.now() - timezone.timedelta(hours=1),
    )
    assert reset_stale_running() == 1
    job.refresh_from_db()
    assert job.status == "queued"
    assert job.started_at is None


def test_worker_alive_lifecycle():
    assert worker_alive() is False
    beat_heartbeat()
    assert worker_alive() is True
    WorkerHeartbeat.objects.filter(pk=1).update(
        last_beat_at=timezone.now() - timezone.timedelta(minutes=5)
    )
    assert worker_alive() is False
    stop_heartbeat()
    assert worker_alive() is False


def test_worker_once_processes_jobs(game, monkeypatch):
    job = AnalysisJob.objects.create(game=game("q8"), depth=8, threshold_cp=150)

    processed = []

    def fake_analyze(game, engine, *, depth, threshold_cp, force):
        processed.append((game.uuid, depth, threshold_cp))
        return AnalysisRun.objects.create(
            game=game, engine_name="fake", engine_version="",
            depth=depth, threshold_cp=threshold_cp,
            status=AnalysisRun.RunStatus.DONE, total_plies=2,
        )

    class FakeEngineManager:
        def __init__(self, threads=4):
            pass

        def __enter__(self):
            return object()

        def __exit__(self, *exc_info):
            return False

    monkeypatch.setattr(
        "analysis.management.commands.analyze_worker.EngineManager", FakeEngineManager
    )
    monkeypatch.setattr(
        "analysis.management.commands.analyze_worker.analyze_game", fake_analyze
    )

    call_command("analyze_worker", once=True)

    job.refresh_from_db()
    assert job.status == "done"
    assert processed == [("q8", 8, 150)]
    assert worker_alive() is False  # heartbeat cleaned up on exit
