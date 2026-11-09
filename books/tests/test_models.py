import pytest
from django.db import IntegrityError, transaction

from books.models import Book, PageImage, Puzzle

pytestmark = pytest.mark.django_db

FEN = "r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3"


@pytest.fixture
def book():
    return Book.objects.create(title="Polgar 5334", slug="polgar-5334")


def test_unique_book_number_exempts_blank(book):
    Puzzle.objects.create(book=book, fen=FEN, number="1", status=Puzzle.Status.VERIFIED)
    Puzzle.objects.create(book=book, fen=FEN, number="", status=Puzzle.Status.EXTRACTED)
    Puzzle.objects.create(book=book, fen=FEN, number="", status=Puzzle.Status.EXTRACTED)
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Puzzle.objects.create(book=book, fen=FEN, number="1")


def test_unique_page_diagram(book):
    page = PageImage.objects.create(book=book, image="x.jpg")
    Puzzle.objects.create(book=book, page=page, diagram_index=1, fen=FEN)
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Puzzle.objects.create(book=book, page=page, diagram_index=1, fen=FEN)
    # null page/diagram do not conflict
    Puzzle.objects.create(book=book, fen=FEN)
    Puzzle.objects.create(book=book, fen=FEN)


def test_board_helper(book):
    puzzle = Puzzle.objects.create(book=book, fen=FEN)
    board = puzzle.board()
    assert board is not None
    assert board.fen().startswith("r1bqkbnr")
    bad = Puzzle.objects.create(book=book, fen="not-a-fen")
    assert bad.board() is None

