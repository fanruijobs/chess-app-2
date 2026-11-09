import numpy as np

from books.cv import detect_diagrams
from books.tests.synthetic import fen_to_multi_page, fen_to_page


def test_single_board_detected():
    page = fen_to_page("r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3")
    diagrams = detect_diagrams(page)
    assert len(diagrams) == 1
    assert diagrams[0].board_img.shape == (640, 640)


def test_two_boards_reading_order():
    fens = [
        "r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3",
        "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
    ]
    page = fen_to_multi_page(fens, size=240)  # canvas stays under 2000px
    diagrams = detect_diagrams(page)
    assert len(diagrams) == 2
    # reading order: left to right
    assert diagrams[0].center[0] < diagrams[1].center[0]
    assert diagrams[1].board_img.shape == (640, 640)


def test_board_amid_text():
    # a board rendered next to textual noise still gets detected
    fens = ["8/8/8/8/8/8/8/4K2k w - - 0 1"]
    page = fen_to_multi_page(fens)
    diagrams = detect_diagrams(page)
    assert len(diagrams) == 1


def test_bbox_in_original_pixels():
    page = np.full((600, 800, 3), 255, dtype=np.uint8)  # white placeholder
    diagrams = detect_diagrams(page)
    assert diagrams == []  # no board on a blank page
