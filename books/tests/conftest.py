"""Shared fixtures for books tests (mirrors analysis/tests/conftest.py)."""

import pytest
from django.contrib.auth.models import User


@pytest.fixture(autouse=True)
def _authenticated_client(client, db):
    user, _ = User.objects.get_or_create(username="admin")
    client.force_login(user)
