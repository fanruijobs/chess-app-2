import json
from pathlib import Path

import pytest
import responses

from chessdata.models import Game, Player
from chessdata.services.chesscom import (
    HiddenProfileError,
    PlayerNotFoundError,
    normalize_result,
    sync_player,
)

pytestmark = pytest.mark.django_db

FIXTURES = Path(__file__).parent / "fixtures"
ARCHIVES_URL = "https://api.chess.com/pub/player/testuser/games/archives"
JULY_URL = "https://api.chess.com/pub/player/testuser/games/2026/07"
AUGUST_URL = "https://api.chess.com/pub/player/testuser/games/2026/08"


def load(name):
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture
def mocked_api():
    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        rsps.add(responses.GET, ARCHIVES_URL, json=load("archives.json"), status=200)
        rsps.add(responses.GET, JULY_URL, json=load("games_2026_07.json"), status=200)
        rsps.add(responses.GET, AUGUST_URL, json=load("games_2026_08.json"), status=200)
        yield rsps


def test_sync_stores_games_and_skips_variants(mocked_api):
    report = sync_player("TestUser", months=1)  # case-insensitive username

    # 6 games in the archive; the crazyhouse one is skipped by default
    assert report.created == 5
    assert report.skipped == 1
    assert Game.objects.count() == 5
    assert Game.objects.filter(rules="crazyhouse").count() == 0
    assert Player.objects.get().username == "testuser"


def test_sync_rated_only(mocked_api):
    report = sync_player("TestUser", months=1, rated_only=True)
    assert report.created == 4  # unrated game also skipped
    assert Game.objects.filter(rated=False).count() == 0


def test_sync_upsert_is_idempotent(mocked_api):
    sync_player("TestUser", months=1)
    report = sync_player("TestUser", months=1)

    assert report.created == 0
    assert report.updated == 5
    assert Game.objects.count() == 5


def test_sync_result_and_side_normalization(mocked_api):
    sync_player("TestUser", months=1)

    by_uuid = {g.uuid: g for g in Game.objects.all()}
    assert by_uuid["aaaa0001"].result == "win" and by_uuid["aaaa0001"].player_side == "w"
    assert by_uuid["aaaa0002"].result == "win" and by_uuid["aaaa0002"].player_side == "b"
    assert by_uuid["aaaa0003"].result == "draw"
    # black was checkmated -> loss from the player's perspective
    assert by_uuid["aaaa0004"].result == "loss"
    assert by_uuid["aaaa0004"].player_result_raw == "checkmated"


def test_sync_limit_stops_early(mocked_api):
    report = sync_player("TestUser", months=1, limit=2)
    # games are ordered by end_time desc -> the two newest ones
    assert report.fetched == 2
    assert set(Game.objects.values_list("uuid", flat=True)) == {"aaaa0001", "aaaa0002"}


def test_sync_dry_run_writes_nothing(mocked_api):
    rows = []
    report = sync_player("TestUser", months=1, dry_run=True, print_row=rows.append)

    assert report.fetched == 5
    assert len(rows) == 5
    assert Player.objects.count() == 0
    assert Game.objects.count() == 0


def test_sync_hidden_profile_403(mocked_api):
    mocked_api.add(
        responses.GET,
        "https://api.chess.com/pub/player/hiddenuser/games/archives",
        status=403,
        body="{}",
    )
    with pytest.raises(HiddenProfileError):
        sync_player("hiddenuser", months=1)


def test_sync_unknown_player_404(mocked_api):
    mocked_api.add(
        responses.GET,
        "https://api.chess.com/pub/player/nobody/games/archives",
        status=404,
        body="{}",
    )
    with pytest.raises(PlayerNotFoundError):
        sync_player("nobody", months=1)


def test_sync_retries_after_429(mocked_api):
    # First request is rate-limited, retry succeeds.
    mocked_api.replace(
        responses.GET,
        ARCHIVES_URL,
        status=429,
        headers={"Retry-After": "0"},
        body="{}",
    )
    mocked_api.add(responses.GET, ARCHIVES_URL, json=load("archives.json"), status=200)
    report = sync_player("TestUser", months=1)
    assert report.created == 5


def test_normalize_result():
    assert normalize_result("win") == "win"
    assert normalize_result("resigned") == "loss"
    assert normalize_result("checkmated") == "loss"
    assert normalize_result("timeout") == "loss"
    assert normalize_result("agreed") == "draw"
    assert normalize_result("stalemate") == "draw"
    assert normalize_result("something-new") == "draw"  # unknown -> draw
