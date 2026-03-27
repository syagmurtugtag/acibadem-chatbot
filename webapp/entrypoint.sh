#!/bin/sh

echo "Waiting for PostgreSQL..."
until python -c "
import psycopg2, os, sys
try:
    psycopg2.connect(
        dbname=os.environ.get('POSTGRES_DB', 'acudb'),
        user=os.environ.get('POSTGRES_USER', 'acuuser'),
        password=os.environ.get('POSTGRES_PASSWORD', 'acupassword123'),
        host='db',
        port='5432'
    )
    sys.exit(0)
except Exception:
    sys.exit(1)
"; do
    echo "PostgreSQL not ready yet, retrying in 2s..."
    sleep 2
done

echo "PostgreSQL is ready!"

echo "Running migrations..."
python manage.py migrate --noinput

echo "Collecting static files..."
python manage.py collectstatic --noinput

AUTO_BOOTSTRAP_KB="${AUTO_BOOTSTRAP_KB:-True}"
KB_BOOTSTRAP_MAX_PAGES="${KB_BOOTSTRAP_MAX_PAGES:-2}"
KB_BOOTSTRAP_MAX_PDFS="${KB_BOOTSTRAP_MAX_PDFS:-1}"

if [ "$AUTO_BOOTSTRAP_KB" = "True" ]; then
  echo "Checking knowledge base bootstrap state..."
  KB_COUNT=$(python manage.py shell -c "from chat.models import KnowledgeBase; print(KnowledgeBase.objects.count())")

  if [ "$KB_COUNT" = "0" ]; then
    echo "Knowledge base is empty. Importing recommended ACU sources..."
    python manage.py scrape --max-pages "$KB_BOOTSTRAP_MAX_PAGES" --max-pdfs "$KB_BOOTSTRAP_MAX_PDFS"
  else
    echo "Knowledge base already contains data. Skipping bootstrap import."
  fi
fi

echo "Starting Gunicorn..."
exec gunicorn config.wsgi:application \
    --bind 0.0.0.0:8000 \
    --workers 2 \
    --timeout 120 \
    --pythonpath /app
