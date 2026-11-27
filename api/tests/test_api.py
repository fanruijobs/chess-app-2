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


@pytest.fixture
def admin_client(user):
    admin = User.objects.create_superuser("root", "root@example.com", "pw")
    client = APIClient()
    client.force_login(admin)
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


def test_create_set_from_book(api_client, user, book, training_set):
    response = api_client.post(
        "/api/train-sets/",
        {
            "name": "Created via API",
            "entries": [
                {
                    "source_type": "book",
                    "book_slug": book.slug,
                    "pages_from": 16,
                }
            ],
        },
        format="json",
    )
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "Created via API"
    assert data["total"] == 3
    assert TrainingSet.objects.get(name="Created via API").owner == user


def test_create_set_requires_a_source(api_client):
    response = api_client.post(
        "/api/train-sets/", {"name": "Empty", "entries": []}, format="json"
    )
    assert response.status_code == 400


def test_clone_set(api_client, training_set):
    response = api_client.post(
        "/api/train-sets/clone/",
        {"source_id": training_set.pk, "name": "Cloned"},
        format="json",
    )
    assert response.status_code == 201
    clone = TrainingSet.objects.get(name="Cloned")
    assert clone.items.count() == training_set.items.count()
    assert clone.owner == training_set.owner


def test_add_items_to_set(api_client, training_set, book):
    response = api_client.post(
        f"/api/train-sets/{training_set.pk}/items/",
        {
            "entries": [
                {
                    "source_type": "book",
                    "book_slug": book.slug,
                    "pages_from": 16,
                }
            ]
        },
        format="json",
    )
    assert response.status_code == 201
    assert response.json()["added"] == 3
    assert training_set.items.count() == 6


def test_mark_all_in_category(api_client, training_set):
    response = api_client.post(
        f"/api/train-sets/{training_set.pk}/mark-all/",
        {"result": "passed", "status": "pending"},
        format="json",
    )
    assert response.status_code == 200
    assert response.json()["marked"] == 3
    assert training_set.items.filter(status="passed").count() == 3


def test_item_detail_and_mark(api_client, training_set):
    items = list(training_set.items.order_by("order", "id"))
    response = api_client.get(
        f"/api/train-sets/{training_set.pk}/items/{items[0].pk}/"
    )
    assert response.status_code == 200
    assert response.json()["item"]["label"] == "1"

    response = api_client.post(
        f"/api/train-sets/{training_set.pk}/items/{items[0].pk}/mark/",
        {"result": "rework", "status": "pending"},
        format="json",
    )
    assert response.status_code == 200
    data = response.json()
    assert data["item_status"] == "rework"
    assert data["attempts"] == 1
    assert data["next_item_id"] == items[1].pk


def test_item_action_is_admin_only(api_client, admin_client, training_set):
    item = training_set.items.first()
    original = item.fen
    response = api_client.post(
        f"/api/train-sets/{training_set.pk}/items/{item.pk}/action/",
        {"action": "rotate"},
        format="json",
    )
    assert response.status_code == 403
    item.refresh_from_db()
    assert item.fen == original

    response = admin_client.post(
        f"/api/train-sets/{training_set.pk}/items/{item.pk}/action/",
        {"action": "rotate"},
        format="json",
    )
    assert response.status_code == 200
    item.refresh_from_db()
    assert item.fen != original


def test_rework_list_is_scoped(api_client, training_set):
    item = training_set.items.first()
    item.status = "rework"
    item.save(update_fields=["status"])
    response = api_client.get("/api/rework/")
    assert response.status_code == 200
    data = response.json()
    assert data["count"] == 1
    assert data["results"][0]["set_id"] == training_set.pk


def test_training_options(api_client, training_set, book):
    response = api_client.get("/api/train-sets/options/")
    assert response.status_code == 200
    data = response.json()
    assert data["books"] == [{"slug": book.slug, "title": book.title}]
    assert data["sets"][0]["id"] == training_set.pk
    assert "classifications" in data
