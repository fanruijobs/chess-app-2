"""chess.com published-data API client and sync logic.

Public endpoints, no auth needed for public games:
- GET /pub/player/{username}/games/archives -> list of monthly archive URLs
- GET <archive url> -> {"games": [{uuid, url, pgn, rated, rules, time_class,
  time_control, end_time, white: {...}, black: {...}}, ...]}

Strictly serial requests (chess.com policy: parallel requests get 429s).
"""
import io
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import chess.pgn
import requests
from django.conf import settings
from django.utils import timezone as dj_timezone

from chessdata.models import Game, Player

logger = logging.getLogger("chessdata.sync")

API_BASE = "https://api.chess.com"
MAX_ATTEMPTS = 3
BACKOFF_SECONDS = (1, 2, 4)

# Raw chess.com result strings (from the tracked player's perspective) -> Game.Result
WIN_RAW = {"win"}
LOSS_RAW = {"lose", "checkmated", "timeout", "resigned", "abandoned"}
DRAW_RAW = {
    "stalemate", "agreed", "repetition", "insufficient", "50move", "timevsinsufficient",
}


class ChessComError(Exception):
    """Base class for chess.com API errors."""


class PlayerNotFoundError(ChessComError):
    """404: no such chess.com account."""


class HiddenProfileError(ChessComError):
    """403: the player has hidden their chess.com profile."""


@dataclass
class SyncReport:
    fetched: int = 0
    created: int = 0
    updated: int = 0
    skipped: int = 0
    errors: int = 0
    messages: list[str] = field(default_factory=list)

    def __str__(self):
        return (
            f"fetched={self.fetched} created={self.created} updated={self.updated} "
            f"skipped={self.skipped} errors={self.errors}"
        )


def normalize_result(raw: str) -> str:
    """Map a chess.com result string (tracked player's perspective) to Game.Result."""
    if raw in WIN_RAW:
        return Game.Result.WIN
    if raw in LOSS_RAW:
        return Game.Result.LOSS
    if raw in DRAW_RAW:
        return Game.Result.DRAW
    logger.warning("Unknown result string %r, treating as draw", raw)
    return Game.Result.DRAW


def _new_session() -> requests.Session:
    session = requests.Session()
    session.headers["User-Agent"] = settings.CHESS_COM_USER_AGENT
    return session


def _get(session: requests.Session, url: str) -> requests.Response:
    """GET with retries: 429 honors Retry-After, 5xx/timeouts retry with backoff."""
    last_error: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = session.get(url, timeout=20)
        except requests.Timeout as exc:
            last_error = exc
            if attempt < MAX_ATTEMPTS:
                time.sleep(BACKOFF_SECONDS[attempt - 1])
            continue
        if response.status_code == 429:
            last_error = ChessComError(f"rate limited on {url}")
            retry_after = int(response.headers.get("Retry-After", "10") or 10)
            logger.warning("429 on %s, sleeping %ss", url, retry_after)
            if attempt < MAX_ATTEMPTS:
                time.sleep(retry_after)
            continue
        if 500 <= response.status_code < 600:
            last_error = ChessComError(f"server error {response.status_code} on {url}")
            if attempt < MAX_ATTEMPTS:
                time.sleep(BACKOFF_SECONDS[attempt - 1])
            continue
        return response
    raise ChessComError(f"failed after {MAX_ATTEMPTS} attempts: {url}") from last_error


def get_archives(username: str) -> list[str]:
    """Return the list of monthly archive URLs for a player, oldest first."""
    url = f"{API_BASE}/pub/player/{username.lower()}/games/archives"
    with _new_session() as session:
        response = _get(session, url)
    if response.status_code == 403:
        raise HiddenProfileError(
            f"'{username}' has hidden their chess.com profile (403)."
        )
    if response.status_code == 404:
        raise PlayerNotFoundError(f"No chess.com player named '{username}' (404).")
    response.raise_for_status()
    return list(response.json().get("archives", []))


def get_month_games(archive_url: str) -> list[dict]:
    """Return the game dicts of one monthly archive."""
    with _new_session() as session:
        response = _get(session, archive_url)
    if response.status_code == 404:
        return []  # month with no games yet
    response.raise_for_status()
    return list(response.json().get("games", []))


def _passes_filters(game: dict, *, rated_only: bool, include_variants: bool) -> bool:
    if not include_variants and game.get("rules", "chess") != "chess":
        return False
    if rated_only and not game.get("rated", True):
        return False
    return True


def _side_of(username: str, game: dict) -> str | None:
    """'w'/'b' if username played this game, else None."""
    if game["white"].get("username", "").lower() == username:
        return "w"
    if game["black"].get("username", "").lower() == username:
        return "b"
    return None


def _game_fields(username: str, game: dict) -> dict | None:
    """Build model fields for a chess.com game dict, or None if unusable."""
    side = _side_of(username, game)
    if side is None:
        return None

    # Validate the PGN; skip games python-chess can't parse.
    try:
        node = chess.pgn.read_game(io.StringIO(game.get("pgn", "")))
    except Exception as exc:  # noqa: BLE001 - any parse error means skip
        logger.warning("Skipping game %s: unparseable PGN (%s)", game.get("uuid"), exc)
        return None
    if node is None:
        logger.warning("Skipping game %s: empty PGN", game.get("uuid"))
        return None

    side_color = "white" if side == "w" else "black"
    player_result_raw = game[side_color].get("result", "draw")
    end_time = datetime.fromtimestamp(game["end_time"], tz=timezone.utc)
    return {
        "uuid": game["uuid"],
        "white_username": game["white"].get("username", "?"),
        "black_username": game["black"].get("username", "?"),
        "white_rating": game["white"].get("rating"),
        "black_rating": game["black"].get("rating"),
        "player_side": side,
        "player_result_raw": player_result_raw,
        "result": normalize_result(player_result_raw),
        "rules": game.get("rules", "chess"),
        "rated": bool(game.get("rated", True)),
        "time_class": game.get("time_class", ""),
        "time_control": game.get("time_control", ""),
        "end_time": end_time,
        "url": game.get("url", ""),
        "pgn": game.get("pgn", ""),
    }


def sync_player(
    username: str,
    *,
    months: int = 3,
    limit: int | None = None,
    rated_only: bool = False,
    include_variants: bool = False,
    dry_run: bool = False,
    print_row=None,
) -> SyncReport:
    """Sync recent games of `username` from chess.com into the DB.

    `months` selects the last N monthly archives (newest first). `limit` stops
    once that many games have been stored. Idempotent: re-syncs upsert by uuid.
    """
    username = username.lower()
    report = SyncReport()
    archives = get_archives(username)  # raises HiddenProfileError / PlayerNotFoundError
    if not archives:
        logger.info("No archives returned for %s", username)
        return report

    if months:
        archives = archives[-months:]
    archives = list(reversed(archives))  # newest first

    if not dry_run:
        player, _ = Player.objects.get_or_create(username=username)
        player.last_synced_at = dj_timezone.now()
        player.last_archive_url = archives[0]
        player.save(update_fields=["last_synced_at", "last_archive_url"])

    stored = 0
    for archive_url in archives:
        logger.info("Fetching archive %s", archive_url)
        games = get_month_games(archive_url)
        games.sort(key=lambda g: g.get("end_time", 0), reverse=True)
        for game in games:
            if not _passes_filters(
                game, rated_only=rated_only, include_variants=include_variants
            ):
                report.skipped += 1
                continue
            fields = _game_fields(username, game)
            if fields is None:
                report.errors += 1
                continue
            if not dry_run:
                fields["player"] = player
            report.fetched += 1
            stored += 1
            if dry_run:
                if print_row:
                    print_row(fields)
            else:
                _, created = Game.objects.update_or_create(uuid=fields["uuid"], defaults=fields)
                if created:
                    report.created += 1
                else:
                    report.updated += 1
            if limit is not None and stored >= limit:
                return report
    return report
