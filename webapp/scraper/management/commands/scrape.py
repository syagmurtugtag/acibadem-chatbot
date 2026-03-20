import time
import requests
from bs4 import BeautifulSoup
from io import BytesIO
from PyPDF2 import PdfReader
from django.core.management.base import BaseCommand
from chat.models import KnowledgeBase


class Command(BaseCommand):
    help = 'Scrape Acibadem University website for double major and minor information'

    def clean_text(self, text, limit=4000):
        text = ' '.join(text.split())
        return text[:limit]

    def extract_relevant_lines(self, text, keywords):
        lines = text.split('\n')
        relevant = []

        for line in lines:
            line_clean = line.strip()
            if not line_clean:
                continue

            lower_line = line_clean.lower()
            if any(keyword in lower_line for keyword in keywords):
                relevant.append(line_clean)

        return ' '.join(relevant)

    def handle(self, *args, **options):
        self.stdout.write('Starting scraper...')

        headers = {
            'User-Agent': 'Mozilla/5.0 (compatible; ACU-Chatbot/1.0)'
        }

        keywords = [
            'çift anadal', 'yandal', 'çap',
            'başvuru', 'koşul', 'şart', 'gpa',
            'ortalama', 'kredi', 'akts',
            'program', 'öğrenci', 'yönerge'
        ]

        pages = [
            {
                'url': 'https://www.acibadem.edu.tr/ogrenci/ogrenci-isleri/cift-anadal-yandal-programlari',
                'topic': 'double_major_minor',
                'title': 'Cift Anadal ve Yandal Programlari'
            },
            {
                'url': 'https://www.acibadem.edu.tr/duyurular/2024-2025-guz-donemi-cift-anadal-yandal-basvurulari-ve-takvimi',
                'topic': 'double_major_minor',
                'title': 'Cift Anadal Yandal Basvuru Takvimi 2024-2025'
            },
        ]

        pdfs = [
            {
                'url': 'https://www.acibadem.edu.tr/sites/default/files/document/2025/ACU%20%C3%87ift%20Anadal%20Yandal%20Y%C3%B6nergesi%2014.11.2023.pdf',
                'topic': 'double_major_minor',
                'title': 'ACU Cift Anadal Yandal Yonergesi'
            },
        ]

        # Scrape web pages
        for page in pages:
            try:
                self.stdout.write(f"Scraping: {page['url']}")
                response = requests.get(page['url'], headers=headers, timeout=15)
                response.raise_for_status()

                soup = BeautifulSoup(response.text, 'html.parser')

                for tag in soup(['script', 'style', 'nav', 'footer', 'header']):
                    tag.decompose()

                raw_text = soup.get_text(separator='\n', strip=True)
                filtered_text = self.extract_relevant_lines(raw_text, keywords)

                if not filtered_text:
                    filtered_text = raw_text

                content = self.clean_text(filtered_text, limit=4000)

                KnowledgeBase.objects.update_or_create(
                    url=page['url'],
                    defaults={
                        'title': page['title'],
                        'content': content,
                        'topic': page['topic']
                    }
                )

                self.stdout.write(self.style.SUCCESS(f"Saved: {page['title']}"))
                time.sleep(2)

            except Exception as e:
                self.stdout.write(self.style.ERROR(f"Failed: {page['url']} - {str(e)}"))

        # Scrape PDFs
        for pdf in pdfs:
            try:
                self.stdout.write(f"Scraping PDF: {pdf['url']}")
                response = requests.get(pdf['url'], headers=headers, timeout=30)
                response.raise_for_status()

                reader = PdfReader(BytesIO(response.content))
                page_texts = []

                for i, page in enumerate(reader.pages):
                    text = page.extract_text()
                    if text:
                        page_texts.append(f"Page {i+1}: {text}")

                raw_pdf_text = '\n'.join(page_texts)
                filtered_pdf_text = self.extract_relevant_lines(raw_pdf_text, keywords)

                if not filtered_pdf_text:
                    filtered_pdf_text = raw_pdf_text

                content = self.clean_text(filtered_pdf_text, limit=6000)

                KnowledgeBase.objects.update_or_create(
                    url=pdf['url'],
                    defaults={
                        'title': pdf['title'],
                        'content': content,
                        'topic': pdf['topic']
                    }
                )

                self.stdout.write(self.style.SUCCESS(f"Saved PDF: {pdf['title']}"))
                time.sleep(2)

            except Exception as e:
                self.stdout.write(self.style.ERROR(f"Failed PDF: {pdf['url']} - {str(e)}"))

        self.stdout.write(self.style.SUCCESS('Scraping completed!'))