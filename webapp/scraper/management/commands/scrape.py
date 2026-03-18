import time
import requests
from bs4 import BeautifulSoup
from django.core.management.base import BaseCommand
from chat.models import KnowledgeBase


class Command(BaseCommand):
    help = 'Scrape Acibadem University website for double major information'

    def handle(self, *args, **options):
        self.stdout.write('Starting scraper...')

        pages = [
            {
                'url': 'https://www.acibadem.edu.tr/akademik/cift-anadal-ve-yandal-programlari/',
                'topic': 'double_major'
            },
            {
                'url': 'https://www.acibadem.edu.tr/ogrenci-hayati/',
                'topic': 'student_life'
            },
            {
                'url': 'https://www.acibadem.edu.tr/akademik/',
                'topic': 'academic'
            },
        ]

        headers = {
            'User-Agent': 'Mozilla/5.0 (compatible; ACU-Chatbot/1.0)'
        }

        for page in pages:
            try:
                self.stdout.write(f"Scraping: {page['url']}")
                response = requests.get(page['url'], headers=headers, timeout=10)
                response.raise_for_status()

                soup = BeautifulSoup(response.text, 'html.parser')

                # Remove scripts and styles
                for tag in soup(['script', 'style', 'nav', 'footer']):
                    tag.decompose()

                title = soup.title.string.strip() if soup.title else 'No title'
                content = soup.get_text(separator=' ', strip=True)

                # Limit content length
                content = content[:3000]

                KnowledgeBase.objects.update_or_create(
                    url=page['url'],
                    defaults={
                        'title': title,
                        'content': content,
                        'topic': page['topic']
                    }
                )

                self.stdout.write(self.style.SUCCESS(f"Saved: {title}"))
                time.sleep(2)

            except Exception as e:
                self.stdout.write(self.style.ERROR(f"Failed: {page['url']} - {str(e)}"))

        self.stdout.write(self.style.SUCCESS('Scraping completed!'))