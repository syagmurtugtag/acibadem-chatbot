"""Management command: scrape ACU OBS Bologna programs via Playwright.

Examples
--------

    # Scrape every program that's not already in the KB
    python manage.py scrape_obs

    # Refresh everything, even programs already cached
    python manage.py scrape_obs --refresh

    # Limit scope (useful while iterating)
    python manage.py scrape_obs --max-programs 3
"""

from django.core.management.base import BaseCommand

from scraper.obs_crawler import crawl_obs_bologna


class Command(BaseCommand):
    help = "Scrape ACU's OBS Bologna interface (programs + curricula) using a real browser."

    def add_arguments(self, parser):
        parser.add_argument(
            "--refresh",
            action="store_true",
            help="Re-scrape programs already present in the knowledge base.",
        )
        parser.add_argument(
            "--max-programs",
            type=int,
            default=0,
            help="Maximum number of programs to scrape this run (0 = no limit).",
        )

    def handle(self, *args, **options):
        max_programs = options["max_programs"] or None
        only_missing = not options["refresh"]

        result = crawl_obs_bologna(
            logger=self.stdout.write,
            only_missing=only_missing,
            max_programs=max_programs,
        )

        self.stdout.write(self.style.SUCCESS(
            "OBS scrape finished. "
            f"Programs processed: {result['programs']}, "
            f"records saved: {result['saved']}, "
            f"skipped: {result['skipped']}, "
            f"failed: {result['failed']}."
        ))
