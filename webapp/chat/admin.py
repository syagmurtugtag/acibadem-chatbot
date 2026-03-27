import requests
from io import BytesIO

from django import forms
from django.contrib import admin, messages
from django.shortcuts import render, redirect
from django.urls import path
from django.utils.html import format_html

from PyPDF2 import PdfReader
from bs4 import BeautifulSoup

from .models import KnowledgeBase, ChatMessage, Conversation
from scraper.site_crawler import AcibademSiteCrawler, crawl_default_acu_sources


# ---------------------------------------------------------------------------
# Forms
# ---------------------------------------------------------------------------

class PDFUploadForm(forms.Form):
    title = forms.CharField(
        max_length=300,
        label="Title",
        help_text="A descriptive title for this document (e.g. 'Computer Engineering Curriculum 2024')"
    )
    topic = forms.ChoiceField(
        choices=[
            ('general', 'General'),
            ('double_major', 'Double Major'),
            ('minor', 'Minor'),
            ('double_major_options', 'Double Major Options'),
            ('minor_options', 'Minor Options'),
            ('curriculum', 'Curriculum'),
            ('course', 'Course'),
            ('admission', 'Admission'),
        ],
        label="Topic"
    )
    pdf_file = forms.FileField(
        label="PDF File",
        help_text="Upload a PDF file. Its text will be extracted and saved automatically."
    )


class URLScrapeForm(forms.Form):
    title = forms.CharField(
        max_length=300,
        label="Title",
        help_text="A descriptive title for this page"
    )
    url = forms.URLField(
        label="Page URL",
        help_text="Full URL of the page to scrape (e.g. https://www.acibadem.edu.tr/...)"
    )
    topic = forms.ChoiceField(
        choices=[
            ('general', 'General'),
            ('double_major', 'Double Major'),
            ('minor', 'Minor'),
            ('double_major_options', 'Double Major Options'),
            ('minor_options', 'Minor Options'),
            ('curriculum', 'Curriculum'),
            ('course', 'Course'),
            ('admission', 'Admission'),
        ],
        label="Topic"
    )


class PasteTextForm(forms.Form):
    title = forms.CharField(
        max_length=300,
        label="Title",
        help_text="A descriptive title for this content"
    )
    url = forms.CharField(
        max_length=500,
        required=False,
        label="Source URL (optional)",
        help_text="The URL where you copied this text from (for reference only)"
    )
    topic = forms.ChoiceField(
        choices=[
            ('general', 'General'),
            ('double_major', 'Double Major'),
            ('minor', 'Minor'),
            ('double_major_options', 'Double Major Options'),
            ('minor_options', 'Minor Options'),
            ('curriculum', 'Curriculum'),
            ('course', 'Course'),
            ('admission', 'Admission'),
        ],
        label="Topic"
    )
    content = forms.CharField(
        widget=forms.Textarea(attrs={'rows': 20, 'cols': 80}),
        label="Content",
        help_text=(
            "Paste the text here. For OBS pages: open the page in your browser, "
            "press Ctrl+A then Ctrl+C, and paste below."
        )
    )


class CrawlSiteForm(forms.Form):
    start_url = forms.URLField(
        label="Start URL",
        initial="https://www.acibadem.edu.tr/",
        help_text="Crawler will stay inside the acibadem.edu.tr domain and follow HTML/PDF links."
    )
    max_pages = forms.IntegerField(
        min_value=1,
        max_value=100,
        initial=20,
        label="Max HTML pages",
        help_text="Safety limit for how many HTML pages to visit in one crawl."
    )
    max_pdfs = forms.IntegerField(
        min_value=0,
        max_value=50,
        initial=10,
        label="Max PDFs",
        help_text="Safety limit for how many PDF files to parse in one crawl."
    )


# ---------------------------------------------------------------------------
# KnowledgeBase Admin
# ---------------------------------------------------------------------------

@admin.register(KnowledgeBase)
class KnowledgeBaseAdmin(admin.ModelAdmin):
    list_display = ['title', 'topic', 'short_url', 'scraped_at', 'content_length']
    list_filter = ['topic']
    search_fields = ['title', 'content']
    readonly_fields = ['scraped_at']

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path('upload-pdf/', self.admin_site.admin_view(self.upload_pdf_view), name='kb_upload_pdf'),
            path('scrape-url/', self.admin_site.admin_view(self.scrape_url_view), name='kb_scrape_url'),
            path('crawl-site/', self.admin_site.admin_view(self.crawl_site_view), name='kb_crawl_site'),
            path('import-default-sources/', self.admin_site.admin_view(self.import_default_sources_view), name='kb_import_default_sources'),
            path('paste-text/', self.admin_site.admin_view(self.paste_text_view), name='kb_paste_text'),
        ]
        return custom_urls + urls

    # --- View 1: PDF Upload ---
    def upload_pdf_view(self, request):
        if request.method == 'POST':
            form = PDFUploadForm(request.POST, request.FILES)
            if form.is_valid():
                pdf_file = request.FILES['pdf_file']
                title = form.cleaned_data['title']
                topic = form.cleaned_data['topic']

                try:
                    reader = PdfReader(BytesIO(pdf_file.read()))
                    pages_text = []
                    for i, page in enumerate(reader.pages):
                        text = page.extract_text()
                        if text and text.strip():
                            pages_text.append(f"[Page {i+1}]\n{text.strip()}")

                    if not pages_text:
                        messages.error(request, "Could not extract any text from this PDF. It may be scanned/image-based.")
                        return redirect('..')

                    content = '\n\n'.join(pages_text)[:8000]  # limit to 8000 chars

                    KnowledgeBase.objects.update_or_create(
                        title=title,
                        defaults={
                            'url': f'pdf://{pdf_file.name}',
                            'content': content,
                            'topic': topic,
                        }
                    )
                    messages.success(
                        request,
                        f'Successfully imported "{title}" ({len(reader.pages)} pages, {len(content)} characters).'
                    )
                    return redirect('../')

                except Exception as e:
                    messages.error(request, f'Failed to read PDF: {str(e)}')
                    return redirect('..')
        else:
            form = PDFUploadForm()

        context = {
            **self.admin_site.each_context(request),
            'form': form,
            'title': 'Upload PDF to Knowledge Base',
            'action_description': 'The PDF will be parsed automatically and its text saved to the knowledge base.',
        }
        return render(request, 'admin/kb_form.html', context)

    # --- View 2: URL Scrape ---
    def scrape_url_view(self, request):
        if request.method == 'POST':
            form = URLScrapeForm(request.POST)
            if form.is_valid():
                url = form.cleaned_data['url']
                title = form.cleaned_data['title']
                topic = form.cleaned_data['topic']

                try:
                    headers = {'User-Agent': 'Mozilla/5.0 (compatible; ACU-Chatbot/1.0)'}
                    response = requests.get(url, headers=headers, timeout=15)
                    response.raise_for_status()

                    soup = BeautifulSoup(response.text, 'html.parser')
                    for tag in soup(['script', 'style', 'nav', 'footer', 'header', 'aside']):
                        tag.decompose()

                    content = ' '.join(soup.get_text(separator='\n', strip=True).split())[:8000]

                    if len(content) < 100:
                        messages.warning(
                            request,
                            'Very little text was extracted. This page may require JavaScript. '
                            'Try the "Paste Text" option instead.'
                        )

                    KnowledgeBase.objects.update_or_create(
                        url=url,
                        defaults={'title': title, 'content': content, 'topic': topic}
                    )
                    messages.success(request, f'Successfully scraped "{title}" ({len(content)} characters).')
                    return redirect('../')

                except requests.exceptions.Timeout:
                    messages.error(request, 'Request timed out. The page took too long to respond.')
                except requests.exceptions.ConnectionError:
                    messages.error(request, 'Could not connect to the URL. Check that it is publicly accessible.')
                except Exception as e:
                    messages.error(request, f'Failed to scrape: {str(e)}')
        else:
            form = URLScrapeForm()

        context = {
            **self.admin_site.each_context(request),
            'form': form,
            'title': 'Scrape URL into Knowledge Base',
            'action_description': (
                'Enter a publicly accessible URL. The page content will be fetched and saved. '
                'Note: JavaScript-heavy pages (like OBS) cannot be scraped this way — use "Paste Text" instead.'
            ),
        }
        return render(request, 'admin/kb_form.html', context)

    # --- View 3: Paste Text ---
    def paste_text_view(self, request):
        if request.method == 'POST':
            form = PasteTextForm(request.POST)
            if form.is_valid():
                title = form.cleaned_data['title']
                url = form.cleaned_data.get('url') or 'manual-entry'
                topic = form.cleaned_data['topic']
                content = form.cleaned_data['content'].strip()

                KnowledgeBase.objects.update_or_create(
                    title=title,
                    defaults={'url': url, 'content': content[:8000], 'topic': topic}
                )
                messages.success(request, f'Successfully saved "{title}" ({len(content)} characters).')
                return redirect('../')
        else:
            form = PasteTextForm()

        context = {
            **self.admin_site.each_context(request),
            'form': form,
            'title': 'Paste Text into Knowledge Base',
            'action_description': (
                'Use this for OBS pages or any JavaScript-heavy content. '
                'Open the page in your browser, press Ctrl+A → Ctrl+C, then paste below.'
            ),
        }
        return render(request, 'admin/kb_form.html', context)

    # --- View 4: Site Crawl ---
    def crawl_site_view(self, request):
        if request.method == 'POST':
            form = CrawlSiteForm(request.POST)
            if form.is_valid():
                start_url = form.cleaned_data['start_url']
                max_pages = form.cleaned_data['max_pages']
                max_pdfs = form.cleaned_data['max_pdfs']

                if 'acibadem.edu.tr' not in start_url.lower():
                    messages.error(request, 'Only acibadem.edu.tr URLs are allowed for crawling.')
                    return redirect('..')

                crawler = AcibademSiteCrawler(
                    start_url,
                    max_pages=max_pages,
                    max_pdfs=max_pdfs,
                )

                try:
                    stats = crawler.crawl()
                    messages.success(
                        request,
                        (
                            f'Crawl finished. Saved {stats.html_saved} HTML pages and '
                            f'{stats.pdf_saved} PDFs. Visited {stats.html_visited} HTML pages and '
                            f'{stats.pdf_visited} PDFs.'
                        )
                    )
                    if stats.failed:
                        messages.warning(request, f'{stats.failed} URLs could not be processed.')
                    return redirect('../')
                except Exception as e:
                    messages.error(request, f'Failed to crawl site: {str(e)}')
                    return redirect('..')
        else:
            form = CrawlSiteForm()

        context = {
            **self.admin_site.each_context(request),
            'form': form,
            'title': 'Crawl Acibadem Website into Knowledge Base',
            'action_description': (
                'Start from one Acibadem URL, follow only acibadem.edu.tr links, '
                'and automatically import relevant HTML and PDF content into the knowledge base.'
            ),
        }
        return render(request, 'admin/kb_form.html', context)

    def import_default_sources_view(self, request):
        if request.method == 'POST':
            try:
                stats = crawl_default_acu_sources()
                messages.success(
                    request,
                    (
                        f'Default ACU import finished. Saved {stats.html_saved} HTML pages and '
                        f'{stats.pdf_saved} PDFs.'
                    )
                )
                if stats.failed:
                    messages.warning(request, f'{stats.failed} sources could not be processed.')
                return redirect('../')
            except Exception as e:
                messages.error(request, f'Failed to import default sources: {str(e)}')
                return redirect('../')

        context = {
            **self.admin_site.each_context(request),
            'title': 'Import Recommended ACU Sources',
            'action_description': (
                'This will automatically crawl the built-in Acibadem student affairs, announcement, '
                'English academic pages, and the main regulation PDF. No manual URL entry is required.'
            ),
        }
        return render(request, 'admin/kb_confirm.html', context)

    # --- Helper display methods ---
    def short_url(self, obj):
        if obj.url and obj.url != 'manual-entry':
            return format_html('<a href="{}" target="_blank">{}</a>', obj.url, obj.url[:50] + '...' if len(obj.url) > 50 else obj.url)
        return obj.url or '—'
    short_url.short_description = 'URL'

    def content_length(self, obj):
        return f'{len(obj.content):,} chars'
    content_length.short_description = 'Content Size'


# ---------------------------------------------------------------------------
# Other admins
# ---------------------------------------------------------------------------

@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = ['title', 'created_at', 'message_count']
    search_fields = ['title']
    readonly_fields = ['created_at']

    def message_count(self, obj):
        return obj.messages.count()
    message_count.short_description = 'Messages'


@admin.register(ChatMessage)
class ChatMessageAdmin(admin.ModelAdmin):
    list_display = ['short_question', 'conversation', 'created_at']
    list_filter = ['conversation']
    search_fields = ['question', 'answer']
    readonly_fields = ['question', 'answer', 'created_at', 'conversation']

    def short_question(self, obj):
        return obj.question[:60] + '...' if len(obj.question) > 60 else obj.question
    short_question.short_description = 'Question'
