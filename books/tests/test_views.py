import pytest
from django.test import Client

from books.models import Book, Puzzle

pytestmark = pytest.mark.django_db

FEN = "r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3"


@pytest.fixture
def book():
    return Book.objects.create(title="Polgar 5334", slug="polgar-5334")


@pytest.fixture
def review_puzzle(book):
    return Puzzle.objects.create(
        book=book, fen=FEN, status=Puzzle.Status.REVIEW,
        confidence=0.5, extraction_notes="low confidence",
    )


def test_bad_fens_lists_illegal_and_links_to_review(client, book):
    bad = Puzzle.objects.create(
        book=book, fen="8/8/8/8/8/8/8/8 w - - 0 1", status=Puzzle.Status.REVIEW
    )
    malformed = Puzzle.objects.create(
        book=book, fen="not a fen", status=Puzzle.Status.REVIEW
    )
    good = Puzzle.objects.create(book=book, fen=FEN, status=Puzzle.Status.REVIEW)
    response = client.get("/bad-fens/")
    content = response.content.decode()
    assert f'href="/review/{bad.pk}/"' in content
    assert f'href="/review/{malformed.pk}/"' in content
    assert f'href="/review/{good.pk}/"' not in content
    assert "kings != 1" in content  # reasons are shown per row
    assert "malformed FEN" in content


def test_bad_fens_book_filter(client, book):
    other = Book.objects.create(title="Other book", slug="other-book")
    Puzzle.objects.create(
        book=book, fen="8/8/8/8/8/8/8/8 w - - 0 1", status=Puzzle.Status.REVIEW
    )
    Puzzle.objects.create(
        book=other, fen="8/8/8/8/8/8/8/8 w - - 0 1", status=Puzzle.Status.REVIEW
    )
    response = client.get(f"/bad-fens/?book={book.slug}")
    assert response.context["book_filter"] == book.slug
    assert len(response.context["bad"]) == 1
    response = client.get("/bad-fens/")
    assert len(response.context["bad"]) == 2


def test_bad_fens_requires_login():
    client = Client()
    response = client.get("/bad-fens/")
    assert response.status_code == 302
    assert response.headers["Location"].startswith("/accounts/login/")


def test_review_queue_lists_review_puzzles(client, review_puzzle):
    verified = Puzzle.objects.create(
        book=review_puzzle.book, fen=FEN, status=Puzzle.Status.VERIFIED, confidence=1.0
    )
    response = client.get("/review/")
    content = response.content.decode()
    assert "low confidence" in content
    # the verified puzzle's review link must not be in the queue
    assert f'href="/review/{review_puzzle.pk}/"' in content
    assert f'href="/review/{verified.pk}/"' not in content


def test_review_verify(client, review_puzzle):
    response = client.post(
        f"/review/{review_puzzle.pk}/",
        {"action": "verify", "fen": FEN, "number": "567"},
    )
    assert response.status_code == 302
    review_puzzle.refresh_from_db()
    assert review_puzzle.status == Puzzle.Status.VERIFIED
    assert review_puzzle.number == "567"
    assert review_puzzle.confidence == 1.0


def test_review_verify_rejects_bad_fen(client, review_puzzle):
    response = client.post(
        f"/review/{review_puzzle.pk}/",
        {"action": "verify", "fen": "4k3/8/8/8/8/8/8/K2K4 w - - 0 1", "number": ""},
    )
    review_puzzle.refresh_from_db()
    assert review_puzzle.status == Puzzle.Status.REVIEW  # unchanged


def test_review_reject(client, review_puzzle):
    response = client.post(f"/review/{review_puzzle.pk}/", {"action": "reject"})
    review_puzzle.refresh_from_db()
    assert review_puzzle.status == Puzzle.Status.REJECTED


def test_review_save_without_verify(client, review_puzzle):
    response = client.post(
        f"/review/{review_puzzle.pk}/",
        {"action": "save", "fen": FEN, "number": "42"},
    )
    review_puzzle.refresh_from_db()
    assert review_puzzle.number == "42"
    assert review_puzzle.status == Puzzle.Status.REVIEW


def test_review_pages_require_login():
    client = Client()
    for url in ("/books/", "/review/", "/review/1/"):
        response = client.get(url)
        assert response.status_code == 302, url
        assert response.headers["Location"].startswith("/accounts/login/"), url


def test_puzzle_board_svg(client, review_puzzle):
    response = client.get(f"/puzzles/{review_puzzle.pk}/board.svg")
    assert response.status_code == 200
    assert response.headers["Content-Type"] == "image/svg+xml"
    assert b"<svg" in response.content


def test_puzzle_view_rotate_persists_and_reverts(client, book):
    fen = "4k3/8/8/8/8/8/4P3/4K3 w - - 0 1"
    puzzle = Puzzle.objects.create(book=book, fen=fen, status=Puzzle.Status.REVIEW)
    response = client.post(f"/puzzles/{puzzle.pk}/", {"action": "rotate"})
    assert response.status_code == 302
    puzzle.refresh_from_db()
    assert puzzle.fen != fen
    # the rotation is its own inverse: clicking again reverts
    client.post(f"/puzzles/{puzzle.pk}/", {"action": "rotate"})
    puzzle.refresh_from_db()
    assert puzzle.fen == fen


def test_puzzle_view_rotate_handles_castling(client, book):
    puzzle = Puzzle.objects.create(
        book=book, fen="r3k2r/8/8/8/8/8/8/4K3 w KQkq - 0 1",
        status=Puzzle.Status.VERIFIED,
    )
    client.post(f"/puzzles/{puzzle.pk}/", {"action": "rotate"})
    puzzle.refresh_from_db()
    # castling rights are dropped: the rotated king sits off e1/e8
    assert puzzle.fen == "3K4/8/8/8/8/8/8/r2k3r w - - 0 1"
    client.post(f"/puzzles/{puzzle.pk}/", {"action": "rotate"})
    puzzle.refresh_from_db()
    assert puzzle.fen == "r3k2r/8/8/8/8/8/8/4K3 w - - 0 1"


def test_puzzle_view_toggle_turn_persists(client, book):
    puzzle = Puzzle.objects.create(
        book=book, fen="4k3/8/8/8/8/8/4P3/4K3 w - - 0 1",
        status=Puzzle.Status.REVIEW,
    )
    client.post(f"/puzzles/{puzzle.pk}/", {"action": "toggle_turn"})
    puzzle.refresh_from_db()
    assert " b " in puzzle.fen
    client.post(f"/puzzles/{puzzle.pk}/", {"action": "toggle_turn"})
    puzzle.refresh_from_db()
    assert " w " in puzzle.fen


def test_puzzle_view_toggle_turn_rejects_invalid_fen(client, book):
    puzzle = Puzzle.objects.create(book=book, fen="not a fen", status=Puzzle.Status.REVIEW)
    response = client.post(f"/puzzles/{puzzle.pk}/", {"action": "toggle_turn"})
    assert response.status_code == 302
    puzzle.refresh_from_db()
    assert puzzle.fen == "not a fen"


def test_puzzle_view_rotate_rejects_invalid_fen(client, book):
    puzzle = Puzzle.objects.create(book=book, fen="not a fen", status=Puzzle.Status.REVIEW)
    response = client.post(f"/puzzles/{puzzle.pk}/", {"action": "rotate"})
    assert response.status_code == 302
    puzzle.refresh_from_db()
    assert puzzle.fen == "not a fen"


def test_books_index_and_detail(client, book):
    response = client.get("/books/")
    assert response.status_code == 200
    assert "Polgar 5334" in response.content.decode()
    response = client.get(f"/books/{book.slug}/")
    assert response.status_code == 200


def test_book_detail_paginates_30_per_page(client, book):
    for i in range(35):
        Puzzle.objects.create(book=book, fen=FEN, status=Puzzle.Status.EXTRACTED)
    response = client.get(f"/books/{book.slug}/")
    assert response.context["puzzles"].number == 1
    assert len(response.context["puzzles"].object_list) == 30
    assert response.context["puzzles"].paginator.num_pages == 2
    response = client.get(f"/books/{book.slug}/?p=2")
    assert len(response.context["puzzles"].object_list) == 5


def test_book_detail_goto_puzzle_redirects(client, book):
    puzzle = Puzzle.objects.create(
        book=book, fen=FEN, status=Puzzle.Status.EXTRACTED, number="42"
    )
    response = client.get(f"/books/{book.slug}/?goto=42")
    assert response.status_code == 302
    assert response.headers["Location"] == f"/puzzles/{puzzle.pk}/"


def test_book_detail_goto_missing_puzzle_warns(client, book):
    response = client.get(f"/books/{book.slug}/?goto=9999")
    assert response.status_code == 200
    assert "No puzzle #9999" in response.content.decode()


def test_book_detail_page_filter(client, book):
    from books.models import PageImage

    page16 = PageImage.objects.create(book=book, page_number=16, image="x.png")
    page17 = PageImage.objects.create(book=book, page_number=17, image="y.png")
    Puzzle.objects.create(
        book=book, page=page16, diagram_index=1, fen=FEN,
        status=Puzzle.Status.EXTRACTED, number="1",
    )
    Puzzle.objects.create(
        book=book, page=page17, diagram_index=1, fen=FEN,
        status=Puzzle.Status.EXTRACTED, number="2",
    )
    response = client.get(f"/books/{book.slug}/?page=16")
    content = response.content.decode()
    assert "on page 16" in content
    assert "Download page 16 as PDF" in content
    assert response.context["selected_page"] == page16
    assert len(response.context["puzzles"].object_list) == 1


def test_book_page_pdf(client, book):
    from books.models import PageImage

    page = PageImage.objects.create(book=book, page_number=16, image="x.png")
    for i in range(8):
        # odd items are black-to-move: marked "#N(B)", no FEN on the page
        fen = FEN.replace(" w ", " b ") if i % 2 else FEN
        Puzzle.objects.create(
            book=book, page=page, diagram_index=i + 1, fen=fen,
            status=Puzzle.Status.EXTRACTED, number=str(i + 1),
        )
    response = client.get(f"/books/{book.slug}/pages/16/pdf/")
    assert response.status_code == 200
    assert response.headers["Content-Type"] == "application/pdf"
    data = b"".join(response.streaming_content)
    assert data.startswith(b"%PDF")

    import pymupdf

    doc = pymupdf.open(stream=data, filetype="pdf")
    text = " ".join(pg.get_text() for pg in doc)
    pdf_pages = doc.page_count
    doc.close()
    assert pdf_pages == 2  # 8 puzzles -> exactly 6 + 2 per page
    assert "#1" in text and "#2(B)" in text  # odd items are black-to-move
    assert "#7" in text and "#8(B)" in text
    # FEN strings must not appear anywhere on the pages
    assert "rnbqkbnr" not in text and "kqbnr" not in text



def test_puzzle_view_prev_next_buttons(client, book):
    from books.models import PageImage

    page = PageImage.objects.create(book=book, page_number=16, image="x.png")
    ids = []
    for i in range(3):
        puzzle = Puzzle.objects.create(
            book=book, page=page, diagram_index=i + 1, number=str(i + 1),
            fen=FEN, status=Puzzle.Status.EXTRACTED, confidence=1.0,
        )
        ids.append(puzzle.pk)
    # middle puzzle: both neighbors
    response = client.get(f"/puzzles/{ids[1]}/")
    content = response.content.decode()
    assert 'title="previous puzzle of this book"' in content
    assert 'title="next puzzle of this book"' in content
    assert f'href="/puzzles/{ids[0]}/"' in content
    assert f'href="/puzzles/{ids[2]}/"' in content
    # first puzzle: only next
    response = client.get(f"/puzzles/{ids[0]}/")
    content = response.content.decode()
    assert 'title="previous puzzle of this book"' not in content
    assert f'href="/puzzles/{ids[1]}/"' in content
    # last puzzle: only previous
    response = client.get(f"/puzzles/{ids[2]}/")
    content = response.content.decode()
    assert 'title="next puzzle of this book"' not in content
    assert f'href="/puzzles/{ids[1]}/"' in content
