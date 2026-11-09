import json
from pathlib import Path

import pytest
import responses
from django.utils import timezone

from analysis.models import AnalysisJob, AnalysisRun, TurningPoint
from chessdata.models import Game, Player

pytestmark = pytest.mark.django_db

FIXTURES = Path(__file__).parent.parent.parent / "chessdata/tests/fixtures"
ARCHIVES_URL = "https://api.chess.com/pub/player/testuser/games/archives"
AUGUST_URL = "https://api.chess.com/pub/player/testuser/games/2026/08"

PGN = (
    '[Event "Live Chess"]\n[White "TestUser"]\n[Black "opponent"]\n[Result "0-1"]\n\n'
    "1. e4 e5 2. Nf3 Nc6 3. Bc4 Bc5 4. Nxe5 Nxe5 0-1"
)


@pytest.fixture
def analyzed_game():
    player = Player.objects.create(username="testuser")
    game = Game.objects.create(
        uuid="viewtest1",
        player=player,
        white_username="TestUser",
        black_username="opponent",
        player_side="w",
        player_result_raw="resigned",
        result=Game.Result.LOSS,
        pgn=PGN,
        end_time=timezone.now(),
        url="https://www.chess.com/game/live/viewtest",
        time_class="blitz",
    )
    run = AnalysisRun.objects.create(
        game=game, engine_name="Stockfish", engine_version="18",
        depth=18, threshold_cp=150, status=AnalysisRun.RunStatus.DONE,
    )
    game.last_run = run
    game.analyzed_at = timezone.now()
    game.save(update_fields=["last_run", "analyzed_at"])
    # a small fake eval curve
    for ply, san, cp in [
        (1, "e4", 30), (2, "e5", 25), (3, "Nf3", 20), (4, "Nc6", 15),
        (5, "Bc4", 10), (6, "Bc5", 5), (7, "Nxe5", -250), (8, "Nxe5", -300),
    ]:
        game.evals.create(ply=ply, san=san, fen="fen", side_to_move="w" if ply % 2 else "b", eval_cp=cp)
    TurningPoint.objects.create(
        game=game, run=run, ply=7, move_number="4. Nxe5",
        san_played="Nxe5", san_best="d3",
        fen_before="fen-before", fen_after="fen-after",
        eval_before_cp=5, eval_after_cp=-250, drop_cp=255,
        classification=TurningPoint.Classification.HUNG_PIECE,
        rule_text="You played Nxe5 and Nxe5 simply captures the knight.",
    )
    return player, game


def test_players_list(client):
    Player.objects.create(username="testuser")
    response = client.get("/")
    assert response.status_code == 200
    assert b"testuser" in response.content
    assert b"Fetch games from chess.com" in response.content


def test_views_require_login():
    from django.test import Client as FreshClient

    client = FreshClient()  # anonymous — the autouse fixture only logs in the shared client
    for url in ("/", "/players/testuser/", "/games/some-uuid/", "/games/some-uuid/pdf/"):
        response = client.get(url)
        assert response.status_code == 302, url
        assert response.headers["Location"].startswith("/accounts/login/"), url


def test_players_detail_filters(client, analyzed_game):
    player, _ = analyzed_game
    Game.objects.create(
        uuid="viewtest2", player=player,
        white_username="TestUser", black_username="opponent2",
        player_side="w", player_result_raw="win", result=Game.Result.WIN,
        pgn=PGN, end_time=timezone.now(), url="https://example.com",
    )
    response = client.get(f"/players/{player.username}/?result=loss&analyzed=1")
    assert response.status_code == 200
    content = response.content.decode()
    assert "viewtest1" in content
    assert "viewtest2" not in content


def test_players_detail_groups_games_by_date(client, analyzed_game):
    import datetime

    player, game = analyzed_game
    same_day = Game.objects.create(
        uuid="viewtest-sameday", player=player,
        white_username="TestUser", black_username="opponent3",
        player_side="w", player_result_raw="win", result=Game.Result.WIN,
        pgn=PGN, end_time=game.end_time + datetime.timedelta(hours=2),
        url="https://example.com",
    )
    other_day = Game.objects.create(
        uuid="viewtest-otherday", player=player,
        white_username="TestUser", black_username="opponent4",
        player_side="w", player_result_raw="win", result=Game.Result.WIN,
        pgn=PGN, end_time=game.end_time - datetime.timedelta(days=3),
        url="https://example.com",
    )
    response = client.get(f"/players/{player.username}/")
    content = response.content.decode()
    assert content.count('class="date-group"') == 2  # two distinct play dates
    assert "viewtest-sameday" in content
    assert "viewtest-otherday" in content
    # the same-day game is grouped with the fixture game (3 games, 2 groups)
    assert content.count("game") > 0


def test_game_detail_board_and_chart(client, analyzed_game):
    _, game = analyzed_game
    response = client.get(f"/games/{game.uuid}/")
    assert response.status_code == 200
    content = response.content.decode()
    assert "<svg" in content  # board rendered server-side
    assert "evals-data" in content  # chart payload via json_script
    chart_data = json.loads(
        response.context["chart_data_json"]
    )
    assert chart_data[6]["isTurningPoint"] is True
    assert chart_data[6]["y"] == -250
    assert "4. Nxe5" in content
    assert "Hung a piece" in content
    assert "chart.umd.js" in content


def test_game_detail_move_navigation(client, analyzed_game):
    _, game = analyzed_game
    response = client.get(f"/games/{game.uuid}/", {"move": 3})
    assert response.status_code == 200
    assert response.context["current_ply"] == 3
    # clamping
    response = client.get(f"/games/{game.uuid}/", {"move": 999})
    assert response.context["current_ply"] == 8


def test_players_fetch_posts(client):
    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        rsps.add(
            responses.GET, ARCHIVES_URL,
            json=json.loads((FIXTURES / "archives.json").read_text()), status=200,
        )
        rsps.add(
            responses.GET, AUGUST_URL,
            json=json.loads((FIXTURES / "games_2026_08.json").read_text()), status=200,
        )
        response = client.post(
            "/players/fetch/", {"username": "TestUser", "months": "1", "limit": ""}
        )
    assert response.status_code == 302
    assert Player.objects.filter(username="testuser").exists()
    assert Game.objects.count() == 5  # crazyhouse filtered


def test_tp_llm_generates(client, analyzed_game, monkeypatch):
    _, game = analyzed_game
    tp = game.turning_points.get()

    from analysis.tests.test_llm import FakeClient

    monkeypatch.setattr("analysis.llm.get_client", lambda: FakeClient())
    response = client.post(f"/games/{game.uuid}/turning-points/{tp.pk}/llm/")
    assert response.status_code == 302
    tp.refresh_from_db()
    assert tp.llm_status == TurningPoint.LlmStatus.DONE
    assert "undefended" in tp.llm_text


def test_queue_post_player_uses_profiles(client, analyzed_game):
    player, game = analyzed_game
    response = client.post(
        f"/players/{player.username}/", {"action": "queue", "lost_only": "on"}
    )
    assert response.status_code == 302
    job = AnalysisJob.objects.get(game=game)
    assert job.status == AnalysisJob.JobStatus.QUEUED
    assert (job.depth, job.threshold_cp) == (16, 200)  # default blitz profile


def test_queue_post_game_dedupe(client, analyzed_game):
    _, game = analyzed_game
    for _ in range(2):
        response = client.post(f"/games/{game.uuid}/", {"action": "queue"})
        assert response.status_code == 302
    assert AnalysisJob.objects.filter(game=game).count() == 1


def test_players_list_shows_worker_status(client, analyzed_game):
    response = client.get("/")
    assert b"worker offline" in response.content


def test_export_post_returns_pdf(client, analyzed_game):
    player, _ = analyzed_game
    response = client.post(
        f"/players/{player.username}/", {"action": "export", "lost_only": "on"}
    )
    assert response.status_code == 200
    assert response.headers["Content-Type"] == "application/pdf"
    assert b"".join(response.streaming_content).startswith(b"%PDF")


def test_export_post_date_range_filters(client, analyzed_game):
    import datetime

    player, game = analyzed_game
    Game.objects.create(
        uuid="viewtest-outside-range", player=player,
        white_username="TestUser", black_username="opponent2",
        player_side="w", player_result_raw="win", result=Game.Result.WIN,
        pgn=PGN, end_time=game.end_time - datetime.timedelta(days=30),
        url="https://example.com",
    )
    # game list respects the date range
    response = client.get(
        f"/players/{player.username}/",
        {"from": (game.end_time - datetime.timedelta(days=1)).strftime("%Y-%m-%d")},
    )
    content = response.content.decode()
    assert "viewtest1" in content
    assert "viewtest-outside-range" not in content
    # export too
    response = client.post(
        f"/players/{player.username}/",
        {"action": "export", "from": (game.end_time - datetime.timedelta(days=1)).strftime("%Y-%m-%d")},
    )
    assert response.status_code == 200
    assert response.headers["Content-Type"] == "application/pdf"
    assert b"".join(response.streaming_content).startswith(b"%PDF")


def test_analysis_board_renders(client):
    response = client.get("/analysis/")
    assert response.status_code == 200
    content = response.content.decode()
    assert "Analysis board" in content
    assert "chessground.min.js" in content
    assert "chess.js" in content
    assert "cdn.jsdelivr.net/npm/stockfish.js" in content
    assert "books/vendor/stockfish.js" in content
    assert "From start" in content
    assert "Custom position" in content
    assert "Apply FEN" in content
    # the nav now links to the board
    assert 'href="/analysis/"' in content


def test_analysis_board_requires_login():
    from django.test import Client as FreshClient

    client = FreshClient()
    response = client.get("/analysis/")
    assert response.status_code == 302
    assert response.headers["Location"].startswith("/accounts/login/")
