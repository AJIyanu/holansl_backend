"""Settings for running the Django test suite against local PostgreSQL."""

import os

import dj_database_url

from .settings import *  # noqa: F403


# Tests must never inherit the online DATABASE_URL loaded by settings.py.
# Override Django's default database alias with a local-only connection.
LOCAL_TEST_DATABASE_URL = os.getenv(
    "LOCAL_TEST_DATABASE_URL",
    "postgresql://Holan:test_password@localhost:5432/holan_test",
)

DATABASES = {  # noqa: F405
    "default": dj_database_url.parse(
        LOCAL_TEST_DATABASE_URL,
        conn_max_age=0,
    ),
}
