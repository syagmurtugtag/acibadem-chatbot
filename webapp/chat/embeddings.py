import requests
from django.conf import settings


EMBEDDING_MODEL = "nomic-embed-text"
EMBEDDING_DIMENSIONS = 768
EMBEDDING_TEXT_LIMIT = 6000


def build_embedding_text(record):
    return (
        f"Title: {record.title}\n"
        f"Topic: {record.topic}\n"
        f"URL: {record.url}\n"
        f"Content: {(record.content or '')[:EMBEDDING_TEXT_LIMIT]}"
    )


def get_text_embedding(text, timeout=45):
    response = requests.post(
        f"{settings.OLLAMA_URL}/api/embeddings",
        json={
            "model": EMBEDDING_MODEL,
            "prompt": (text or "")[:EMBEDDING_TEXT_LIMIT],
        },
        timeout=timeout,
    )
    response.raise_for_status()

    embedding = response.json().get("embedding")
    if not embedding or len(embedding) != EMBEDDING_DIMENSIONS:
        return None

    return embedding
