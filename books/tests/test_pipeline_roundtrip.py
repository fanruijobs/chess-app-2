"""Round-trip: synthetic board images -> extraction -> FEN equality.

Validates pipeline mechanics against the SVG piece font. Real-book font
robustness is checked separately with photos in fixtures/pages/.
"""

import pytest

from books.cv import extract_page
from books.tests.synthetic import build_profile, fen_to_page

CALIBRATION_POSITIONS = [
    "r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3",
    "r1bq1rk1/ppp2ppp/2np1n2/2b1p3/2B1P3/2NP1N2/PPP2PPP/R1BQ1RK1 w - - 0 7",
    "rnbq1k1r/pp1Pbppp/2p2n2/8/2B5/8/PPP1NnPP/RNBQK2R w KQ - 1 8",
    # cover queens on light squares and corner kings (the sparse test cases)
    "k7/8/8/8/8/4Q3/2q5/7K w - - 0 1",
    "k7/8/8/2q5/8/8/3Q4/7K w - - 0 1",
]

TEST_POSITIONS = [
    ("startpos", "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"),
    ("midgame", "r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3"),
    ("pieces", "6k1/5ppp/8/8/8/8/5PPP/R3K2R w KQ - 0 1"),
    ("queens", "k7/8/8/8/8/4Q3/2q5/7K w - - 0 1"),
    ("bishops", "k7/8/1b6/2B5/8/8/8/7K w - - 0 1"),
    ("knights", "k7/8/8/8/2N5/8/1n6/7K w - - 0 1"),
    ("black_side", "rnb1kbnr/pppp1ppp/8/8/8/5N2/PPPPQPPP/RNB1KB1R w KQkq - 0 1"),  # Qe2+ -> b to move
]


@pytest.fixture(scope="module")
def profile():
    return build_profile(CALIBRATION_POSITIONS)


@pytest.mark.parametrize("name,fen", TEST_POSITIONS)
def test_roundtrip(profile, name, fen):
    page = fen_to_page(fen)
    results = extract_page(page, profile)
    assert len(results) == 1, name
    result = results[0]
    source_board, source_turn, *_ = fen.split(" ")
    extracted_board = result.fen.split(" ")[0]
    assert result.legal, (name, result.notes)
    assert extracted_board == source_board, (
        f"{name}: {extracted_board} != {source_board} "
        f"(unknown={result.unknown_count}, corr={result.mean_corr})"
    )
    # side to move: heuristic flips only when the side to move is in check
    expected_turn = source_turn
    if name == "black_side":
        expected_turn = "b"
    assert result.fen.split(" ")[1] == expected_turn, name
