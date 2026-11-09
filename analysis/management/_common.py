"""Shared helpers for analysis management commands (not a command itself)."""

from analysis.models import TurningPoint
from chessdata.models import Game


def resolve_games(target: str):
    """A username or a comma-separated list of game uuids -> Game queryset."""
    if "," in target:
        uuids = [u.strip() for u in target.split(",") if u.strip()]
        return Game.objects.filter(uuid__in=uuids)
    return Game.objects.filter(player__username=target.lower())


def resolve_turning_points(target: str):
    """A username or a comma-separated list of game uuids -> TurningPoint queryset."""
    if "," in target:
        uuids = [u.strip() for u in target.split(",") if u.strip()]
        return TurningPoint.objects.filter(game__uuid__in=uuids).select_related("game")
    return TurningPoint.objects.filter(
        game__player__username=target.lower()
    ).select_related("game")
