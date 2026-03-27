from django.core.management.base import BaseCommand

from chat.models import KnowledgeBase
from scraper.site_crawler import AcibademSiteCrawler, crawl_default_acu_sources


DEFAULT_START_URL = "https://www.acibadem.edu.tr/"

SEED_DATA = [
    {
        "url": "https://www.acibadem.edu.tr/en/double-major",
        "title": "What is Double Major",
        "topic": "double_major",
        "content": (
            "Double Major is a program at Acibadem University that allows students who meet the "
            "eligibility requirements to pursue a second undergraduate degree alongside their "
            "primary program."
        ),
    },
    {
        "url": "https://www.acibadem.edu.tr/en/minor",
        "title": "What is Minor",
        "topic": "minor",
        "content": (
            "Minor is a supplementary academic program at Acibadem University that allows students "
            "to gain formal recognition in a secondary field of study without pursuing a full "
            "second degree."
        ),
    },
]


class Command(BaseCommand):
    help = "Crawl the Acibadem University website and import relevant HTML/PDF content."

    def add_arguments(self, parser):
        parser.add_argument(
            "--start-url",
            default="",
            help="Starting URL for the crawl. Only acibadem.edu.tr links will be followed.",
        )
        parser.add_argument(
            "--max-pages",
            type=int,
            default=20,
            help="Maximum number of HTML pages to visit.",
        )
        parser.add_argument(
            "--max-pdfs",
            type=int,
            default=10,
            help="Maximum number of PDFs to parse.",
        )
        parser.add_argument(
            "--load-seed-data",
            action="store_true",
            help="Also load the small built-in English seed records after crawling.",
        )

    def handle(self, *args, **options):
        start_url = options["start_url"]
        max_pages = options["max_pages"]
        max_pdfs = options["max_pdfs"]
        load_seed_data = options["load_seed_data"]

        if start_url:
            self.stdout.write(f"Starting crawl from: {start_url}")
            crawler = AcibademSiteCrawler(
                start_url,
                max_pages=max_pages,
                max_pdfs=max_pdfs,
                logger=self.stdout.write,
            )
            stats = crawler.crawl()
        else:
            self.stdout.write("No start URL provided. Importing recommended Acibadem sources...")
            stats = crawl_default_acu_sources(
                logger=self.stdout.write,
                max_pages_per_source=max_pages,
                max_pdfs_per_source=max_pdfs,
            )

        self.stdout.write(
            self.style.SUCCESS(
                (
                    f"Crawl completed. Saved {stats.html_saved} HTML pages and {stats.pdf_saved} PDFs. "
                    f"Visited {stats.html_visited} HTML pages and {stats.pdf_visited} PDFs."
                )
            )
        )

        if stats.failed:
            self.stdout.write(self.style.WARNING(f"{stats.failed} URLs could not be processed."))

        if load_seed_data:
            self.stdout.write("Loading optional seed data...")
            for entry in SEED_DATA:
                KnowledgeBase.objects.update_or_create(
                    url=entry["url"],
                    defaults={
                        "title": entry["title"],
                        "content": entry["content"],
                        "topic": entry["topic"],
                    },
                )
            self.stdout.write(self.style.SUCCESS("Seed data loaded."))
