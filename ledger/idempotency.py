"""Serialize retries across parties as well as within a party's posting lock."""

from django.db import connection


def lock_submission(key):
    # Must run inside the caller's transaction. Hash collisions only serialize
    # unrelated requests; the unique UUID and canonical payload remain authoritative.
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", [str(key)]
        )
