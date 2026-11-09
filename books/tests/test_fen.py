import chess

from books.cv.fen import build_fen, matrix_to_fen, side_to_move, validate_fen


def _matrix_from_fen(fen: str) -> list[list[str | None]]:
    board = chess.Board(fen)
    code_map = {"P": "wP", "N": "wN", "B": "wB", "R": "wR", "Q": "wQ", "K": "wK",
                "p": "bP", "n": "bN", "b": "bB", "r": "bR", "q": "bQ", "k": "bK"}
    matrix = []
    for rank in range(7, -1, -1):
        row = []
        for file in range(8):
            piece = board.piece_at(chess.square(file, rank))
            row.append(code_map[piece.symbol()] if piece else None)
        matrix.append(row)
    return matrix


def test_matrix_to_fen_startpos():
    fen = matrix_to_fen(_matrix_from_fen(chess.STARTING_FEN))
    assert fen.startswith("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR")


def test_matrix_to_fen_midgame():
    src = "r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R"
    fen = matrix_to_fen(_matrix_from_fen(src))
    assert fen.startswith(src)


def test_unknown_cells_render_as_empty():
    matrix = [[None] * 8 for _ in range(8)]
    matrix[0][0] = "?"
    fen = matrix_to_fen(matrix)
    assert fen.startswith("8/8/8/8/8/8/8/8")


def test_build_fen_roundtrip_side_and_legality():
    src = "r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3"
    fen, legal, reasons = build_fen(_matrix_from_fen(src))
    assert legal
    assert fen.startswith(src.split(" ")[0])
    assert " w " in fen


def test_side_to_move_flips_when_black_in_check():
    # Qe2 checks the e8 king down the open e-file -> black to move
    src = "rnb1kbnr/pppp1ppp/8/8/8/5N2/PPPPQPPP/RNB1KB1R w KQkq - 0 1"
    matrix = _matrix_from_fen(src)
    fen, legal, _ = build_fen(matrix)
    assert legal
    assert " b " in fen


def test_side_to_move_stays_when_white_in_check():
    # qh4 checks e1 -> white (the checked side) is to move
    src = "rnb1kbnr/pppp1ppp/8/4p3/7q/5N2/PPPP2PP/RNBQKB1R w KQkq - 0 1"
    matrix = _matrix_from_fen(src)
    fen, legal, _ = build_fen(matrix)
    assert legal
    assert " w " in fen


def test_both_sides_in_check_invalid():
    # adjacent kings attack each other -> side_to_move gives up
    fen = "8/8/8/8/8/4k3/4K3/8 w - - 0 1"
    assert side_to_move(fen) is None


def test_validate_fen_reasons():
    # two white kings
    fen, legal, reasons = validate_fen("4k3/8/8/8/8/8/8/K2K4 w - - 0 1")
    assert not legal
    assert reasons
