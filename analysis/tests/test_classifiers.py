"""Rule-based classifier tests on real positions built from PGNs/FENs.

The punishing-reply engine search is stubbed with a FakeEngine that returns a
scripted move + score for the position after the blunder.
"""

import io

import chess
import chess.engine
import chess.pgn
import pytest

pytestmark = pytest.mark.django_db  # needed by the autouse auth fixture

from analysis.classifiers import (
    RuleContext,
    classify,
    find_punishing_reply,
)


class FakeEngine:
    """Returns a scripted best reply and score for whatever board it gets."""

    def __init__(self, reply_uci=None, score_cp=-300, score_mate=None):
        self.reply_uci = reply_uci
        self.score_cp = score_cp
        self.score_mate = score_mate

    def analyse(self, board, limit):
        pv = [chess.Move.from_uci(self.reply_uci)] if self.reply_uci else []
        if self.score_mate is not None:
            score = chess.engine.PovScore(chess.engine.Mate(self.score_mate), board.turn)
        else:
            score = chess.engine.PovScore(chess.engine.Cp(self.score_cp), board.turn)
        return {"score": score, "pv": pv}


def board_from_pgn(pgn: str, stop_ply: int):
    """Return (board_before, board_after, played) around the move at stop_ply."""
    node = chess.pgn.read_game(io.StringIO(pgn))
    board = chess.Board()
    board_before = board_after = played = None
    for ply, move in enumerate(node.mainline_moves(), start=1):
        if ply == stop_ply:
            board_before = board.copy()
            played = move
            board.push(move)
            board_after = board.copy()
            break
        board.push(move)
    assert board_before is not None, f"ply {stop_ply} not in PGN"
    return board_before, board_after, played


def make_ctx(
    board_before,
    board_after,
    played,
    *,
    san_best="",
    before_n=0,
    after_n=-250,
    before_mate=None,
    after_mate=None,
    player_color=chess.WHITE,
    engine=None,
):
    best = board_before.parse_san(san_best) if san_best else None
    ctx = RuleContext(
        board_before=board_before,
        board_after=board_after,
        played=played,
        best=best,
        san_played=board_before.san(played),
        san_best=san_best,
        before_n=before_n,
        after_n=after_n,
        before_mate=before_mate,
        after_mate=after_mate,
        player_color=player_color,
    )
    if engine is not None:
        ctx.reply = find_punishing_reply(ctx, engine)
    return ctx


# 1. f3 e5 2. g4 — white's g4 (ply 3) allows Qh4#
FOOLS_MATE = "1. f3 e5 2. g4"


def test_allowed_mate_via_after_eval():
    board_before, board_after, played = board_from_pgn(FOOLS_MATE, stop_ply=3)
    ctx = make_ctx(board_before, board_after, played, after_mate=-1)
    label, _ = classify(ctx)
    assert label == "allowed_mate"


def test_allowed_mate_via_punishing_reply():
    board_before, board_after, played = board_from_pgn(FOOLS_MATE, stop_ply=3)
    # black to move after g4 and mates in 1 (from black's point of view)
    engine = FakeEngine(reply_uci="d8h4", score_mate=1)
    ctx = make_ctx(board_before, board_after, played, engine=engine)
    label, _ = classify(ctx)
    assert label == "allowed_mate"
    assert ctx.reply.is_mate and ctx.reply.san == "Qh4#"


def test_missed_mate():
    # 3...Nf6 walks into Qxf7# — white had mate in 1 but played Qg6 instead
    board_before, board_after, played = board_from_pgn(
        "1. e4 e5 2. Bc4 Nc6 3. Qh5 Nf6 4. Qg6", stop_ply=7
    )
    ctx = make_ctx(
        board_before, board_after, played,
        san_best="Qxf7", before_mate=1, before_n=99_990,
    )
    label, text = classify(ctx)
    assert label == "missed_mate"
    assert "mate in 1" in text


def test_hung_piece_moved_piece_captured():
    # 4. Nxe5 — black just takes the knight on e5
    pgn = "1. e4 e5 2. Nf3 Nc6 3. Bc4 Bc5 4. Nxe5"
    board_before, board_after, played = board_from_pgn(pgn, stop_ply=7)
    engine = FakeEngine(reply_uci="c6e5", score_cp=-320)
    ctx = make_ctx(board_before, board_after, played, san_best="d3", engine=engine)
    label, text = classify(ctx)
    assert label == "hung_piece"
    assert "captures the knight" in text


def test_hung_piece_left_undefended():
    # 1. e4 e5 2. d3 Nf6 3. d4 — d3->d4 stops defending e4, black plays Nxe4
    board_before, board_after, played = board_from_pgn(
        "1. e4 e5 2. d3 Nf6 3. d4", stop_ply=5
    )
    engine = FakeEngine(reply_uci="f6e4", score_cp=-150)
    ctx = make_ctx(board_before, board_after, played, san_best="Nf3", engine=engine)
    label, text = classify(ctx)
    assert label == "hung_piece"
    assert "undefended" in text


def test_walked_into_check_fork():
    # Petrov trap: after 4...Nf6?? white has 5. Nc6+!, a discovered check
    # from Qe2 that also attacks the d8 queen (black = user blunders)
    pgn = "1. e4 e5 2. Nf3 Nf6 3. Nxe5 Nxe4 4. Qe2 Nf6"
    board_before, board_after, played = board_from_pgn(pgn, stop_ply=8)
    # after 4...Nf6 it is white to move; Nc6+ wins the queen
    engine = FakeEngine(reply_uci="e5c6", score_cp=400)
    ctx = make_ctx(
        board_before, board_after, played,
        san_best="d5", after_n=-100,
        player_color=chess.BLACK, engine=engine,
    )
    assert ctx.reply.is_check
    label, text = classify(ctx)
    assert label == "walked_into_check"
    assert "Nc6+" in text


def test_missed_capture():
    # after 1. e4 e5 2. Nf3 Nc6 white can win the e5 pawn with Nxe5
    board_before, board_after, played = board_from_pgn(
        "1. e4 e5 2. Nf3 Nc6 3. d4", stop_ply=5
    )
    ctx = make_ctx(board_before, board_after, played, san_best="Nxe5")
    label, text = classify(ctx)
    assert label == "missed_capture"
    assert "pawn on e5" in text


def test_missed_opportunity():
    board_before, board_after, played = board_from_pgn(
        "1. e4 e5 2. Nf3 Nc6 3. h3", stop_ply=5
    )
    ctx = make_ctx(
        board_before, board_after, played,
        san_best="d4", before_n=40, after_n=-250,
    )
    label, _ = classify(ctx)
    assert label == "missed_opportunity"


def test_promotion_missed():
    board_before = chess.Board("4k3/P7/8/8/8/8/8/4K3 w - - 0 1")
    played = chess.Move.from_uci("e1d2")
    board_after = board_before.copy()
    board_after.push(played)
    ctx = make_ctx(
        board_before, board_after, played,
        san_best="a8=Q", before_n=500, after_n=-200,
    )
    label, _ = classify(ctx)
    assert label == "promotion_missed"


def test_unspecified_blunder_fallback():
    board_before, board_after, played = board_from_pgn(
        "1. e4 e5 2. Nf3 Nc6 3. h3", stop_ply=5
    )
    ctx = make_ctx(
        board_before, board_after, played,
        san_best="d4", before_n=0, after_n=-150,  # drop 150, quiet best
    )
    label, text = classify(ctx)
    assert label == "unspecified_blunder"
    assert "150 centipawns" in text


def test_find_punishing_reply_scores():
    board_before, board_after, played = board_from_pgn(FOOLS_MATE, stop_ply=3)
    engine = FakeEngine(reply_uci="d8h4", score_mate=1)
    ctx = make_ctx(board_before, board_after, played, engine=engine)
    assert ctx.reply.material_lost == 0  # mate, not material
    assert ctx.reply.is_mate
