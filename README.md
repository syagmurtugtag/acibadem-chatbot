# ACU AI Chatbot

An AI-powered chatbot for Acibadem University, focused on **Double Major** and **Minor** program information.

Built with **Django**, **PostgreSQL**, and **Ollama (Llama 3.2 3B)** — fully containerized with **Docker Compose**.

> CSE 322 – Cloud Computing | Spring 2026

---

## Team Members

| Name | Student ID |
|---|---|
| Selin Yagmur Tugtag | 221401703 |
| Mehmet Emir Altinsoy | 231401704 |

---

## ⚡ Quick Start (step by step)

> **Requirements:** Docker Desktop must be installed and **running** before you begin.
> Download: https://www.docker.com/products/docker-desktop/

### Step 1 — Clone the repository

```bash
git clone https://github.com/syagmurtugtag/acibadem-chatbot.git
cd acibadem-chatbot
```

### Step 2 — Create the `.env` file

> ⚠️ This step is **required**. Without it, the containers will not start.

```bash
cp .env.example .env
```

On Windows (Command Prompt):
```cmd
copy .env.example .env
```

You do not need to edit `.env` — the default values work for local development.

### Step 3 — Build and start all containers

```bash
docker compose up --build
```

> **First run:** Docker will download the Llama 3.2 3B model (~2 GB). This can take **5–15 minutes** depending on your internet speed. Wait until you see:
> ```
> webapp  | Starting Gunicorn...
> ```

### Step 4 — Load the knowledge base

Open a **new terminal** in the same folder and run:

```bash
docker compose exec webapp python manage.py scrape
```

### Step 5 — Open the app

- **Chatbot:** http://localhost:8000
- **Admin panel:** http://localhost:8000/admin/

To create an admin account:
```bash
docker compose exec webapp python manage.py createsuperuser
```

---

## 🛑 Common Issues

**`http://localhost:8000` does not open / ERR_CONNECTION_REFUSED**
- Make sure you ran `cp .env.example .env` (Step 2)
- Wait for the line `Starting Gunicorn...` in the terminal before opening the browser
- Check that Docker Desktop is running

**Containers exit immediately**
- You forgot to create `.env`. Run `cp .env.example .env` and try again.

**Model download is stuck**
- The first run downloads ~2 GB. Be patient. Do not stop the process.

**Port 8000 already in use**
- Something else is running on port 8000. Stop it, or change the port in `docker-compose.yml` from `"8000:8000"` to e.g. `"8080:8000"` and open http://localhost:8080 instead.

---

## System Architecture

```
┌─────────────────────────────────────────────────────┐
│                  Docker Network                      │
│                                                     │
│  ┌──────────┐    ┌──────────────┐    ┌───────────┐  │
│  │ PostgreSQL│    │   Django /   │    │  Ollama   │  │
│  │   :5432   │◄──►│   Gunicorn   │───►│  :11434   │  │
│  │           │    │    :8000     │    │ llama3.2  │  │
│  └──────────┘    └──────────────┘    └───────────┘  │
│                         ▲                           │
└─────────────────────────┼───────────────────────────┘
                          │ HTTP
                     Browser / User
```

**Containers:**
- `db` — PostgreSQL 15: stores knowledge base, chat history, conversations
- `ollama` — Ollama server running Llama 3.2 3B locally (no external API calls)
- `ollama-init` — one-time service that pulls the model automatically on first run
- `webapp` — Django application served by Gunicorn

**Startup order:**
1. `db` starts and becomes healthy
2. `ollama` starts and becomes healthy
3. `ollama-init` pulls `llama3.2:3b` if not already cached
4. `webapp` runs migrations and starts Gunicorn

---

## Tech Stack

| Layer | Technology |
|---|---|
| Web Framework | Django 4.2 |
| Database | PostgreSQL 15 |
| LLM | Llama 3.2 3B via Ollama |
| Containerization | Docker & Docker Compose |
| Web Server | Gunicorn |
| Scraping | requests + BeautifulSoup + PyPDF2 |

---

## Features

- **Chat interface** — ask questions about double major and minor programs
- **Conversation history** — past conversations saved and accessible from the sidebar
- **Smart question routing** — definition, option-list, and yes/no questions handled with separate prompt strategies
- **Local LLM** — Llama 3.2 3B runs entirely on your machine; no data sent to external APIs
- **Admin panel** — manage knowledge base entries, view conversations and chat logs
- **Knowledge base tools** — upload PDFs, scrape URLs, or paste text directly from the admin panel
- **REST API** — `POST /api/chat/` endpoint

---

## Project Structure

```
acibadem-chatbot/
├── docker-compose.yml
├── .env.example              ← copy this to .env before running
├── README.md
└── webapp/
    ├── Dockerfile
    ├── entrypoint.sh         # waits for DB, runs migrations, starts Gunicorn
    ├── requirements.txt
    ├── manage.py
    ├── config/               # Django settings, URLs, WSGI
    ├── chat/                 # models, views, admin, templates
    └── scraper/              # management command: python manage.py scrape
```

---

## API Reference

### POST `/api/chat/`

```json
// Request
{ "question": "Which departments can Computer Engineering students apply to for a double major?", "conversation_id": null }

// Response
{ "answer": "Computer Engineering students can apply for a Double Major in: Biomedical Engineering, Molecular Biology and Genetics, Psychology, Sociology, and Health Management.", "conversation_id": 1 }
```

### GET `/api/conversation/<id>/`

Returns a conversation with all its messages.
