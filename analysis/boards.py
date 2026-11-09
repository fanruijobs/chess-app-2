"""Board reconstruction helpers for the web UI and PDF export."""

import io

import chess
import chess.pgn
import chess.svg


def board_at(game, ply: int) -> chess.Board:
    """Replay the game PGN and return the board after `ply` plies."""
    node = chess.pgn.read_game(io.StringIO(game.pgn))
    board = node.board()
    for i, move in enumerate(node.mainline_moves(), start=1):
        if i > ply:
            break
        board.push(move)
    return board


def board_svg(game, ply: int, size: int = 560) -> str:
    """Render the board after `ply` plies, oriented for the tracked player."""
    orientation = chess.BLACK if game.player_side == "b" else chess.WHITE
    return chess.svg.board(
        board_at(game, ply),
        size=size,
        orientation=orientation,
        coordinates=True,
    )
