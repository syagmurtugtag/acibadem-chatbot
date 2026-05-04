# ACU AI Academic Assistant

An official-source AI chatbot for Acibadem University.

The system answers questions about Acibadem University academic information, Double Major (CAP), Minor programs, application requirements, curricula, department heads, faculty information, and library information by using only official ACU web pages, official PDFs, and OBS/Bologna pages.

It is not a keyword-only search project. The chatbot uses semantic retrieval with vector embeddings, official-source evidence extraction, and a local LLM-based response generation step.

> CSE 322 - Cloud Computing | Spring 2026

---

## Team Members

| Name | Student ID |
|---|---|
| Selin Yagmur Tugtag | 221401703 |
| Mehmet Emir Altinsoy | 231401704 |

---

## Key Features

- Dockerized Django web application
- PostgreSQL database with pgvector support
- Local LLM inference with Ollama and `llama3.2:3b`
- Semantic retrieval with `nomic-embed-text` embeddings
- Retrieval-Augmented Generation (RAG) pipeline over official ACU sources
- Playwright-based OBS/Bologna scraper for JavaScript-rendered curriculum pages
- BeautifulSoup-based crawler for official ACU web pages
- PDF table extraction for CAP/Minor program lists
- Official regulation extraction for CAP/Minor application requirements
- LLM-based grounded answer generation with validation against retrieved evidence
- Response formatting layer for readable bullet points and short sections
- Chat history and stored conversations
- REST chat API for frontend communication
- Django admin tools for knowledge-base inspection and scraping

---

## Official Data Sources

The chatbot is designed to use only official Acibadem University sources:

- `https://www.acibadem.edu.tr/`
- official ACU PDF documents
- `https://obs.acibadem.edu.tr/oibs/bologna/`

Manual hard-coded question-answer records are not used as the knowledge source. Facts are retrieved from the scraped knowledge base and then passed through semantic retrieval, structured evidence extraction, grounded LLM generation, validation, and formatting steps.

---

## Quick Start

Requirements:

- Docker Desktop installed and running
- Internet connection on first run
- Enough disk space for Docker images, Ollama models, and Playwright Chromium

### 1. Clone the repository

```bash
git clone https://github.com/syagmurtugtag/acibadem-chatbot.git
cd acibadem-chatbot
```

### 2. Create the `.env` file

Linux/macOS:

```bash
cp .env.example .env
```

Windows Command Prompt:

```cmd
copy .env.example .env
```

The default values are enough for local development and classroom demos. The default chat model is `llama3.2:3b` because it is lightweight enough for a 16 GB RAM laptop while still providing good Turkish output for this project.

### 3. Start the full system

```bash
docker compose up --build
```

The first run can take several minutes because Docker downloads base images, Ollama pulls local models, Playwright installs Chromium, and the knowledge base starts bootstrapping.

If you change the model in `.env`, rebuild/restart Docker Compose so both the web app and `ollama-init` use the same model value.

### 4. Open the application

- Chatbot: [http://localhost:8000](http://localhost:8000)
- Admin panel: [http://localhost:8000/admin/](http://localhost:8000/admin/)
- Health check: [http://localhost:8000/health/](http://localhost:8000/health/)

Create an admin user:

```bash
docker compose exec webapp python manage.py createsuperuser
```

---

## Startup Behavior

The project is intended to run with one command:

```bash
docker compose up --build
```

On startup:

- PostgreSQL with pgvector starts.
- Ollama starts.
- `ollama-init` pulls:
  - `llama3.2:3b`
  - `nomic-embed-text`
- Django runs migrations.
- Static files are collected.
- If the knowledge base is empty, recommended official ACU sources are imported in the background.
- Missing embeddings are generated in the background.
- If proper OBS records are missing, the Playwright OBS scraper starts in the background.
- Gunicorn serves the Django application on port `8000`.

Useful log commands:

```bash
docker compose logs -f webapp
docker compose exec webapp tail -f /tmp/scrape.log
docker compose exec webapp tail -f /tmp/scrape_obs.log
docker compose exec webapp tail -f /tmp/embeddings.log
```

---

## RAG Pipeline

The chatbot uses a guarded Retrieval-Augmented Generation pipeline. The goal is to answer from official evidence while still generating a readable answer with the local language model.

1. **Source loading**
   - ACU website pages are crawled with requests and BeautifulSoup.
   - Official PDFs are parsed with PDF tools.
   - OBS/Bologna curriculum pages are rendered with Playwright because they depend on JavaScript.

2. **Knowledge storage**
   - Scraped pages, PDFs, and OBS records are stored in PostgreSQL.
   - Each record can store a pgvector embedding.

3. **Retrieval**
   - User questions are analyzed for intent.
   - Semantic embeddings are generated with `nomic-embed-text`.
   - pgvector cosine distance retrieves candidate records.
   - Lexical and semantic scoring re-ranks passages.

4. **Structured extraction**
   - Department and dean questions use official department and faculty management pages.
   - Curriculum and common-course questions use OBS course tables.
   - CAP/Minor option questions use official program-list PDF tables.
   - CAP/Minor requirement questions use official regulation sections.
   - Library questions use official ACU library pages.

5. **Grounded generation**
   - Retrieved evidence is passed to the local LLM through a controlled prompt.
   - The model is instructed to rewrite and structure the answer without adding unsupported facts or copying raw source text.

6. **Validation and formatting**
   - Answers are checked against retrieved evidence to reduce hallucinations.
   - Final responses are formatted into short sections and bullet points.
   - If the LLM output is empty, unsupported, or linguistically broken, the system falls back to the most reliable official evidence instead of inventing an answer.

---

## OBS/Bologna Integration

ACU OBS/Bologna pages are ASP.NET WebForms pages that often require JavaScript rendering. A simple `requests` crawler cannot reliably extract their tables.

For this reason, the project includes a Playwright scraper:

```bash
docker compose exec webapp python manage.py scrape_obs
```

Useful variants:

```bash
docker compose exec webapp python manage.py scrape_obs --refresh
docker compose exec webapp python manage.py scrape_obs --max-programs 3
docker compose exec webapp python manage.py fix_obs_titles
```

The scraper discovers OBS `curSunit` program IDs, renders curriculum/about pages, extracts the visible text, and saves canonical OBS records into the knowledge base.

---

## Knowledge Base Commands

Import recommended ACU sources:

```bash
docker compose exec webapp python manage.py scrape
```

Scrape OBS/Bologna pages:

```bash
docker compose exec webapp python manage.py scrape_obs
```

Generate missing embeddings:

```bash
docker compose exec webapp python manage.py generate_embeddings
```

Refresh all embeddings:

```bash
docker compose exec webapp python manage.py generate_embeddings --all
```

Fix canonical OBS titles:

```bash
docker compose exec webapp python manage.py fix_obs_titles
```

---

## Example Questions

Turkish examples:

- `Bilgisayar Muhendisligi ogrencileri hangi bolumlerle CAP yapabilir?`
- `Bilgisayar Muhendisligi ogrencileri hangi bolumlerle yandal yapabilir?`
- `CAP basvuru gereklilikleri nelerdir?`
- `Yandal basvuru kosullari nelerdir?`
- `Bilgisayar Muhendisligi bolum baskani kimdir?`
- `Molekuler Biyoloji ve Genetik bolum baskani kimdir?`
- `Muhendislik ve Doga Bilimleri Fakultesi dekani kimdir?`
- `Acibadem Universitesi Muhendislik ve Doga Bilimleri Fakultesinde kac bolum var?`
- `Bilgisayar Muhendisligi ucuncu yariyilda hangi dersler var?`
- `Biyomedikal Muhendisligi ile Bilgisayar Muhendisligi birinci sinifta hangi dersler ortak?`
- `Acibadem Universitesi kutuphanesi hakkinda bilgi verir misin?`

English examples:

- `Which programs can Computer Engineering students apply to for a double major?`
- `What are the double major application requirements?`
- `Who is the head of Molecular Biology and Genetics?`
- `Who is the head of Computer Engineering?`

---

## API Reference

### POST `/api/chat/`

Request:

```json
{
  "question": "CAP basvuru gereklilikleri nelerdir?",
  "conversation_id": null
}
```

Response:

```json
{
  "answer": "CAP basvuru/kabul gereklilikleri resmi yonergeye gore sunlardir:\n- ...",
  "conversation_id": 1
}
```

### GET `/api/conversation/<id>/`

Returns one stored conversation and its messages.

---

## Admin Tools

The Django admin panel can be used to inspect:

- Knowledge base records
- Chat messages
- Conversations

Knowledge-base admin actions include official-source scraping helpers. Manual local file upload and manual answer records are intentionally avoided for the main workflow so the chatbot remains evidence-driven instead of manually scripted.

---

## Project Structure

```text
acibadem-chatbot/
|-- docker-compose.yml
|-- .dockerignore
|-- .env.example
|-- README.md
`-- webapp/
    |-- Dockerfile
    |-- entrypoint.sh
    |-- manage.py
    |-- requirements.txt
    |-- chat/
    |   |-- admin.py
    |   |-- embeddings.py
    |   |-- models.py
    |   |-- views.py
    |   |-- management/commands/generate_embeddings.py
    |   `-- migrations/
    |-- config/
    |   |-- settings.py
    |   |-- urls.py
    |   `-- wsgi.py
    |-- scraper/
    |   |-- site_crawler.py
    |   |-- obs_crawler.py
    |   `-- management/commands/
    |       |-- scrape.py
    |       |-- scrape_obs.py
    |       `-- fix_obs_titles.py
    `-- templates/
        `-- chat/index.html
```

---

## Docker Architecture

Services:

- `db`: PostgreSQL 15 with pgvector
- `ollama`: local Ollama server
- `ollama-init`: pulls required models
- `webapp`: Django + Gunicorn app

Docker concepts used:

- Multi-service Docker Compose orchestration
- Custom bridge network
- Named volumes for persistent PostgreSQL and Ollama data
- Health checks for startup coordination
- Reproducible webapp image with pinned Python dependencies
- Background startup tasks for scraping and embedding generation

---

## Tech Stack

| Layer | Technology |
|---|---|
| Web Framework | Django 4.2 |
| Database | PostgreSQL 15 + pgvector |
| LLM | Llama 3.2 3B via Ollama |
| Embeddings | nomic-embed-text via Ollama |
| RAG Storage | PostgreSQL knowledge base + vector embeddings |
| Web Scraping | requests, BeautifulSoup |
| OBS Scraping | Playwright Chromium |
| PDF Parsing | PyPDF2, pdfplumber |
| Server | Gunicorn |
| Containerization | Docker, Docker Compose |

---

## Demo Checklist

For a classroom demo, a clean run should show:

- `docker compose up --build` starts PostgreSQL, Ollama, and Django.
- The chatbot opens at [http://localhost:8000](http://localhost:8000).
- The admin panel opens at [http://localhost:8000/admin/](http://localhost:8000/admin/).
- Questions are answered from official ACU/OBS/PDF evidence.
- Answers are generated in a readable bullet-point format.
- Chat history is stored and can be revisited.
- Semantic search is available through pgvector embeddings.

---

## Current Limitations

- First startup can take time because models, Chromium, scraping, and embeddings are initialized.
- OBS scraping runs in the background; some curriculum answers may improve after it finishes.
- The local 3B model is small, so validation and formatting guardrails are used to keep answers grounded.
- The project focuses on official ACU academic information, not general open-domain chat.
