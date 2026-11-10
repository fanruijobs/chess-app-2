"""Shared fixtures for books tests (mirrors analysis/tests/conftest.py)."""

import pytest
from django.contrib.auth.models import User


@pytest.fixture(autouse=True)
def _authenticated_client(client, db):
    user, _ = User.objects.get_or_create(username="admin")
    client.force_login(user)


@pytest.fixture
def admin_user(db):
    """A superuser — the only role allowed to edit stored positions."""
    return User.objects.create_superuser("root", "root@example.com", "pw")


@pytest.fixture
def admin_client(client, admin_user, _authenticated_client):
    client.force_login(admin_user)
    return client
