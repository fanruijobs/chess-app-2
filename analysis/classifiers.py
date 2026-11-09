"""Rule-based classification of turning points.

Each rule is a pure function of the decision position, the blunder move, the
engine's best move, and (optionally) the opponent's punishing reply found by a
short engine search. Rules are tried in priority order; the first match wins.
"""

from dataclasses import dataclass

import chess
import chess.engine

from analysis.turning_points import cp_norm

PIECE_VALUES = {
    chess.PAWN: 100,
    chess.KNIGHT: 320,
    chess.BISHOP: 330,
    chess.ROOK: 500,
    chess.QUEEN: 900,
    chess.KING: 0,
}
PIECE_NAMES = {
    chess.PAWN: "pawn",
    chess.KNIGHT: "knight",
    chess.BISHOP: "bishop",
    chess.ROOK: "rook",
    chess.QUEEN: "queen",
    chess.KING: "king",
}


@dataclass
class PunishingReply:
    """The opponent's best answer to the blunder, from a short engine search."""

    san: str
    move: chess.Move
    is_check: bool
    is_capture: bool
    is_mate: bool
    eval_norm: int  # pov of the player after the reply
    material_lost: int  # player material lost by the reply, >= 0


@dataclass
class RuleContext:
    board_before: chess.Board  # decision position (player to move)
    board_after: chess.Board  # after the played move
    played: chess.Move
    best: chess.Move | None  # engine best move in the before-position
    san_played: str
    san_best: str
    before_n: int  # cp_norm values
    after_n: int
    before_mate: int | None
    after_mate: int | None
    player_color: chess.Color
    reply: PunishingReply | None = None

    @property
    def drop(self) -> int:
        return self.before_n - self.after_n


def material(board: chess.Board, color: chess.Color) -> int:
    return sum(
        PIECE_VALUES[piece.piece_type]
        for piece in board.piece_map().values()
        if piece.color == color
    )


def find_punishing_reply(
    ctx: RuleContext, engine, *, depth: int = 10, time_s: float = 3.0
) -> PunishingReply | None:
    """Short search on the position after the blunder: how is it punished?"""
    board = ctx.board_after
    info = engine.analyse(board, chess.engine.Limit(depth=depth, time=time_s))
    pv = info.get("pv", [])
    if not pv:
        return None
    move = pv[0]
    # Guard against corrupting the board: python-chess push() does not
    # validate the side to move, so only accept legal replies.
    if not board.is_legal(move):
        return None
    score = info["score"].pov(ctx.player_color)
    norm = cp_norm(score.score(), score.mate())
    after_reply = board.copy()
    after_reply.push(move)
    material_lost = material(board, ctx.player_color) - material(
        after_reply, ctx.player_color
    )
    return PunishingReply(
        san=board.san(move),
        move=move,
        is_check=board.gives_check(move),
        is_capture=board.is_capture(move),
        is_mate=score.is_mate() and score.mate() < 0,
        eval_norm=norm,
        material_lost=max(0, material_lost),
    )


# --- Rules, in priority order ---


def rule_allowed_mate(ctx: RuleContext):
    if ctx.after_mate is not None and ctx.after_mate < 0:
        return (
            "allowed_mate",
            f"Your move allowed a forced mate: after {ctx.san_played} the position "
            f"is mate in {abs(ctx.after_mate)} against you.",
        )
    if ctx.reply is not None and ctx.reply.is_mate:
        return (
            "allowed_mate",
            f"Your move allowed a forced mate: {ctx.reply.san} starts an "
            "unstoppable mating attack.",
        )
    return None


def rule_missed_mate(ctx: RuleContext):
    if (
        ctx.before_mate is not None
        and ctx.before_mate > 0
        and ctx.best is not None
        and ctx.played != ctx.best
    ):
        return (
            "missed_mate",
            f"You had mate in {ctx.before_mate} but played {ctx.san_played} instead; "
            f"{ctx.san_best} would have finished the game.",
        )
    return None


def rule_hung_piece(ctx: RuleContext):
    reply = ctx.reply
    if reply is None or not reply.is_capture or reply.material_lost < 100:
        return None
    target = reply.move.to_square
    captured = ctx.board_after.piece_at(target)
    moved_piece_hung = ctx.played.to_square == target
    defenders_before = ctx.board_before.attackers(ctx.player_color, target)
    defenders_after = ctx.board_after.attackers(ctx.player_color, target)
    left_undefended = bool(defenders_before) and not defenders_after
    if not (moved_piece_hung or left_undefended):
        return None
    piece_name = PIECE_NAMES[captured.piece_type] if captured else "piece"
    if moved_piece_hung:
        text = (
            f"You played {ctx.san_played} and {reply.san} simply captures the "
            f"{piece_name} — it had no way out."
        )
    else:
        text = (
            f"Your move left the {piece_name} on {chess.square_name(target)} "
            f"undefended: {reply.san} wins it without compensation."
        )
    return ("hung_piece", text)


def rule_walked_into_check(ctx: RuleContext):
    reply = ctx.reply
    # The check either captures material outright or is a fork-style check
    # whose threat wins material (the punishing reply makes things worse).
    if reply is not None and reply.is_check and (
        reply.material_lost >= 100 or reply.eval_norm < ctx.after_n
    ):
        return (
            "walked_into_check",
            f"{reply.san} comes with check and wins material — "
            "your move walked right into it.",
        )
    return None


def rule_missed_capture(ctx: RuleContext):
    if ctx.best is None:
        return None
    if ctx.board_before.is_capture(ctx.best) and ctx.best.promotion is None:
        captured = ctx.board_before.piece_at(ctx.best.to_square)
        if captured is not None and PIECE_VALUES[captured.piece_type] >= 100:
            piece_name = PIECE_NAMES[captured.piece_type]
            return (
                "missed_capture",
                f"You missed winning a {piece_name} on "
                f"{chess.square_name(ctx.best.to_square)}: {ctx.san_best} was available, "
                f"you played {ctx.san_played} instead.",
            )
    return None


def rule_missed_opportunity(ctx: RuleContext):
    if ctx.best is None:
        return None
    if (
        not ctx.board_before.is_capture(ctx.best)
        and ctx.best.promotion is None
        and ctx.drop >= 200
    ):
        return (
            "missed_opportunity",
            f"You played {ctx.san_played}, but the position called for {ctx.san_best}. "
            f"Your advantage evaporated ({ctx.drop} centipawns).",
        )
    return None


def rule_promotion_missed(ctx: RuleContext):
    if ctx.best is not None and ctx.best.promotion is not None:
        return (
            "promotion_missed",
            f"You missed promoting your pawn with {ctx.san_best}; "
            f"{ctx.san_played} let the opportunity slip.",
        )
    return None


def rule_unspecified_blunder(ctx: RuleContext):
    return (
        "unspecified_blunder",
        f"Your evaluation dropped by {ctx.drop} centipawns after {ctx.san_played}.",
    )


RULES = [
    (10, rule_allowed_mate),
    (20, rule_missed_mate),
    (30, rule_hung_piece),
    (40, rule_walked_into_check),
    (50, rule_missed_capture),
    (60, rule_missed_opportunity),
    (70, rule_promotion_missed),
    (100, rule_unspecified_blunder),
]


def classify(ctx: RuleContext) -> tuple[str, str]:
    """Return (label, rule_text) from the first rule that fires."""
    for _priority, rule in RULES:
        result = rule(ctx)
        if result is not None:
            return result
    raise AssertionError("fallback rule must always fire")
