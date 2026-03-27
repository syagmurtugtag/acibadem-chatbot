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

echo "Starting Gunicorn..."
exec gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers 2 --timeout 120
