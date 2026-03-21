# ACU AI Chatbot

A Django-based AI chatbot for Acibadem University, focused on double major and minor program information.

Containerized with Docker and Docker Compose.

## Team Members

- Selin Yagmur Tugtag - 221401703
- Mehmet Emir Altinsoy - 231401704

## Tech Stack

- Django 4.2
- PostgreSQL 15
- Ollama + Llama 3.2 3B

## Features

- Answers questions about double major and minor programs
- Uses a local LLM through Ollama
- Retrieves relevant information from the knowledge base
- Supports definition questions, option-list questions, and yes/no eligibility questions
- Stores chat history in the database

## Setup

1. Clone the repository
2. Copy `.env.example` to `.env`
3. Run:

   ```bash
   docker compose up --build

4. Run scraper:

   ```bash
   docker compose exec webapp python manage.py scrape

5. Visit: 
    http://localhost:8000


## Project Structure

acibadem-chatbot/
├── docker-compose.yml
├── .env.example
├── README.md
└── webapp/
    ├── Dockerfile
    ├── requirements.txt
    ├── manage.py
    ├── config/
    ├── chat/
    ├── scraper/
    └── templates/