\# ACU AI Chatbot



A Django-based AI chatbot for Acibadem University, focused on double major program information.

Containerized with Docker and Docker Compose.



\## Team Members

\- Selin Yagmur Tugtag - 221401703

\- Mehmet Emir Altinsoy - 231401704



\## Tech Stack

\- Django 4.2

\- PostgreSQL 15

\- Ollama + Llama 3.2 3B



\## Setup



1\. Clone the repository

2\. Copy `.env.example` to `.env`

3\. Run: `docker-compose up --build`

4\. Run scraper: `docker-compose exec webapp python manage.py scrape`

5\. Visit: http://localhost:8000



\## Project Structure

```

acibadem-chatbot/

├── docker-compose.yml

├── .env.example

├── README.md

└── webapp/

&#x20;   ├── Dockerfile

&#x20;   ├── requirements.txt

&#x20;   ├── manage.py

&#x20;   ├── config/

&#x20;   ├── chat/

&#x20;   ├── scraper/

&#x20;   └── templates/

```





