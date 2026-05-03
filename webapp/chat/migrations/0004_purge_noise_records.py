"""One-shot cleanup: remove KB records that came from noisy URL paths.

Earlier versions of the crawler followed news / announcement / event /
strategic-plan / virtual-tour links, polluting the knowledge base with
content that the RAG layer kept matching by accident (e.g. an unrelated
news headline being returned as a "compulsory course").

This migration deletes those records so the bot stops surfacing them.
The crawler's normalize_url now rejects the same patterns up front, so
they will not come back on the next scrape.
"""

from django.db import migrations


NOISE_URL_PATTERNS = (
    "/haberler",
    "/duyurular",
    "/etkinlikler",
    "/galeri",
    "/medya",
    "/blog",
    "/stratejik-plan",
    "/sss",
    "/iletisim",
    "/node/",
)


NOISE_HOST_FRAGMENTS = (
    "tour.acibadem.edu.tr",
    "bademnet.acibadem.edu.tr",
)


def purge_noise(apps, schema_editor):
    KnowledgeBase = apps.get_model("chat", "KnowledgeBase")

    qs = KnowledgeBase.objects.all()
    to_delete = []
    for record in qs:
        url = (record.url or "").lower()
        if not url:
            continue
        if any(pattern in url for pattern in NOISE_URL_PATTERNS):
            to_delete.append(record.pk)
            continue
        if any(host in url for host in NOISE_HOST_FRAGMENTS):
            to_delete.append(record.pk)

    if to_delete:
        KnowledgeBase.objects.filter(pk__in=to_delete).delete()


def noop(apps, schema_editor):
    # Reverse is a no-op — we never want the noise back.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("chat", "0003_seed_department_chairs"),
    ]

    operations = [
        migrations.RunPython(purge_noise, noop),
    ]
