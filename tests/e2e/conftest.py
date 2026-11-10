"""E2E setup.

Playwright's sync API starts an event loop in the test thread before
pytest-django creates the transactional test database. Django's async-safety
guard rejects the DB setup in that situation, so disable the guard for the
browser suite. The setting only affects tests, not the app.
"""

import os

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
