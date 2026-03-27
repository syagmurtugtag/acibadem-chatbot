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

    def save_record(self, url, title, content, topic):
        KnowledgeBase.objects.update_or_create(
            url=url,
            defaults={'title': title, 'content': content, 'topic': topic}
        )
        self.stdout.write(self.style.SUCCESS(f"Saved: {title}"))

    def handle(self, *args, **options):
        self.stdout.write('Starting scraper...')

        headers = {'User-Agent': 'Mozilla/5.0 (compatible; ACU-Chatbot/1.0)'}

        # ----------------------------------------------------------------
        # PART 1 – Turkish pages (general regulations / announcements)
        # ----------------------------------------------------------------
        tr_keywords = [
            'çift anadal', 'yandal', 'çap',
            'başvuru', 'koşul', 'şart', 'gpa',
            'ortalama', 'kredi', 'akts',
            'program', 'öğrenci', 'yönerge'
        ]

        tr_pages = [
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

        tr_pdfs = [
            {
                'url': 'https://www.acibadem.edu.tr/sites/default/files/document/2025/ACU%20%C3%87ift%20Anadal%20Yandal%20Y%C3%B6nergesi%2014.11.2023.pdf',
                'topic': 'double_major_minor',
                'title': 'ACU Cift Anadal Yandal Yonergesi'
            },
        ]

        for page in tr_pages:
            try:
                self.stdout.write(f"Scraping: {page['url']}")
                response = requests.get(page['url'], headers=headers, timeout=15)
                response.raise_for_status()
                soup = BeautifulSoup(response.text, 'html.parser')
                for tag in soup(['script', 'style', 'nav', 'footer', 'header']):
                    tag.decompose()
                raw_text = soup.get_text(separator='\n', strip=True)
                filtered_text = self.extract_relevant_lines(raw_text, tr_keywords) or raw_text
                self.save_record(page['url'], page['title'],
                                 self.clean_text(filtered_text, 4000), page['topic'])
                time.sleep(2)
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"Failed: {page['url']} - {str(e)}"))

        for pdf in tr_pdfs:
            try:
                self.stdout.write(f"Scraping PDF: {pdf['url']}")
                response = requests.get(pdf['url'], headers=headers, timeout=30)
                response.raise_for_status()
                reader = PdfReader(BytesIO(response.content))
                page_texts = []
                for i, p in enumerate(reader.pages):
                    text = p.extract_text()
                    if text:
                        page_texts.append(f"Page {i+1}: {text}")
                raw_pdf_text = '\n'.join(page_texts)
                filtered = self.extract_relevant_lines(raw_pdf_text, tr_keywords) or raw_pdf_text
                self.save_record(pdf['url'], pdf['title'],
                                 self.clean_text(filtered, 6000), pdf['topic'])
                time.sleep(2)
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"Failed PDF: {pdf['url']} - {str(e)}"))

        # ----------------------------------------------------------------
        # PART 2 – English seed data (definitions + per-department options)
        # ----------------------------------------------------------------
        self.stdout.write("Loading English seed data...")

        seed_data = [
            # --- Definitions ---
            {
                'url': 'https://www.acibadem.edu.tr/en/double-major',
                'title': 'What is Double Major',
                'topic': 'double_major',
                'content': (
                    "Double Major (Çift Anadal) is a program at Acibadem University that allows "
                    "students who meet the eligibility requirements to simultaneously pursue a "
                    "second undergraduate degree alongside their primary program. "
                    "To be eligible, students must have completed at least the first two semesters "
                    "of their primary program, have a minimum GPA of 2.72 out of 4.00, and must "
                    "not be on academic probation. The double major program adds additional credits "
                    "to the student's course load and typically extends the total duration of study."
                )
            },
            {
                'url': 'https://www.acibadem.edu.tr/en/minor',
                'title': 'What is Minor',
                'topic': 'minor',
                'content': (
                    "Minor (Yandal) is a supplementary academic program at Acibadem University "
                    "that allows students to gain formal recognition in a secondary field of study "
                    "without pursuing a full second degree. "
                    "To be eligible for a minor, students must have completed at least two semesters, "
                    "maintain a minimum GPA of 2.00 out of 4.00, and must not be on academic probation. "
                    "A minor program consists of a defined set of courses from the host department "
                    "and is noted on the student's transcript upon successful completion."
                )
            },

            # --- Double Major options per department ---
            {
                'url': 'https://www.acibadem.edu.tr/en/double-major/computer-engineering',
                'title': 'Computer Engineering Double Major Options',
                'topic': 'double_major_options',
                'content': (
                    "Computer Engineering students at Acibadem University can apply for a Double Major "
                    "in the following departments: "
                    "Biomedical Engineering, Molecular Biology and Genetics, Psychology, "
                    "Sociology, Health Management."
                )
            },
            {
                'url': 'https://www.acibadem.edu.tr/en/double-major/biomedical-engineering',
                'title': 'Biomedical Engineering Double Major Options',
                'topic': 'double_major_options',
                'content': (
                    "Biomedical Engineering students at Acibadem University can apply for a Double Major "
                    "in the following departments: "
                    "Computer Engineering, Molecular Biology and Genetics, Psychology, "
                    "Sociology, Health Management."
                )
            },
            {
                'url': 'https://www.acibadem.edu.tr/en/double-major/molecular-biology-genetics',
                'title': 'Molecular Biology and Genetics Double Major Options',
                'topic': 'double_major_options',
                'content': (
                    "Molecular Biology and Genetics students at Acibadem University can apply for a Double Major "
                    "in the following departments: "
                    "Computer Engineering, Biomedical Engineering, Psychology, "
                    "Sociology, Health Management, Nutrition and Dietetics."
                )
            },
            {
                'url': 'https://www.acibadem.edu.tr/en/double-major/psychology',
                'title': 'Psychology Double Major Options',
                'topic': 'double_major_options',
                'content': (
                    "Psychology students at Acibadem University can apply for a Double Major "
                    "in the following departments: "
                    "Sociology, Health Management, Molecular Biology and Genetics, Nutrition and Dietetics."
                )
            },
            {
                'url': 'https://www.acibadem.edu.tr/en/double-major/nursing',
                'title': 'Nursing Double Major Options',
                'topic': 'double_major_options',
                'content': (
                    "Nursing students at Acibadem University can apply for a Double Major "
                    "in the following departments: "
                    "Health Management, Nutrition and Dietetics, Psychology, Sociology."
                )
            },
            {
                'url': 'https://www.acibadem.edu.tr/en/double-major/nutrition-dietetics',
                'title': 'Nutrition and Dietetics Double Major Options',
                'topic': 'double_major_options',
                'content': (
                    "Nutrition and Dietetics students at Acibadem University can apply for a Double Major "
                    "in the following departments: "
                    "Health Management, Psychology, Sociology, Nursing, Molecular Biology and Genetics."
                )
            },
            {
                'url': 'https://www.acibadem.edu.tr/en/double-major/physiotherapy',
                'title': 'Physiotherapy and Rehabilitation Double Major Options',
                'topic': 'double_major_options',
                'content': (
                    "Physiotherapy and Rehabilitation students at Acibadem University can apply for a Double Major "
                    "in the following departments: "
                    "Health Management, Psychology, Sociology, Nursing, Nutrition and Dietetics."
                )
            },

            # --- Minor options per department ---
            {
                'url': 'https://www.acibadem.edu.tr/en/minor/computer-engineering',
                'title': 'Computer Engineering Minor Options',
                'topic': 'minor_options',
                'content': (
                    "Computer Engineering students at Acibadem University can apply for a Minor "
                    "in the following departments: "
                    "Biomedical Engineering, Psychology, Sociology, Health Management, "
                    "Molecular Biology and Genetics."
                )
            },
            {
                'url': 'https://www.acibadem.edu.tr/en/minor/biomedical-engineering',
                'title': 'Biomedical Engineering Minor Options',
                'topic': 'minor_options',
                'content': (
                    "Biomedical Engineering students at Acibadem University can apply for a Minor "
                    "in the following departments: "
                    "Computer Engineering, Psychology, Sociology, Health Management, "
                    "Molecular Biology and Genetics."
                )
            },
            {
                'url': 'https://www.acibadem.edu.tr/en/minor/nursing',
                'title': 'Nursing Minor Options',
                'topic': 'minor_options',
                'content': (
                    "Nursing students at Acibadem University can apply for a Minor "
                    "in the following departments: "
                    "Health Management, Psychology, Sociology, Nutrition and Dietetics."
                )
            },
            {
                'url': 'https://www.acibadem.edu.tr/en/minor/psychology',
                'title': 'Psychology Minor Options',
                'topic': 'minor_options',
                'content': (
                    "Psychology students at Acibadem University can apply for a Minor "
                    "in the following departments: "
                    "Sociology, Health Management, Molecular Biology and Genetics, Nutrition and Dietetics."
                )
            },
            {
                'url': 'https://www.acibadem.edu.tr/en/minor/nutrition-dietetics',
                'title': 'Nutrition and Dietetics Minor Options',
                'topic': 'minor_options',
                'content': (
                    "Nutrition and Dietetics students at Acibadem University can apply for a Minor "
                    "in the following departments: "
                    "Health Management, Psychology, Sociology, Nursing."
                )
            },
            {
                'url': 'https://www.acibadem.edu.tr/en/minor/physiotherapy',
                'title': 'Physiotherapy and Rehabilitation Minor Options',
                'topic': 'minor_options',
                'content': (
                    "Physiotherapy and Rehabilitation students at Acibadem University can apply for a Minor "
                    "in the following departments: "
                    "Health Management, Psychology, Sociology, Nursing, Nutrition and Dietetics."
                )
            },

            # --- Application requirements ---
            {
                'url': 'https://www.acibadem.edu.tr/en/double-major/requirements',
                'title': 'Double Major Application Requirements',
                'topic': 'double_major',
                'content': (
                    "To apply for the Double Major program at Acibadem University, students must meet "
                    "the following requirements: "
                    "1) Must have completed at least the first two semesters of the primary program. "
                    "2) Minimum cumulative GPA of 2.72 out of 4.00 at the time of application. "
                    "3) Must not be on academic probation or disciplinary suspension. "
                    "4) The second program must be approved as compatible with the primary program "
                    "according to the university's double major pairing list. "
                    "Applications are accepted once per semester, during the announced application period."
                )
            },
            {
                'url': 'https://www.acibadem.edu.tr/en/minor/requirements',
                'title': 'Minor Application Requirements',
                'topic': 'minor',
                'content': (
                    "To apply for the Minor program at Acibadem University, students must meet "
                    "the following requirements: "
                    "1) Must have completed at least the first two semesters of the primary program. "
                    "2) Minimum cumulative GPA of 2.00 out of 4.00 at the time of application. "
                    "3) Must not be on academic probation or disciplinary suspension. "
                    "4) Students may only pursue one minor at a time. "
                    "Applications are accepted once per semester, during the announced application period."
                )
            },
        ]

        for entry in seed_data:
            self.save_record(entry['url'], entry['title'], entry['content'], entry['topic'])

        self.stdout.write(self.style.SUCCESS('Scraping and seeding completed!'))
