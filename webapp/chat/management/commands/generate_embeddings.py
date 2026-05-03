from django.core.management.base import BaseCommand

from chat.embeddings import build_embedding_text, get_text_embedding
from chat.models import KnowledgeBase
from scraper.site_crawler import is_official_acu_source_url


class Command(BaseCommand):
    help = "Generate Ollama embeddings for official ACU/OBS knowledge-base records."

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            default=0,
            help="Maximum number of records to embed. 0 means no limit.",
        )
        parser.add_argument(
            "--all",
            action="store_true",
            help="Regenerate embeddings even when a record already has one.",
        )

    def handle(self, *args, **options):
        queryset = KnowledgeBase.objects.all().order_by("id")

        if not options["all"]:
            queryset = queryset.filter(embedding__isnull=True)

        records = [
            record for record in queryset
            if is_official_acu_source_url(record.url)
        ]

        limit = options["limit"]
        if limit:
            records = records[:limit]

        total = len(records)
        if not total:
            self.stdout.write(self.style.SUCCESS("No embeddings to generate."))
            return

        self.stdout.write(f"Generating embeddings for {total} records...")

        success = 0
        failed = 0
        for index, record in enumerate(records, start=1):
            self.stdout.write(f"[{index}/{total}] {record.title[:80]}")
            try:
                embedding = get_text_embedding(build_embedding_text(record))
                if not embedding:
                    failed += 1
                    self.stdout.write(self.style.WARNING("  skipped: empty embedding"))
                    continue

                record.embedding = embedding
                record.save(update_fields=["embedding"])
                success += 1
            except Exception as exc:
                failed += 1
                self.stdout.write(self.style.WARNING(f"  failed: {exc}"))

        self.stdout.write(self.style.SUCCESS(f"Done. Embedded: {success}, failed: {failed}"))
