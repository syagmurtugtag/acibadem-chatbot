from django import forms
from django.contrib import admin, messages
from django.shortcuts import render, redirect
from django.urls import path
from django.utils.html import format_html

from .models import KnowledgeBase, ChatMessage, Conversation
from scraper.site_crawler import (
    AcibademSiteCrawler,
    crawl_default_acu_sources,
    is_official_acu_source_url,
)


class URLScrapeForm(forms.Form):
    url = forms.URLField(
        label="Official ACU / OBS URL",
        help_text="Only acibadem.edu.tr and obs.acibadem.edu.tr URLs are allowed.",
    )


class CrawlSiteForm(forms.Form):
    start_url = forms.URLField(
        label="Start URL",
        initial="https://www.acibadem.edu.tr/",
        help_text="Crawler stays inside official Acibadem domains, including OBS/Bologna pages.",
    )
    max_pages = forms.IntegerField(
        min_value=1,
        max_value=100,
        initial=20,
        label="Max HTML pages",
        help_text="Safety limit for how many HTML pages to visit in one crawl.",
    )
    max_pdfs = forms.IntegerField(
        min_value=0,
        max_value=50,
        initial=10,
        label="Max PDFs",
        help_text="Safety limit for how many PDF files to parse in one crawl.",
    )


@admin.register(KnowledgeBase)
class KnowledgeBaseAdmin(admin.ModelAdmin):
    list_display = ["title", "topic", "short_url", "scraped_at", "content_length"]
    list_filter = ["topic"]
    search_fields = ["title", "content", "url"]
    readonly_fields = ["scraped_at"]

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path("scrape-url/", self.admin_site.admin_view(self.scrape_url_view), name="kb_scrape_url"),
            path("crawl-site/", self.admin_site.admin_view(self.crawl_site_view), name="kb_crawl_site"),
            path(
                "import-default-sources/",
                self.admin_site.admin_view(self.import_default_sources_view),
                name="kb_import_default_sources",
            ),
        ]
        return custom_urls + urls

    def scrape_url_view(self, request):
        if request.method == "POST":
            form = URLScrapeForm(request.POST)
            if form.is_valid():
                url = form.cleaned_data["url"]

                if not is_official_acu_source_url(url):
                    messages.error(request, "Only official Acibadem University and OBS URLs are allowed.")
                    return redirect("..")

                try:
                    crawler = AcibademSiteCrawler(url, max_pages=1, max_pdfs=1)
                    stats = crawler.crawl()
                    messages.success(
                        request,
                        f"Scrape finished. Saved {stats.html_saved} HTML pages and {stats.pdf_saved} PDFs.",
                    )
                    if stats.failed:
                        messages.warning(request, f"{stats.failed} URLs could not be processed.")
                    return redirect("../")
                except Exception as exc:
                    messages.error(request, f"Failed to scrape: {exc}")
        else:
            form = URLScrapeForm()

        context = {
            **self.admin_site.each_context(request),
            "form": form,
            "title": "Scrape Official ACU / OBS URL",
            "action_description": (
                "Enter an official Acibadem University website, PDF, or OBS/Bologna URL. "
                "The crawler fetches the source and infers the topic automatically."
            ),
        }
        return render(request, "admin/kb_form.html", context)

    def crawl_site_view(self, request):
        if request.method == "POST":
            form = CrawlSiteForm(request.POST)
            if form.is_valid():
                start_url = form.cleaned_data["start_url"]
                max_pages = form.cleaned_data["max_pages"]
                max_pdfs = form.cleaned_data["max_pdfs"]

                if not is_official_acu_source_url(start_url):
                    messages.error(request, "Only official Acibadem University and OBS URLs are allowed.")
                    return redirect("..")

                try:
                    crawler = AcibademSiteCrawler(
                        start_url,
                        max_pages=max_pages,
                        max_pdfs=max_pdfs,
                    )
                    stats = crawler.crawl()
                    messages.success(
                        request,
                        (
                            f"Crawl finished. Saved {stats.html_saved} HTML pages and "
                            f"{stats.pdf_saved} PDFs. Visited {stats.html_visited} HTML pages and "
                            f"{stats.pdf_visited} PDFs."
                        ),
                    )
                    if stats.failed:
                        messages.warning(request, f"{stats.failed} URLs could not be processed.")
                    return redirect("../")
                except Exception as exc:
                    messages.error(request, f"Failed to crawl site: {exc}")
                    return redirect("..")
        else:
            form = CrawlSiteForm()

        context = {
            **self.admin_site.each_context(request),
            "form": form,
            "title": "Crawl Official ACU / OBS Sources",
            "action_description": (
                "Start from one official Acibadem or OBS URL, follow only allowed Acibadem links, "
                "and automatically import relevant HTML/PDF content."
            ),
        }
        return render(request, "admin/kb_form.html", context)

    def import_default_sources_view(self, request):
        if request.method == "POST":
            try:
                stats = crawl_default_acu_sources()
                messages.success(
                    request,
                    (
                        f"Default import finished. Saved {stats.html_saved} HTML pages and "
                        f"{stats.pdf_saved} PDFs from official ACU/OBS sources."
                    ),
                )
                if stats.failed:
                    messages.warning(request, f"{stats.failed} sources could not be processed.")
                return redirect("../")
            except Exception as exc:
                messages.error(request, f"Failed to import default sources: {exc}")
                return redirect("../")

        context = {
            **self.admin_site.each_context(request),
            "title": "Import Recommended ACU / OBS Sources",
            "action_description": (
                "This automatically crawls the built-in Acibadem website pages, official PDFs, "
                "and OBS/Bologna curriculum pages. No manual content entry is used."
            ),
        }
        return render(request, "admin/kb_confirm.html", context)

    def short_url(self, obj):
        if obj.url:
            label = obj.url[:50] + "..." if len(obj.url) > 50 else obj.url
            return format_html('<a href="{}" target="_blank">{}</a>', obj.url, label)
        return "-"

    short_url.short_description = "URL"

    def content_length(self, obj):
        return f"{len(obj.content):,} chars"

    content_length.short_description = "Content Size"


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = ["title", "created_at", "message_count"]
    search_fields = ["title"]
    readonly_fields = ["created_at"]

    def message_count(self, obj):
        return obj.messages.count()

    message_count.short_description = "Messages"


@admin.register(ChatMessage)
class ChatMessageAdmin(admin.ModelAdmin):
    list_display = ["short_question", "conversation", "created_at"]
    list_filter = ["conversation"]
    search_fields = ["question", "answer"]
    readonly_fields = ["question", "answer", "created_at", "conversation"]

    def short_question(self, obj):
        return obj.question[:60] + "..." if len(obj.question) > 60 else obj.question

    short_question.short_description = "Question"
