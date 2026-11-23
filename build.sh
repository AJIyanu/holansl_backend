#!/usr/bin/env bash
set -o errexit

pip install -r requirements.txt

if [[ "${APP_ENV:-production}" != "production" && "${APP_ENV:-production}" != "prod" ]]; then
  echo "Render builds must run with APP_ENV=production."
  exit 1
fi

python manage.py check
python manage.py collectstatic --no-input
python manage.py migrate

# The database cache table is required only for the PostgreSQL fallback.
if [[ -z "${REDIS_URL:-}" ]]; then
  python manage.py createcachetable django_cache
fi

# Idempotent baseline records required by a fresh application database.
python manage.py create_default_account_data
python manage.py create_default_notification_templates
python manage.py create_task_notification_templates
python manage.py createsuperuser_if_not_exists
python manage.py verify_supabase_storage
