"""Training views: mixed-source set creation, categories, analyze/mark JSON."""

import json

import pytest
from django.test import Client

from books.models import Book, PageImage, Puzzle
from training.models import TrainingSet, TrainingSetItem

pytestmark = pytest.mark.django_db

FEN = "r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3"


@pytest.fixture
def book():
    return Book.objects.create(title="Polgar 5334", slug="polgar-5334")


@pytest.fixture
def book_set(book, owner):
    page = PageImage.objects.create(book=book, page_number=16, image="x.png")
    for i in range(3):
        Puzzle.objects.create(
            book=book, page=page, diagram_index=i + 1, number=str(i + 1),
            fen=FEN, status=Puzzle.Status.VERIFIED, confidence=1.0,
        )
    from training.services import create_set

    return create_set("Test set", [
        {"source_type": "book", "book_slug": book.slug, "pages_from": 16,
         "pages_to": 16},
    ], owner)


def test_set_list_and_create(client, book):
    response = client.get("/train-sets/")
    assert response.status_code == 200
    response = client.post("/train-sets/", {
        "name": "My set", "source_type_0": "book", "book_0": book.slug,
        "pages_from_0": "16", "pages_to_0": "16",
    })
    assert response.status_code == 302
    training_set = TrainingSet.objects.get(name="My set")
    assert training_set.items.count() == 0  # no puzzles created in this test
    assert response.headers["Location"] == f"/train-sets/{training_set.pk}/"


def test_set_list_paginates_50_per_page(client, owner):
    for i in range(51):
        TrainingSet.objects.create(name=f"Set {i:02d}", owner=owner)

    response = client.get("/train-sets/")
    assert response.status_code == 200
    page = response.context["set_page"]
    assert page.paginator.per_page == 50
    assert page.paginator.num_pages == 2
    assert len(page.object_list) == 50
    # the clone dropdown must still offer every set, not just this page
    assert response.context["sets"].count() == 51

    response = client.get("/train-sets/?p=2")
    assert len(response.context["set_page"].object_list) == 1

    # out-of-range and invalid page numbers fall back safely
    response = client.get("/train-sets/?p=99")
    assert response.context["set_page"].number == 2
    response = client.get("/train-sets/?p=abc")
    assert response.context["set_page"].number == 1


def test_set_create_mixed_entries(client, book):
    from analysis.models import AnalysisRun, TurningPoint
    from chessdata.models import Game, Player

    player = Player.objects.create(username="bob")
    game = Game.objects.create(
        uuid="g1", player=player, white_username="bob", black_username="x",
        player_side="w", player_result_raw="resigned", result="loss",
        time_class="rapid", end_time="2026-08-01T00:00:00Z", url="http://x",
        pgn="1. e4 e5",
    )
    run = AnalysisRun.objects.create(
        game=game, engine_name="Stockfish", engine_version="16", depth=18,
        threshold_cp=150, status=AnalysisRun.RunStatus.DONE,
    )
    TurningPoint.objects.create(
        game=game, run=run, ply=20, move_number="10... Ne5", san_played="Ne5",
        san_best="Qh5", fen_before=FEN, fen_after=FEN, drop_cp=200,
        classification=TurningPoint.Classification.HUNG_PIECE, rule_text="x",
    )
    response = client.post("/train-sets/", {
        "name": "Mixed",
        "source_type_0": "book", "book_0": book.slug, "pages_from_0": "16",
        "pages_to_0": "16",
        "source_type_1": "turning_points", "player_1": "bob",
        "date_from_1": "2026-07-01", "date_to_1": "2026-08-31",
    })
    assert response.status_code == 302
    training_set = TrainingSet.objects.get(name="Mixed")
    assert training_set.items.count() == 1  # book has no puzzles here; TP has 1
    assert training_set.items.first().turning_point is not None


def test_set_list_requires_a_source(client):
    response = client.post("/train-sets/", {
        "name": "X", "source_type_0": "book", "book_0": "",
    })
    assert response.status_code == 302
    assert TrainingSet.objects.count() == 0


def test_set_detail_shows_items_and_stats(client, book_set):
    response = client.get(f"/train-sets/{book_set.pk}/")
    content = response.content.decode()
    assert "Test set" in content
    assert "<strong>3</strong> positions" in content
    assert "3 pending" in content
    assert "Start training" in content
    assert "Export PDF" in content
    assert "Re-work" in content  # the category exists


def test_set_detail_category_filter(client, book_set):
    item = book_set.items.first()
    item.status = TrainingSetItem.Status.REWORK
    item.save(update_fields=["status"])
    response = client.get(f"/train-sets/{book_set.pk}/?status=rework")
    assert len(response.context["items"]) == 1
    assert response.context["items"][0].pk == item.pk


def test_set_pdf_exports_positions(client, book_set):
    response = client.get(f"/train-sets/{book_set.pk}/pdf/")
    assert response.status_code == 200
    assert response["Content-Type"] == "application/pdf"
    assert b"".join(response.streaming_content).startswith(b"%PDF")
    assert response["Content-Disposition"] == (
        'attachment; filename="test-set_positions.pdf"'
    )


def test_set_pdf_skips_unparseable_fen(book_set):
    from training.pdf import build_items

    bad = book_set.items.first()
    bad.fen = "not-a-fen"
    bad.save(update_fields=["fen"])
    entries = build_items(book_set)
    pks = [entry["item"].pk for entry in entries]
    assert bad.pk not in pks
    assert len(entries) == book_set.items.count() - 1


def test_set_pdf_marks_black_to_move(book_set):
    from training.pdf import build_items

    item = book_set.items.first()
    item.fen = FEN.replace(" w ", " b ")
    item.save(update_fields=["fen"])
    marks = {entry["item"].pk: entry["side_mark"] for entry in build_items(book_set)}
    assert marks[item.pk] == "(B)"
    assert all(mark == "" for pk, mark in marks.items() if pk != item.pk)


def test_set_pdf_uses_chesscom_board_style(book_set):
    from training.pdf import build_items

    svg = build_items(book_set)[0]["board_svg"]
    # chess.com square colors, not the python-chess default brown
    assert "#ebecd0" in svg
    assert "#739552" in svg
    assert "#ffce9e" not in svg
    # the coordinate margin is transparent — the board prints borderless
    assert 'opacity="0.0"' in svg


def test_rework_list_shows_only_rework_items(client, book_set):
    items = list(book_set.items.all())
    items[0].status = TrainingSetItem.Status.REWORK
    items[0].save(update_fields=["status"])
    items[1].status = TrainingSetItem.Status.PASSED
    items[1].save(update_fields=["status"])
    response = client.get("/rework/")
    assert response.status_code == 200
    content = response.content.decode()
    assert "Re-work" in content
    assert len(response.context["items"]) == 1
    assert response.context["items"][0].pk == items[0].pk


def test_rework_list_filter_by_set(client, book_set, owner):
    from training.services import create_set

    other = create_set("Other set", [
        {"source_type": "book", "book_slug": book_set.items.first().puzzle.book.slug,
         "pages_from": 16, "pages_to": 16},
    ], owner)
    in_first = book_set.items.first()
    in_other = other.items.first()
    for item in (in_first, in_other):
        item.status = TrainingSetItem.Status.REWORK
        item.save(update_fields=["status"])

    response = client.get(f"/rework/?set={book_set.pk}")
    assert response.context["selected_set"].pk == book_set.pk
    assert list(response.context["items"]) == [in_first]

    response = client.get(f"/rework/?set={other.pk}")
    assert list(response.context["items"]) == [in_other]


def test_rework_list_filter_by_book(client, book_set, owner):
    from training.services import create_set

    other_book = Book.objects.create(title="Other book", slug="other-book")
    page = PageImage.objects.create(book=other_book, page_number=1, image="x.png")
    Puzzle.objects.create(
        book=other_book, page=page, diagram_index=1, number="1",
        fen=FEN, status=Puzzle.Status.VERIFIED, confidence=1.0,
    )
    other = create_set("Other set", [
        {"source_type": "book", "book_slug": other_book.slug,
         "pages_from": 1, "pages_to": 1},
    ], owner)
    in_first = book_set.items.first()
    in_other = other.items.first()
    for item in (in_first, in_other):
        item.status = TrainingSetItem.Status.REWORK
        item.save(update_fields=["status"])

    response = client.get(f"/rework/?book={in_first.puzzle.book.slug}")
    assert response.context["selected_book"].pk == in_first.puzzle.book.pk
    assert list(response.context["items"]) == [in_first]

    response = client.get("/rework/?book=other-book")
    assert list(response.context["items"]) == [in_other]

    # unknown set/book params fall back to the unfiltered list
    response = client.get("/rework/?set=9999&book=nope")
    assert len(response.context["items"]) == 2


def test_item_session_renders_board(client, book_set):
    item = book_set.items.first()
    response = client.get(f"/train-sets/{book_set.pk}/items/{item.pk}/")
    content = response.content.decode()
    assert response.status_code == 200
    assert "chessground.min.js" in content
    assert "item-fen" in content
    assert "Re-analyze" in content
    assert "Flip board" in content
    # position-editing controls are admin-only
    assert "Fix orientation" not in content
    assert "Switch side to move" not in content
    assert "Edit position" not in content
    assert "analysis-switch" in content
    assert "Open in Chess.com" in content
    assert "Re-work" in content


def test_item_session_shows_edit_buttons_for_superuser(admin_client, book_set):
    item = book_set.items.first()
    response = admin_client.get(f"/train-sets/{book_set.pk}/items/{item.pk}/")
    content = response.content.decode()
    assert response.status_code == 200
    assert "Fix orientation" in content
    assert "Switch side to move" in content
    assert "Edit position" in content


@pytest.mark.parametrize(
    "payload",
    (
        {"action": "rotate"},
        {"action": "toggle_turn"},
        {"action": "set_fen", "fen": "4k3/8/8/8/8/8/8/4K3 w - - 0 1"},
    ),
)
def test_item_action_edit_actions_forbidden_for_non_superuser(client, book_set, payload):
    item = book_set.items.first()
    response = client.post(
        f"/train-sets/{book_set.pk}/items/{item.pk}/action/",
        data=json.dumps(payload),
        content_type="application/json",
    )
    assert response.status_code == 403
    item.refresh_from_db()
    assert item.fen == FEN


def test_item_action_rotate_saves(admin_client, book_set):
    item = book_set.items.first()
    original = item.fen
    response = admin_client.post(
        f"/train-sets/{book_set.pk}/items/{item.pk}/action/",
        data=json.dumps({"action": "rotate"}),
        content_type="application/json",
    )
    assert response.status_code == 200
    fen = response.json()["fen"]
    assert fen != original  # the position changed
    item.refresh_from_db()
    assert item.fen == fen  # saved to the DB
    # self-inverse: rotating again returns the original placement
    # (castling rights are dropped by the first rotation)
    response = admin_client.post(
        f"/train-sets/{book_set.pk}/items/{item.pk}/action/",
        data=json.dumps({"action": "rotate"}),
        content_type="application/json",
    )
    from books.views import rotate_position_180

    assert rotate_position_180(rotate_position_180(original)) == response.json()["fen"]


def test_item_action_toggle_turn_saves(admin_client, book_set):
    item = book_set.items.first()
    assert " w " in item.fen
    response = admin_client.post(
        f"/train-sets/{book_set.pk}/items/{item.pk}/action/",
        data=json.dumps({"action": "toggle_turn"}),
        content_type="application/json",
    )
    assert response.status_code == 200
    fen = response.json()["fen"]
    assert " b " in fen
    item.refresh_from_db()
    assert item.fen == fen


def test_item_action_rejects_bad_action(client, book_set):
    item = book_set.items.first()
    response = client.post(
        f"/train-sets/{book_set.pk}/items/{item.pk}/action/",
        data=json.dumps({"action": "nonsense"}),
        content_type="application/json",
    )
    assert response.status_code == 400
    item.refresh_from_db()
    assert item.fen == FEN


def test_mark_item_json_advances(client, book_set):
    item = book_set.items.first()
    response = client.post(
        f"/train-sets/{book_set.pk}/items/{item.pk}/mark/",
        data=json.dumps({"result": "rework", "status": ""}),
        content_type="application/json",
    )
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] and data["item_status"] == "rework"
    item.refresh_from_db()
    assert item.attempts == 1 and item.puzzle.failed == 1
    # next pending item exists
    assert data["next_item_url"].startswith(f"/train-sets/{book_set.pk}/items/")


def test_mark_item_rejects_bad_result(client, book_set):
    item = book_set.items.first()
    response = client.post(
        f"/train-sets/{book_set.pk}/items/{item.pk}/mark/",
        data=json.dumps({"result": "nonsense"}),
        content_type="application/json",
    )
    assert response.status_code == 400
    item.refresh_from_db()
    assert item.status == TrainingSetItem.Status.PENDING


def test_mark_all_within_category(client, book_set):
    response = client.post(
        f"/train-sets/{book_set.pk}/mark-all/",
        {"result": "rework", "status": "pending"},
    )
    assert response.status_code == 302
    assert book_set.items.filter(status=TrainingSetItem.Status.REWORK).count() == 3


def test_set_detail_add_positions_rework_source(client, book_set, owner):
    # mark two items re-work in this set, then add them to a second set
    from training.services import create_set

    for item in book_set.items.all()[:2]:
        item.status = TrainingSetItem.Status.REWORK
        item.save(update_fields=["status"])
    target = create_set("Target", [], owner)
    response = client.post(
        f"/train-sets/{target.pk}/",
        {
            "add_positions": "1",
            "source_type_0": "rework",
            "from_set_0": str(book_set.pk),
        },
    )
    assert response.status_code == 302
    assert target.items.count() == 2
    assert target.items.first().fen == book_set.items.first().fen


def test_training_pages_require_login():
    client = Client()
    for url in ("/train-sets/", "/train-sets/1/pdf/"):
        response = client.get(url)
        assert response.status_code == 302, url
        assert response.headers["Location"].startswith("/accounts/login/"), url


def test_clone_set(client, book_set):
    # mark one item re-work so the clone must reset statuses
    item = book_set.items.first()
    item.status = TrainingSetItem.Status.REWORK
    item.save(update_fields=["status"])
    response = client.post("/train-sets/", {
        "clone_from": str(book_set.pk),
        "clone_name": "My clone",
    })
    assert response.status_code == 302
    clone = TrainingSet.objects.get(name="My clone")
    assert clone.items.count() == book_set.items.count()
    assert clone.items.filter(status=TrainingSetItem.Status.PENDING).count() == 3
    assert set(clone.items.values_list("fen", flat=True)) == set(
        book_set.items.values_list("fen", flat=True)
    )
    assert response.headers["Location"] == f"/train-sets/{clone.pk}/"


def test_clone_set_default_name(client, book_set):
    response = client.post("/train-sets/", {"clone_from": str(book_set.pk)})
    assert response.status_code == 302
    clone = TrainingSet.objects.get(name="Copy of Test set")
    assert clone.items.count() == 3


def test_clone_set_unknown_source(client):
    response = client.post("/train-sets/", {"clone_from": "9999"})
    assert response.status_code == 302
    assert TrainingSet.objects.count() == 0


def test_item_action_set_fen_updates_item_and_puzzle(admin_client, book_set):
    item = book_set.items.first()
    new_fen = "4k3/8/8/8/8/8/8/4K3 w - - 0 1"
    response = admin_client.post(
        f"/train-sets/{book_set.pk}/items/{item.pk}/action/",
        data=json.dumps({"action": "set_fen", "fen": new_fen}),
        content_type="application/json",
    )
    assert response.status_code == 200
    item.refresh_from_db()
    item.puzzle.refresh_from_db()
    assert item.fen == new_fen
    assert item.puzzle.fen == new_fen  # the source puzzle is fixed too
    assert item.puzzle.piece_cells == []


def test_item_action_set_fen_accepts_kingless_fen(admin_client, book_set):
    # ill-scanned OCR positions (e.g. missing a king) must be saveable so
    # the user can repair them in the editor
    item = book_set.items.first()
    kingless = "8/8/8/8/8/8/8/K7 w - - 0 1"
    response = admin_client.post(
        f"/train-sets/{book_set.pk}/items/{item.pk}/action/",
        data=json.dumps({"action": "set_fen", "fen": kingless}),
        content_type="application/json",
    )
    assert response.status_code == 200
    item.refresh_from_db()
    assert item.fen == kingless


def test_item_action_set_fen_rejects_bad_fen(admin_client, book_set):
    item = book_set.items.first()
    response = admin_client.post(
        f"/train-sets/{book_set.pk}/items/{item.pk}/action/",
        data=json.dumps({"action": "set_fen", "fen": "not-a-fen"}),
        content_type="application/json",
    )
    assert response.status_code == 400
    item.refresh_from_db()
    assert item.fen == FEN
