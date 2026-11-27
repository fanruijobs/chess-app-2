import pytest
from django.contrib.auth.models import User
from rest_framework.test import APIClient

from books.models import Book, PageImage, Puzzle
from training.models import TrainingSet, TrainingSetItem
from training.services import create_set

pytestmark = pytest.mark.django_db

FEN = "r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3"


@pytest.fixture
def user():
    return User.objects.create_user(username="player", password="pw")


@pytest.fixture
def other_user():
    return User.objects.create_user(username="other", password="pw")


@pytest.fixture
def book():
    return Book.objects.create(title="Polgar 5334", slug="polgar-5334")


@pytest.fixture
def training_set(user, book):
    page = PageImage.objects.create(book=book, page_number=16, image="x.png")
    for index in range(3):
        Puzzle.objects.create(
            book=book,
            page=page,
            diagram_index=index + 1,
            number=str(index + 1),
            fen=FEN,
            status=Puzzle.Status.VERIFIED,
            confidence=1.0,
        )
    return create_set(
        "API set",
        [{"source_type": "book", "book_slug": book.slug, "pages_from": 16}],
        user,
    )


@pytest.fixture
def api_client(user):
    client = APIClient()
    client.force_login(user)
    return client


def test_api_requires_authentication():
    response = APIClient().get("/api/train-sets/")
    assert response.status_code in (401, 403)


def test_login_requires_csrf_then_returns_user(user):
    client = APIClient(enforce_csrf_checks=True)
    response = client.post(
        "/api/auth/login/",
        {"username": "player", "password": "pw"},
        format="json",
    )
    assert response.status_code == 403

    csrf = client.get("/api/auth/csrf/")
    token = csrf.cookies["csrftoken"].value
    response = client.post(
        "/api/auth/login/",
        {"username": "player", "password": "pw"},
        format="json",
        HTTP_X_CSRFTOKEN=token,
    )
    assert response.status_code == 200
    assert response.json()["username"] == "player"


def test_me_and_logout(api_client):
    response = api_client.get("/api/auth/me/")
    assert response.status_code == 200
    assert response.json()["username"] == "player"
    assert api_client.post("/api/auth/logout/").status_code == 200
    assert api_client.get("/api/auth/me/").status_code in (401, 403)


def test_training_set_list_is_paginated(api_client, user):
    for index in range(51):
        TrainingSet.objects.create(name=f"Set {index:02d}", owner=user)
    response = api_client.get("/api/train-sets/")
    assert response.status_code == 200
    data = response.json()
    assert data["count"] == 51
    assert len(data["results"]) == 50
    assert data["next"] is not None

    response = api_client.get("/api/train-sets/?page=2")
    assert response.status_code == 200
    assert len(response.json()["results"]) == 1


def test_training_set_detail_and_items(api_client, training_set):
    response = api_client.get(f"/api/train-sets/{training_set.pk}/")
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "API set"
    assert data["total"] == 3
    assert data["pending"] == 3
    assert data["pass_rate"] is None

    response = api_client.get(f"/api/train-sets/{training_set.pk}/items/")
    assert response.status_code == 200
    assert response.json()["count"] == 3
    assert [item["label"] for item in response.json()["results"]] == ["1", "2", "3"]


def test_training_set_ownership_is_scoped(api_client, other_user, book):
    theirs = TrainingSet.objects.create(name="Theirs", owner=other_user)
    assert api_client.get(f"/api/train-sets/{theirs.pk}/").status_code == 404
    assert api_client.get(f"/api/train-sets/{theirs.pk}/items/").status_code == 404

    response = api_client.get("/api/train-sets/")
    names = [row["name"] for row in response.json()["results"]]
    assert "Theirs" not in names


def test_superuser_sees_all_sets(user, other_user):
    admin = User.objects.create_superuser("root", "root@example.com", "pw")
    TrainingSet.objects.create(name="Mine", owner=user)
    TrainingSet.objects.create(name="Theirs", owner=other_user)
    client = APIClient()
    client.force_login(admin)
    response = client.get("/api/train-sets/")
    assert {row["name"] for row in response.json()["results"]} == {"Mine", "Theirs"}
