"""Shared fixtures for analysis tests.

The autouse fixture logs the test client in as the admin user — all app views
require login. Tests that need an anonymous client create their own
django.test.Client() directly.
"""

import pytest
from django.contrib.auth.models import User


@pytest.fixture(autouse=True)
def _authenticated_client(client, db):
    user, _ = User.objects.get_or_create(username="admin")
    client.force_login(user)
