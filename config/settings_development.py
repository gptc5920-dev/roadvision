"""Local development settings backed by an isolated SQLite database.

Use this profile when the production-compatible MariaDB service is not
available. It keeps local React/Django development independent from legacy
databases while production continues to use the database configured in
``config.settings``.
"""

from .settings import *  # noqa: F403


DEBUG = True
SECURE_SSL_REDIRECT = False
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
SECURE_HSTS_SECONDS = 0

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.development.sqlite3",  # noqa: F405
    }
}

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
    },
}
