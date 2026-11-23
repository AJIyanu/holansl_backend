#!/usr/bin/env bash
set -o errexit
set -o nounset
set -o pipefail

if [[ "${APP_ENV:-}" != "development" && "${APP_ENV:-}" != "dev" ]]; then
  echo "Set APP_ENV=development (or dev) before running this script."
  exit 1
fi

if [[ -z "${LOCAL_DEV_DATABASE_URL:-}" ]]; then
  echo "LOCAL_DEV_DATABASE_URL is required."
  exit 1
fi

PYTHON_BIN="${PYTHON_BIN:-./venv/bin/python}"

mapfile -t database_details < <(
  "${PYTHON_BIN}" -c '
import os
import re
from urllib.parse import urlsplit, urlunsplit

url = urlsplit(os.environ["LOCAL_DEV_DATABASE_URL"])
if url.scheme not in {"postgres", "postgresql"}:
    raise SystemExit("LOCAL_DEV_DATABASE_URL must use PostgreSQL.")
if (url.hostname or "").lower() not in {"localhost", "127.0.0.1", "::1"}:
    raise SystemExit("LOCAL_DEV_DATABASE_URL must target the local machine.")
name = url.path.lstrip("/")
if not re.fullmatch(r"[A-Za-z0-9_-]+", name or "") or name == "holan_test":
    raise SystemExit("Use a named development database other than holan_test.")
print(name)
print(urlunsplit((url.scheme, url.netloc, "/postgres", url.query, "")))
'
)

database_name="${database_details[0]}"
maintenance_url="${database_details[1]}"

if [[ "$(psql "${maintenance_url}" --tuples-only --no-align --command "SELECT 1 FROM pg_database WHERE datname = '${database_name}'")" != "1" ]]; then
  createdb --maintenance-db="${maintenance_url}" "${database_name}"
  echo "Created local development database: ${database_name}"
else
  echo "Local development database already exists: ${database_name}"
fi

"${PYTHON_BIN}" manage.py migrate

if [[ -z "${REDIS_URL:-}" ]]; then
  "${PYTHON_BIN}" manage.py createcachetable django_cache
fi

"${PYTHON_BIN}" manage.py create_default_account_data
"${PYTHON_BIN}" manage.py create_default_notification_templates
"${PYTHON_BIN}" manage.py create_task_notification_templates
"${PYTHON_BIN}" manage.py createsuperuser_if_not_exists

echo "Development database bootstrap complete."
