"""Per-time-class analysis profiles and ETA estimation."""

from django.conf import settings

TIME_CLASSES = ("bullet", "blitz", "rapid", "daily")

# Rough average game length per time class (plies), used for ETA estimates
# before a game has been analyzed.
AVG_PLIES = {"bullet": 60, "blitz": 70, "rapid": 80, "daily": 90}
DEFAULT_AVG_PLIES = 80

# Measured ~1.05 s/ply at depth 18 on this machine (4 threads, Apple Silicon).
# Scaling heuristic: every depth step multiplies per-ply cost by ~1.15.
BASE_DEPTH = 18
BASE_SECONDS_PER_PLY = 1.05
DEPTH_COST_FACTOR = 1.15


def resolve_profile(time_class: str) -> dict:
    """Return {'depth': int, 'threshold_cp': int} for a game's time class."""
    profile = settings.ANALYSIS_PROFILES.get(time_class or "")
    return profile or settings.DEFAULT_ANALYSIS_PROFILE


def estimated_seconds_per_game(game) -> float:
    """Heuristic runtime estimate for analyzing one game at its profile depth."""
    depth = resolve_profile(game.time_class)["depth"]
    plies = AVG_PLIES.get(game.time_class, DEFAULT_AVG_PLIES)
    per_ply = BASE_SECONDS_PER_PLY * DEPTH_COST_FACTOR ** (depth - BASE_DEPTH)
    return plies * per_ply


def format_duration(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60}s"
    return f"{seconds // 3600}h {(seconds % 3600) // 60}m"
