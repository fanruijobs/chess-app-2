"""Per-user ownership of training sets: visibility, 404s and cross-user writes.

The autouse conftest fixture logs `client` in as `owner`; a set built in these
tests therefore belongs to the logged-in user unless stated otherwise.
"""

import json

import pytest
from django.contrib.auth.models import User
from django.test import Client

from books.models import Book, PageImage, Puzzle
from training.models import TrainingSet, TrainingSetItem

pytestmark = pytest.mark.django_db

FEN = "r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3"


@pytest.fixture
def puzzle(db):
    book = Book.objects.create(title="Book", slug="book")
    page = PageImage.objects.create(book=book, page_number=1, image="x.png")
    return Puzzle.objects.create(
        book=book, page=page, diagram_index=1, number="1", fen=FEN,
        status=Puzzle.Status.VERIFIED, confidence=1.0,
    )


def make_set(owner, puzzle, name="S", status=TrainingSetItem.Status.PENDING):
    """A one-item set owned by `owner` (items need a puzzle or a TP)."""
    training_set = TrainingSet.objects.create(name=name, owner=owner)
    TrainingSetItem.objects.create(
        training_set=training_set, puzzle=puzzle, fen=FEN, label="1",
        order=0, status=status,
    )
    return training_set


@pytest.fixture
def other(db):
    return User.objects.create_user(username="other", password="pw")


@pytest.fixture
def other_set(other, puzzle):
    return make_set(other, puzzle, name="Other's set")


@pytest.fixture
def own_set(owner, puzzle):
    return make_set(owner, puzzle, name="Mine")


def test_set_list_only_shows_own_sets(client, own_set, other_set):
    response = client.get("/train-sets/")
    assert response.status_code == 200
    names = [s.name for s in response.context["sets"]]
    assert names == ["Mine"]
    html = response.content.decode()
    assert "Mine" in html
    assert "Other&#x27;s set" not in html and "Other's set" not in html
    # the clone dropdown must not offer another user's set either
    assert f'value="{other_set.pk}"' not in html


def test_other_users_set_is_404(client, own_set, other_set):
    item = other_set.items.first()
    for url in (
        f"/train-sets/{other_set.pk}/",
        f"/train-sets/{other_set.pk}/pdf/",
        f"/train-sets/{other_set.pk}/items/{item.pk}/",
        f"/train-sets/{other_set.pk}/mark-all/",
    ):
        assert client.get(url).status_code == 404, url

    for url in (
        f"/train-sets/{other_set.pk}/items/{item.pk}/action/",
        f"/train-sets/{other_set.pk}/items/{item.pk}/mark/",
    ):
        response = client.post(
            url, data=json.dumps({"action": "rotate_fen", "result": "passed"}),
            content_type="application/json",
        )
        assert response.status_code == 404, url

    item.refresh_from_db()
    assert item.status == TrainingSetItem.Status.PENDING and item.attempts == 0


def test_own_set_is_reachable(client, own_set):
    item = own_set.items.first()
    assert client.get(f"/train-sets/{own_set.pk}/").status_code == 200
    assert client.get(f"/train-sets/{own_set.pk}/pdf/").status_code == 200
    assert client.get(f"/train-sets/{own_set.pk}/items/{item.pk}/").status_code == 200


def test_rework_list_is_scoped(client, own_set, other_set):
    theirs = other_set.items.first()
    theirs.status = TrainingSetItem.Status.REWORK
    theirs.save(update_fields=["status"])
    mine = own_set.items.first()
    mine.status = TrainingSetItem.Status.REWORK
    mine.save(update_fields=["status"])

    response = client.get("/rework/")
    assert list(response.context["items"]) == [mine]
    assert [s.name for s in response.context["sets"]] == ["Mine"]

    # filtering by a foreign set must not leak it — falls back to own items
    response = client.get(f"/rework/?set={other_set.pk}")
    assert response.context["selected_set"] is None
    assert list(response.context["items"]) == [mine]

    # and their book must not appear in the book filter either
    assert list(response.context["books"]) == [mine.puzzle.book]


def test_rework_source_cannot_cross_users(client, own_set, other_set):
    theirs = other_set.items.first()
    theirs.status = TrainingSetItem.Status.REWORK
    theirs.save(update_fields=["status"])

    response = client.post(f"/train-sets/{own_set.pk}/", {
        "add_positions": "1",
        "source_type_0": "rework",
        "from_set_0": str(other_set.pk),
    })
    assert response.status_code == 302
    assert own_set.items.count() == 1  # nothing copied in


def test_clone_from_other_users_set_rejected(client, own_set, other_set):
    response = client.post("/train-sets/", {"clone_from": str(other_set.pk)})
    assert response.status_code == 302
    assert TrainingSet.objects.count() == 2  # no clone created


def test_clone_and_non_numeric_source(client, own_set):
    # non-numeric clone_from used to raise ValueError -> 500
    response = client.post("/train-sets/", {"clone_from": "abc"})
    assert response.status_code == 302
    assert TrainingSet.objects.count() == 1


def test_superuser_sees_all_sets(own_set, other_set):
    staff = User.objects.create_superuser(username="root", password="pw")
    client = Client()
    client.force_login(staff)

    response = client.get("/train-sets/")
    assert sorted(s.name for s in response.context["sets"]) == ["Mine", "Other's set"]
    assert client.get(f"/train-sets/{other_set.pk}/").status_code == 200
    assert client.get(f"/train-sets/{own_set.pk}/").status_code == 200

    # a set created by the superuser belongs to the superuser
    response = client.post("/train-sets/", {
        "clone_from": str(other_set.pk), "clone_name": "Root copy",
    })
    assert response.status_code == 302
    assert TrainingSet.objects.get(name="Root copy").owner == staff


def test_login_logout_flow(db):
    User.objects.create_user(username="fan", password="password")
    client = Client()

    response = client.get("/train-sets/")
    assert response.status_code == 302
    assert response.headers["Location"] == "/accounts/login/?next=/train-sets/"

    assert client.get("/accounts/login/").status_code == 200

    assert client.post(
        "/accounts/login/", {"username": "fan", "password": "wrong"}
    ).status_code == 200  # re-renders with errors

    response = client.post("/accounts/login/", {"username": "fan", "password": "password"})
    assert response.status_code == 302 and response.headers["Location"] == "/"
    assert client.get("/train-sets/").status_code == 200

    # LogoutView is POST-only
    assert client.get("/accounts/logout/").status_code == 405
    response = client.post("/accounts/logout/")
    assert response.status_code == 302
    assert response.headers["Location"] == "/accounts/login/"
    assert client.get("/train-sets/").status_code == 302
