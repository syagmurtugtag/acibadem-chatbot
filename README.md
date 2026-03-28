# ACU AI Chatbot

An AI-powered chatbot for Acibadem University, focused on Double Major and Minor program information.

Built with Django, PostgreSQL, and Ollama (Llama 3.2 3B), fully containerized with Docker and Docker Compose.

> CSE 322 - Cloud Computing | Spring 2026

---

## Team Members

| Name | Student ID |
|---|---|
| Selin Yagmur Tugtag | 221401703 |
| Mehmet Emir Altinsoy | 231401704 |

---

## Quick Start

Requirements:
- Docker Desktop must be installed and running
- Internet connection is needed on the first run to download the model and fetch ACU sources

### 1. Clone the repository

```bash
git clone https://github.com/syagmurtugtag/acibadem-chatbot.git
cd acibadem-chatbot
```

### 2. Create the `.env` file

```bash
cp .env.example .env
```

On Windows Command Prompt:

```cmd
copy .env.example .env
```

The default values are enough for local development.

Note:
- `.env.example` is intended for local development and classroom demos
- for production, replace the default database password and `SECRET_KEY`

### 3. Start the full system

```bash
docker compose up --build
```

This is the main startup command for the project.

What happens on startup:
- PostgreSQL starts
- Ollama starts
- the `llama3.2:3b` model is pulled automatically if it is missing
- Django runs migrations and collects static files
- if the knowledge base is empty, the app automatically imports recommended Acibadem University sources
- the web app starts on port `8000`

### 4. Open the app

- Chatbot: [http://localhost:8000](http://localhost:8000)
- Admin panel: [http://localhost:8000/admin/](http://localhost:8000/admin/)

To create an admin account:

```bash
docker compose exec webapp python manage.py createsuperuser
```

---

## One-Command Startup

The assignment requires the project to be startable with a single `docker compose up` command.

This repository now supports that flow:

```bash
docker compose up --build
```

If the database is empty on first startup, the web app automatically bootstraps the knowledge base by importing recommended ACU pages and PDFs. This means the user does not need to run a separate scrape command during normal first-time setup.

---

## Knowledge Base Loading

The chatbot answers questions using content collected from Acibadem University sources.

There are three ways to load data:

### A. Automatic bootstrap on first startup

This happens automatically inside the `webapp` container when the knowledge base is empty.

### B. Manual import of recommended ACU sources

```bash
docker compose exec webapp python manage.py scrape
```

This imports a built-in set of recommended Acibadem University pages and PDFs.

### C. Crawl from a custom ACU page

```bash
docker compose exec webapp python manage.py scrape --start-url https://www.acibadem.edu.tr/
```

Only `acibadem.edu.tr` links are followed by the crawler.

---

## Admin Tools

From the Django admin panel, the Knowledge Base section supports:

- `Upload PDF`: import text from a PDF file
- `Scrape URL`: import content from one page
- `Crawl ACU Site`: crawl from a custom Acibadem URL
- `Import Recommended ACU Sources`: one-click import of built-in ACU sources
- `Paste Text (OBS)`: manually paste content from JavaScript-heavy pages such as OBS/Bologna pages

This is useful because some public ACU sources are regular HTML pages, while others may need manual copy-paste if they are JavaScript-heavy.

---

## Useful Commands

Start everything:

```bash
docker compose up --build
```

Start in background:

```bash
docker compose up -d --build
```

Stop containers:

```bash
docker compose down
```

View logs:

```bash
docker compose logs -f
```

View only web app logs:

```bash
docker compose logs -f webapp
```

Import recommended ACU sources manually:

```bash
docker compose exec webapp python manage.py scrape
```

Create admin user:

```bash
docker compose exec webapp python manage.py createsuperuser
```

---

## System Architecture

Containers:
- `db`: PostgreSQL 15 database for knowledge base content, chat history, and application data
- `ollama`: local LLM service
- `ollama-init`: pulls the `llama3.2:3b` model if needed
- `webapp`: Django application served by Gunicorn

Typical flow:
1. User asks a question in the web interface.
2. Django retrieves relevant knowledge base content.
3. Django sends context plus question to the local Ollama model.
4. The model generates an answer.
5. The answer and conversation are stored in PostgreSQL.

---

## Features

- Chat interface for university-related questions
- Conversation history stored in PostgreSQL
- Django admin panel for knowledge base and chat logs
- REST API endpoint at `POST /api/chat/`
- Local LLM integration through Ollama
- Prompt customization for more grounded answers
- Domain-limited ACU crawler for public website content
- Automatic knowledge base bootstrap on first startup

---

## Project Structure

```text
acibadem-chatbot/
|-- docker-compose.yml
|-- .env.example
|-- README.md
`-- webapp/
    |-- Dockerfile
    |-- entrypoint.sh
    |-- manage.py
    |-- requirements.txt
    |-- config/
    |   |-- settings.py
    |   |-- urls.py
    |   `-- wsgi.py
    |-- chat/
    |   |-- admin.py
    |   |-- models.py
    |   |-- urls.py
    |   `-- views.py
    |-- scraper/
    |   |-- site_crawler.py
    |   `-- management/commands/scrape.py
    `-- templates/
```

---

## API Reference

### POST `/api/chat/`

Request:

```json
{
  "question": "Which departments can Computer Engineering students apply to for a double major?",
  "conversation_id": null
}
```

Response:

```json
{
  "answer": "Computer Engineering students can apply for a Double Major in Biomedical Engineering, Molecular Biology and Genetics, Psychology, Sociology, and Health Management.",
  "conversation_id": 1
}
```

### GET `/api/conversation/<id>/`

Returns one conversation together with its stored messages.

---

## Current Limitations

- Answer quality is still being improved
- Some pages may require manual copy-paste because they are JavaScript-heavy
- First startup can take time because of model download and initial source import
- The chatbot is currently strongest on double major, minor, and related academic information

---

## Tech Stack

| Layer | Technology |
|---|---|
| Web Framework | Django 4.2 |
| Database | PostgreSQL 15 |
| LLM | Llama 3.2 3B via Ollama |
| Containerization | Docker and Docker Compose |
| Web Server | Gunicorn |
| Scraping | requests, BeautifulSoup, PyPDF2 |
