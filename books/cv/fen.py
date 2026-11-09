"""FEN building and validation from a 64-square piece matrix."""

import chess

PIECE_CODE_TO_FEN = {
    "wK": "K", "wQ": "Q", "wR": "R", "wB": "B", "wN": "N", "wP": "P",
    "bK": "k", "bQ": "q", "bR": "r", "bB": "b", "bN": "n", "bP": "p",
}


def matrix_to_fen(cells: list[list[str | None]]) -> str:
    """8x8 piece-code matrix (rank 8 first) -> FEN board part.

    Unknown cells ("?") are rendered as empty — they still drag down
    confidence, but produce a parseable FEN.
    """
    ranks = []
    for row in cells:
        rank = ""
        empty = 0
        for code in row:
            if code in (None, "?"):
                empty += 1
            else:
                if empty:
                    rank += str(empty)
                    empty = 0
                rank += PIECE_CODE_TO_FEN.get(code, "?")
        if empty:
            rank += str(empty)
        ranks.append(rank)
    return "/".join(ranks) + " w - - 0 1"


def side_to_move(fen_white_to_move: str) -> str | None:
    """Heuristic from check geometry.

    In any legal position the CHECKED side is the side to move (it must
    answer the check). So: black king in check -> black to move ('b'),
    otherwise white to move ('w'). Both kings in check -> None (invalid).
    """
    board_w = chess.Board(fen_white_to_move)
    white_in_check = board_w.is_check()
    board_b = chess.Board(fen_white_to_move.replace(" w ", " b ", 1))
    black_in_check = board_b.is_check()
    if white_in_check and black_in_check:
        return None
    if black_in_check:
        return "b"
    return "w"


def build_fen(cells: list[list[str | None]]) -> tuple[str, bool, list[str]]:
    """Matrix -> (fen, legal, reasons). Side to move applied heuristically."""
    fen = matrix_to_fen(cells)
    reasons = []
    turn = side_to_move(fen)
    if turn is None:
        return fen, False, ["both sides in check"]
    if turn == "b":
        fen = fen.replace(" w ", " b ", 1)
    return validate_fen(fen)


def validate_fen(fen: str) -> tuple[str, bool, list[str]]:
    """FEN -> (fen, legal, reasons). Reasons explain invalidity."""
    reasons = []
    try:
        board = chess.Board(fen)
    except ValueError as exc:
        return fen, False, [f"malformed FEN: {exc}"]
    if not board.is_valid():
        reasons.append("position invalid (kings/pawns)")
    if len(board.pieces(chess.KING, chess.WHITE)) != 1:
        reasons.append("white kings != 1")
    if len(board.pieces(chess.KING, chess.BLACK)) != 1:
        reasons.append("black kings != 1")
    if len(board.piece_map()) > 32:
        reasons.append("more than 32 pieces")
    return fen, not reasons, reasons
