"""
Deployment settings for the EC2 instance.

Everything is inherited from the local dev settings; only the pieces that must
differ in production are overridden here. Select it with:

    DJANGO_SETTINGS_MODULE=config.settings_deploy
"""

import os

from .settings import *  # noqa: F401,F403  (re-export the dev baseline)
from .settings import BASE_DIR

DEBUG = False

SECRET_KEY = "mychessapp"

ALLOWED_HOSTS = [
    "3.107.245.75",
    "localhost",
    "127.0.0.1",
]

# Served on :8888 (nginx listens there and proxies to gunicorn on loopback).
CSRF_TRUSTED_ORIGINS = [
    "http://3.107.245.75:8888",
    "http://localhost:8888",
]

# Postgres runs on this same box and listens on the loopback interface only,
# so the connection never leaves the machine.
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("POSTGRES_DB", "chessapp"),
        "USER": os.environ.get("POSTGRES_USER", "chessapp"),
        "PASSWORD": os.environ.get("POSTGRES_PASSWORD", ""),
        "HOST": "127.0.0.1",
        "PORT": os.environ.get("POSTGRES_PORT", "5432"),
        "CONN_MAX_AGE": 60,
    }
}

# collectstatic target; nginx serves this directory directly.
STATIC_ROOT = BASE_DIR / "staticfiles"

# Linux engine binary rather than the macOS Homebrew path in the dev settings.
STOCKFISH_PATH = os.environ.get("STOCKFISH_PATH", "/usr/games/stockfish")

# No TLS certificate is set up yet, so leave the secure-cookie and redirect
# settings off; turn them on once this is behind HTTPS.
SECURE_SSL_REDIRECT = False
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
