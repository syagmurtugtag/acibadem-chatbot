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
    echo "Knowledge base is empty. Importing recommended ACU sources in BACKGROUND..."
    echo "(Web app will start serving immediately; KB will fill up over the next few minutes.)"
    echo "Tail the scrape log with:  docker compose exec webapp tail -f /tmp/scrape.log"
    nohup python manage.py scrape \
        --max-pages "$KB_BOOTSTRAP_MAX_PAGES" \
        --max-pdfs "$KB_BOOTSTRAP_MAX_PDFS" \
        > /tmp/scrape.log 2>&1 &
  else
    echo "Knowledge base already contains data. Skipping bootstrap import."
  fi

  echo "Generating missing embeddings in BACKGROUND..."
  nohup python manage.py generate_embeddings > /tmp/embeddings.log 2>&1 &

  # OBS Bologna scrape — runs Playwright headless Chromium to fetch the
  # authoritative course curricula. Only triggered when the KB does not
  # already contain any *Playwright-sourced* OBS records (recognised by the
  # title suffix written in obs_crawler.py). This way a previous static
  # crawl that only wrote stub OBS rows does not block the proper scrape.
  OBS_KB_COUNT=$(python manage.py shell -c "from chat.models import KnowledgeBase; print(KnowledgeBase.objects.filter(url__contains='obs.acibadem.edu.tr', title__contains='(OBS)').count())")

  if [ "$OBS_KB_COUNT" = "0" ]; then
    echo "No proper OBS Bologna records yet. Running browser-based OBS scrape in BACKGROUND..."
    echo "Tail it with:  docker compose exec webapp tail -f /tmp/scrape_obs.log"
    nohup python manage.py scrape_obs > /tmp/scrape_obs.log 2>&1 &
  else
    echo "Playwright OBS records already present ($OBS_KB_COUNT). Skipping OBS scrape."
  fi
fi

echo "Starting Gunicorn..."
exec gunicorn config.wsgi:application \
    --bind 0.0.0.0:8000 \
    --workers 2 \
    --timeout 120 \
    --pythonpath /app
