import shutil

import pytest
from django.conf import settings
from django.core.management import call_command
from django.utils import timezone

from analysis.engine import EngineManager, analyze_game
from analysis.models import AnalysisRun, TurningPoint
from chessdata.models import Game, Player

pytestmark = [pytest.mark.django_db, pytest.mark.integration]

PGN = (
    '[Event "Live Chess"]\n[White "TestUser"]\n[Black "opponent"]\n[Result "0-1"]\n\n'
    "1. e4 e5 2. Nf3 Nc6 3. Bc4 Bc5 4. Nxe5 Nxe5 0-1"
)


@pytest.fixture
def game():
    player = Player.objects.create(username="testuser")
    return Game.objects.create(
        uuid="itestgame1",
        player=player,
        white_username="TestUser",
        black_username="opponent",
        player_side="w",
        player_result_raw="resigned",
        result=Game.Result.LOSS,
        pgn=PGN,
        end_time=timezone.now(),
        url="https://www.chess.com/game/live/itest",
    )


def test_analysis_detects_turning_point(game):
    if not shutil.which(settings.STOCKFISH_PATH):
        pytest.skip("Stockfish not installed")
    with EngineManager() as engine:
        run = analyze_game(game, engine, depth=8, threshold_cp=150)

    assert run.status == AnalysisRun.RunStatus.DONE
    assert run.total_plies == 8
    assert game.evals.count() == 8
    # eval curve is pov-normalized: white is clearly losing after 4. Nxe5
    assert game.evals.get(ply=7).eval_cp < -200

    tp = TurningPoint.objects.get(game=game)
    assert tp.ply == 7
    assert tp.san_played == "Nxe5"
    assert tp.drop_cp >= 150
    assert tp.classification in TurningPoint.Classification.values

    # skip on re-run without force
    with EngineManager() as engine:
        assert analyze_game(game, engine, depth=8, threshold_cp=150) is None


def test_worker_processes_job_with_real_engine(game):
    if not shutil.which(settings.STOCKFISH_PATH):
        pytest.skip("Stockfish not installed")
    from analysis.models import AnalysisJob

    AnalysisJob.objects.create(game=game, depth=8, threshold_cp=150)
    call_command("analyze_worker", once=True)

    job = AnalysisJob.objects.get(game=game)
    assert job.status == AnalysisJob.JobStatus.DONE
    game.refresh_from_db()  # the worker wrote last_run in its own process view
    assert game.turning_points.count() == 1
    assert game.last_run.total_plies == 8
