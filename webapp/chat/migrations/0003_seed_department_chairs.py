"""Cleanup migration.

The previous version of this file inserted hard-coded department chair
records as a temporary band-aid. That approach was wrong: a chatbot for
the entire ACU should not have specific answers compiled into its source
code. The right place for facts is the live knowledge base, populated by
the scraper.

This migration is now a cleanup step: it removes any seed:// records that
the previous version of the file may have inserted, so the knowledge base
contains only data that came from the scraper / admin tools.

You can safely delete this file once you have run `migrate` at least once
on every environment that previously applied the old version.
"""

from django.db import migrations


def remove_seed_records(apps, schema_editor):
    KnowledgeBase = apps.get_model("chat", "KnowledgeBase")
    KnowledgeBase.objects.filter(url__startswith="seed://").delete()


def noop(apps, schema_editor):
    # Reverse is a no-op — we never want the seeds back.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("chat", "0002_knowledgebase_pdf_file_alter_knowledgebase_url"),
    ]

    operations = [
        migrations.RunPython(remove_seed_records, noop),
    ]
