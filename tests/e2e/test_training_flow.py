"""Browser end-to-end tests for the critical player and admin journeys.

Run with the Playwright extra installed:

    pip install -r requirements-e2e.txt
    python -m playwright install chromium
    pytest tests/e2e -m e2e

These tests intentionally stay out of the default unit-test run (pytest.ini
`testpaths`), so CI can run them in a separate browser-enabled job.
"""

import re

import pytest
from django.contrib.auth.models import User
from playwright.sync_api import expect

from books.models import Book, PageImage, Puzzle
from training.models import TrainingSet, TrainingSetItem

pytestmark = [pytest.mark.e2e, pytest.mark.django_db(transaction=True)]

FEN = "r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3"


def login(page, live_server, username, password="pw"):
    page.goto(f"{live_server.url}/accounts/login/")
    page.locator("#id_username").fill(username)
    page.locator("#id_password").fill(password)
    page.get_by_role("button", name="Log in").click()
    page.wait_for_url(f"{live_server.url}/")


def make_set(owner, name="E2E set", item_count=1):
    book = Book.objects.create(title=f"Book {owner.pk}", slug=f"book-{owner.pk}")
    page_image = PageImage.objects.create(
        book=book, page_number=16, image="e2e.png"
    )
    training_set = TrainingSet.objects.create(name=name, owner=owner)
    items = []
    for index in range(item_count):
        puzzle = Puzzle.objects.create(
            book=book,
            page=page_image,
            diagram_index=index + 1,
            number=str(index + 1),
            fen=FEN,
            status=Puzzle.Status.VERIFIED,
            confidence=1.0,
        )
        items.append(
            TrainingSetItem.objects.create(
                training_set=training_set,
                puzzle=puzzle,
                fen=FEN,
                label=str(index + 1),
                order=index,
            )
        )
    return training_set, items


def test_login_and_training_set_pagination(live_server, page):
    player = User.objects.create_user(username="player", password="pw")
    for index in range(51):
        TrainingSet.objects.create(name=f"Set {index:02d}", owner=player)

    login(page, live_server, "player")
    page.goto(f"{live_server.url}/train-sets/")

    rows = page.locator("table tbody tr")
    expect(rows).to_have_count(50)
    expect(page.get_by_text(re.compile(r"page\s+1\s+of\s+2"))).to_be_visible()

    page.get_by_role("link", name=re.compile("next")).click()
    expect(page).to_have_url(re.compile(r"\?p=2$"))
    expect(rows).to_have_count(1)
    expect(page.get_by_text(re.compile(r"page\s+2\s+of\s+2"))).to_be_visible()


def test_position_edit_buttons_are_admin_only(live_server, browser):
    player = User.objects.create_user(username="player", password="pw")
    admin = User.objects.create_superuser(
        username="root", email="root@example.com", password="pw"
    )
    training_set, items = make_set(player)
    item_url = f"/train-sets/{training_set.pk}/items/{items[0].pk}/"

    player_context = browser.new_context()
    player_page = player_context.new_page()
    login(player_page, live_server, "player")
    player_page.goto(live_server.url + item_url)
    expect(player_page.locator("#flip-btn")).to_have_count(1)
    expect(player_page.locator("#rotate-btn")).to_have_count(0)
    expect(player_page.locator("#turn-btn")).to_have_count(0)
    expect(player_page.locator("#edit-btn")).to_have_count(0)
    player_context.close()

    admin_context = browser.new_context()
    admin_page = admin_context.new_page()
    login(admin_page, live_server, "root")
    admin_page.goto(live_server.url + item_url)
    expect(admin_page.locator("#rotate-btn")).to_have_count(1)
    expect(admin_page.locator("#turn-btn")).to_have_count(1)
    expect(admin_page.locator("#edit-btn")).to_have_count(1)
    admin_context.close()


def test_marking_pass_advances_to_the_next_item(live_server, page):
    player = User.objects.create_user(username="player", password="pw")
    training_set, items = make_set(player, item_count=2)
    first, second = items

    login(page, live_server, "player")
    page.goto(
        f"{live_server.url}/train-sets/{training_set.pk}/items/{first.pk}/",
        wait_until="networkidle",
    )
    page.locator("#pass-btn").click()
    page.wait_for_url(
        re.compile(rf"/train-sets/{training_set.pk}/items/{second.pk}/")
    )

    first.refresh_from_db()
    assert first.status == TrainingSetItem.Status.PASSED
    assert first.attempts == 1
