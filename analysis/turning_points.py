"""Turning-point detection: pure functions over the per-move eval curve."""

from dataclasses import dataclass

# Mate scores are mapped into centipawn space so any forced mate dwarfs any
# material evaluation, while "mate in 1" still beats "mate in 3".
MATE_CP = 100_000

# Turning-point rule: only a mistake from an equal-or-better position that
# costs more than 2.0 pawns counts ("equal" = within +/-1.5, "winning" = +3
# and up), and only the FIRST such mistake per game is picked.
EQUAL_CP = 150
DROP_CP = 200


def cp_norm(cp: int | None, mate: int | None) -> int:
    """Normalize (cp, mate) into one signed, comparable value.

    Positive = good for the tracked player, negative = bad. For a mate score,
    mate > 0 means the player forces mate in N moves; mate < 0 means the player
    is being mated in N moves.
    """
    if mate is None:
        return cp
    return (MATE_CP - 10 * abs(mate)) * (1 if mate > 0 else -1)


@dataclass(frozen=True)
class EvalPoint:
    """Evaluation of the position after one ply, from the player's perspective."""

    ply: int
    san: str  # the move that was just played
    fen: str  # position after the move
    side_to_move: str  # side that just moved ("w"/"b")
    cp: int | None
    mate: int | None
    pv_san: str = ""  # engine best move for the side to move, "" when unavailable

    @property
    def norm(self) -> int:
        return cp_norm(self.cp, self.mate)


@dataclass(frozen=True)
class TurningPointRecord:
    ply: int
    san_played: str
    san_best: str
    fen_before: str  # decision position (player to move)
    fen_after: str  # position after the blunder
    before: EvalPoint
    after: EvalPoint
    drop_cp: int


def detect_turning_points(
    evals: list[EvalPoint], user_side: str
) -> list[TurningPointRecord]:
    """Find the FIRST mistake that throws away a decent position.

    A move qualifies when the position before it was equal or better for
    the player (not worse than -1.5, i.e. >= -150 cp) and the evaluation
    dropped by more than 2.0 points (> 200 cp). At most one turning point
    per game is reported: after the first such swing the position is
    already worsened, so later mistakes are compounding, not turning.

    For the user's move at ply i, "before" is the eval at ply i-1 (opponent
    just moved, user to move) and "after" is the eval at ply i. Both are
    already normalized to the user's perspective.
    """
    for i in range(1, len(evals)):
        after = evals[i]
        if after.side_to_move != user_side:
            continue
        before = evals[i - 1]
        if before.norm < -EQUAL_CP:
            continue  # already losing — the mistake only compounds it
        drop = before.norm - after.norm
        if drop <= DROP_CP:
            continue
        return [
            TurningPointRecord(
                ply=after.ply,
                san_played=after.san,
                san_best=before.pv_san,
                fen_before=before.fen,
                fen_after=after.fen,
                before=before,
                after=after,
                drop_cp=drop,
            )
        ]
    return []
