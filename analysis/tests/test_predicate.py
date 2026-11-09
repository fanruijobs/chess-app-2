import pytest

from analysis.turning_points import EvalPoint, cp_norm, detect_turning_points

pytestmark = pytest.mark.django_db  # needed by the autouse auth fixture


def point(ply, san, side, cp=None, mate=None, pv_san="", fen="fen"):
    return EvalPoint(
        ply=ply, san=san, fen=fen, side_to_move=side, cp=cp, mate=mate, pv_san=pv_san
    )


def test_cp_norm_mate_ordering():
    assert cp_norm(100, None) == 100
    assert cp_norm(-100, None) == -100
    # mate in 1 (99990) > mate in 3 (99970) > any cp value
    assert cp_norm(None, 1) == 99_990
    assert cp_norm(None, 1) > cp_norm(None, 3) > 50_000
    # being mated in 2 is worse than being mated in 4, and worse than any cp
    assert cp_norm(None, -2) < cp_norm(None, -4) < -50_000


def test_detects_user_move_drop():
    evals = [
        point(1, "e4", "w", 30),
        point(2, "e5", "b", 25),
        point(3, "Nf3", "w", 20),
        point(4, "Nc6", "b", 15, pv_san="Nc3"),  # best move in the before-position
        point(5, "Nxe5", "w", -250),  # user (white) blunders
        point(6, "Nxe5", "b", -280),
    ]
    tps = detect_turning_points(evals, user_side="w")
    assert len(tps) == 1
    tp = tps[0]
    assert tp.ply == 5
    assert tp.san_played == "Nxe5"
    assert tp.san_best == "Nc3"  # best move from the before-position
    assert tp.drop_cp == 15 - (-250)
    assert tp.fen_before == "fen"  # before-position is evals[ply-1]
    assert tp.after is evals[4]  # the blunder move itself (ply 5)


def test_sub_threshold_not_detected():
    evals = [
        point(1, "e4", "w", 30),
        point(2, "e5", "b", 28),
        point(3, "Nf3", "w", 10),
    ]
    assert detect_turning_points(evals, "w") == []


def test_user_as_black_perspective():
    evals = [
        point(1, "d4", "w", 20),
        point(2, "d5", "b", 18),
        point(3, "c4", "w", 25),
        point(4, "Qh4", "b", -300, pv_san="Nf6"),  # black (user) blunders
        point(5, "Nf3", "w", -350),
    ]
    tps = detect_turning_points(evals, "b")
    assert len(tps) == 1
    assert tps[0].ply == 4
    assert tps[0].san_played == "Qh4"


def test_allowed_mate_detected():
    # from +35 the user plays a move that allows mate in 2
    evals = [
        point(1, "e4", "w", 40),
        point(2, "e5", "b", 35),
        point(3, "g4", "w", None, -2),  # allows Qh4 threat
        point(4, "Qh4", "b", None, -1),
    ]
    tps = detect_turning_points(evals, "w")
    assert len(tps) == 1
    assert tps[0].drop_cp == 35 - cp_norm(None, -2) == 100_015
    assert tps[0].after.mate == -2


def test_missed_mate_detected():
    # from a forced mate in 3 the user plays a quiet move that gives it away
    evals = [
        point(1, "e4", "w", 30),
        point(2, "e5", "b", 25),
        point(3, "Qh5", "w", None, 3, pv_san="Qh5"),
        point(4, "Nf6", "b", 10),
        point(5, "Qxe5+", "w", None, 3, pv_san="Qxe5+"),
        point(6, "Be7", "b", 8),
        point(7, "Qxh8", "w", None, 3, pv_san="Qxh8"),
        point(8, "Nc6", "b", None, 3, pv_san="Qxh8"),  # mate in 3 still on the board
        point(9, "d3", "w", -80),  # missed the mate
    ]
    tps = detect_turning_points(evals, "w")
    assert len(tps) == 1
    tp = tps[0]
    assert tp.ply == 9
    assert tp.before.mate == 3  # had mate in 3
    assert tp.san_best == "Qxh8"
    assert tp.drop_cp > 200


def test_first_ply_is_never_a_turning_point():
    evals = [point(1, "f3", "w", -200)]
    assert detect_turning_points(evals, "w") == []


def test_only_first_turning_point_per_game_reported():
    # two separate blunders in the same game, both from an equal position —
    # only the FIRST is picked (one turning point per game)
    evals = [
        point(1, "e4", "w", 30),
        point(2, "e5", "b", 25),
        point(3, "Nf3", "w", 20),
        point(4, "Nc6", "b", 15),
        point(5, "Nxe5", "w", -250),  # first blunder (before +15, drop 265)
        point(6, "Nxe5", "b", -280),
        point(7, "Bc4", "w", 20),  # recovers to equal
        point(8, "Bc5", "b", 15),
        point(9, "d3", "w", -400),  # second blunder would also qualify
        point(10, "Qh4", "b", -450),
    ]
    tps = detect_turning_points(evals, "w")
    assert [tp.ply for tp in tps] == [5]


def test_mistake_from_already_losing_position_not_detected():
    # compounding a lost position (-2.0) is not a turning point: the game
    # drifts into losing gradually (no single drop > 2.0), then a big
    # further drop happens from an already-lost position
    evals = [
        point(1, "e4", "w", 30),
        point(2, "e5", "b", 20),
        point(3, "Nf3", "w", -80),  # drop 100 — below the 2.0 cutoff
        point(4, "Nc6", "b", -90),
        point(5, "Be2", "w", -200),  # drop 110 — now worse than -1.5
        point(6, "Be7", "b", -180),
        point(7, "Nxe5", "w", -550),  # drop 370, but from a lost position
        point(8, "Nxe5", "b", -600),
    ]
    assert detect_turning_points(evals, "w") == []


def test_equal_floor_boundary_is_detected():
    # exactly -1.5 before the move still counts as "equal"
    evals = [
        point(1, "e4", "w", 30),
        point(2, "e5", "b", -150),  # boundary: equal-or-better
        point(3, "Nf3", "w", -400),  # drop 250 > 200
        point(4, "Nc6", "b", -420),
    ]
    tps = detect_turning_points(evals, "w")
    assert [tp.ply for tp in tps] == [3]
    assert tps[0].drop_cp == 250


def test_drop_boundary_not_detected():
    # "more than 2.0" — a drop of exactly 2.0 does not qualify
    evals = [
        point(1, "e4", "w", 30),
        point(2, "e5", "b", 20),
        point(3, "Nf3", "w", -180),  # drop exactly 200
        point(4, "Nc6", "b", -190),
    ]
    assert detect_turning_points(evals, "w") == []


def test_winning_position_detected():
    # from a winning +3.5 the user drops 2.4 — clearly a turning point
    evals = [
        point(1, "e4", "w", 40),
        point(2, "e5", "b", 350),
        point(3, "Nf3", "w", 110),  # drop 240 > 200
        point(4, "Nc6", "b", 100),
    ]
    tps = detect_turning_points(evals, "w")
    assert [tp.ply for tp in tps] == [3]


def test_opponent_moves_are_ignored():
    # big drops on the opponent's moves are not the player's turning points
    evals = [
        point(1, "e4", "w", 30),
        point(2, "e5", "b", -400),  # opponent blundered, but it's not ours
        point(3, "Nf3", "w", 400),
    ]
    assert detect_turning_points(evals, "w") == []
