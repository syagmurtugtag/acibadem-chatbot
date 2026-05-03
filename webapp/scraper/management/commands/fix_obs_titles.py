"""Rebuild canonical titles for existing OBS Bologna KnowledgeBase records.

The first version of obs_crawler saved records with generic titles such as
``"Program 22 - Müfredat (OBS)"``. The matcher then could not tell which
faculty / department a record belonged to, which led to wrong-department
hits (e.g. an "Ameliyathane Hizmetleri" page surfacing for a Biyomedikal
Mühendisliği question because its course list happened to mention
"Biyomedikal Cihaz Teknolojisi").

This command re-derives a clean title from the first non-empty line of
each OBS record's body (which OBS Bologna renders as
``"<faculty> / <department> - Dersler"``) and updates the row in place.
No re-scraping required.
"""

from django.core.management.base import BaseCommand

from chat.models import KnowledgeBase
from scraper.obs_crawler import OBS_HOST, parse_obs_canonical_name


SUFFIX_BY_TOPIC = {
    "curriculum": "Müfredat (OBS)",
    "department": "Program Bilgileri (OBS)",
}


class Command(BaseCommand):
    help = "Rebuild OBS Bologna record titles from the rendered page body."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print what would change without saving anything.",
        )

    def handle(self, *args, **options):
        dry = options["dry_run"]
        updated = 0
        skipped = 0

        qs = KnowledgeBase.objects.filter(url__contains=OBS_HOST).order_by("id")
        total = qs.count()
        self.stdout.write(f"Scanning {total} OBS records...")

        for record in qs:
            canonical = parse_obs_canonical_name(record.content or "")
            if not canonical:
                skipped += 1
                continue

            suffix = SUFFIX_BY_TOPIC.get(record.topic, "OBS")
            new_title = f"{canonical} - {suffix}"[:300]

            if new_title == record.title:
                skipped += 1
                continue

            self.stdout.write(
                f"id={record.id} OLD={record.title!r}  NEW={new_title!r}"
            )

            if not dry:
                record.title = new_title
                record.save(update_fields=["title"])
            updated += 1

        verb = "Would update" if dry else "Updated"
        self.stdout.write(self.style.SUCCESS(
            f"{verb} {updated} record(s); skipped {skipped} (unchanged or no canonical name)."
        ))
