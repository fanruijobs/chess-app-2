"""Shared fixtures for training tests (mirrors books/tests/conftest.py)."""

import pytest
from django.contrib.auth.models import User


@pytest.fixture
def owner(db):
    """The logged-in user — and therefore the owner of sets built in tests.

    Deliberately NOT a superuser: a superuser bypasses the ownership filter,
    so the suite would stop exercising ownership at all.
    """
    user, _ = User.objects.get_or_create(username="admin")
    return user


@pytest.fixture(autouse=True)
def _authenticated_client(client, owner):
    client.force_login(owner)


@pytest.fixture
def admin_user(db):
    """A superuser — the only role allowed to edit stored positions."""
    return User.objects.create_superuser("root", "root@example.com", "pw")


@pytest.fixture
def admin_client(client, admin_user, _authenticated_client):
    client.force_login(admin_user)
    return client
