from collections import deque
from dataclasses import dataclass
from io import BytesIO
from urllib.parse import urldefrag, urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from PyPDF2 import PdfReader

from chat.models import KnowledgeBase


DEFAULT_KEYWORDS = [
    "acibadem",
    "çift anadal",
    "cift anadal",
    "çap",
    "cap",
    "yandal",
    "minor",
    "double major",
    "program",
    "programlari",
    "başvuru",
    "basvuru",
    "koşul",
    "kosul",
    "şart",
    "gpa",
    "akts",
    "curriculum",
    "course",
]

DEFAULT_ACU_SOURCE_URLS = [
    "https://www.acibadem.edu.tr/ogrenci/ogrenci-isleri/cift-anadal-yandal-programlari",
    "https://www.acibadem.edu.tr/duyurular/2024-2025-guz-donemi-cift-anadal-yandal-basvurulari-ve-takvimi",
    "https://www.acibadem.edu.tr/en/academic",
    "https://www.acibadem.edu.tr/en",
    "https://www.acibadem.edu.tr/sites/default/files/document/2025/ACU%20%C3%87ift%20Anadal%20Yandal%20Y%C3%B6nergesi%2014.11.2023.pdf",
]

SKIP_EXTENSIONS = (
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".svg",
    ".webp",
    ".zip",
    ".rar",
    ".7z",
    ".mp4",
    ".mp3",
    ".avi",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
)


@dataclass
class CrawlStats:
    html_saved: int = 0
    pdf_saved: int = 0
    html_visited: int = 0
    pdf_visited: int = 0
    skipped: int = 0
    failed: int = 0


class AcibademSiteCrawler:
    def __init__(
        self,
        start_url,
        *,
        allowed_domain="acibadem.edu.tr",
        keywords=None,
        max_pages=25,
        max_pdfs=10,
        request_timeout=20,
        content_limit=12000,
        logger=None,
    ):
        self.start_url = start_url
        self.allowed_domain = allowed_domain.lower()
        self.keywords = [keyword.lower() for keyword in (keywords or DEFAULT_KEYWORDS)]
        self.max_pages = max_pages
        self.max_pdfs = max_pdfs
        self.request_timeout = request_timeout
        self.content_limit = content_limit
        self.logger = logger
        self.stats = CrawlStats()
        self.session = requests.Session()
        self.session.headers.update(
            {"User-Agent": "Mozilla/5.0 (compatible; ACU-Chatbot/1.0)"}
        )

    def crawl(self):
        queue = deque([self.normalize_url(self.start_url)])
        seen = set()

        while queue:
            current_url = queue.popleft()
            if not current_url or current_url in seen:
                continue

            seen.add(current_url)

            if current_url.lower().endswith(".pdf"):
                if self.stats.pdf_visited >= self.max_pdfs:
                    self.stats.skipped += 1
                    continue
                self.stats.pdf_visited += 1
                self._log(f"PDF: {current_url}")
                self._crawl_pdf(current_url)
                continue

            if self.stats.html_visited >= self.max_pages:
                self.stats.skipped += 1
                continue

            self.stats.html_visited += 1
            self._log(f"HTML: {current_url}")
            discovered_links = self._crawl_html(current_url)

            for link in discovered_links:
                normalized = self.normalize_url(link)
                if normalized and normalized not in seen:
                    queue.append(normalized)

        return self.stats

    def normalize_url(self, url):
        if not url:
            return None

        normalized, _fragment = urldefrag(url.strip())
        parsed = urlparse(normalized)

        if parsed.scheme not in {"http", "https"}:
            return None

        if not self.is_allowed_host(parsed.netloc):
            return None

        lower_url = normalized.lower()
        if any(lower_url.endswith(ext) for ext in SKIP_EXTENSIONS):
            return None

        return normalized

    def is_allowed_host(self, host):
        host = (host or "").lower()
        return host == self.allowed_domain or host.endswith(f".{self.allowed_domain}")

    def _crawl_html(self, url):
        try:
            response = self.session.get(url, timeout=self.request_timeout)
            response.raise_for_status()
        except Exception as exc:
            self.stats.failed += 1
            self._log(f"Failed HTML {url}: {exc}")
            return []

        soup = BeautifulSoup(response.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()

        title = self.extract_html_title(soup, url)
        raw_text = soup.get_text(separator="\n", strip=True)
        content = self.extract_relevant_text(raw_text)

        if content:
            self.save_record(url, title, content, self.infer_topic(url, title, content))
            self.stats.html_saved += 1

        links = []
        for anchor in soup.find_all("a", href=True):
            joined = urljoin(url, anchor["href"])
            normalized = self.normalize_url(joined)
            if normalized:
                links.append(normalized)

        return links

    def _crawl_pdf(self, url):
        try:
            response = self.session.get(url, timeout=max(self.request_timeout, 30))
            response.raise_for_status()
            reader = PdfReader(BytesIO(response.content))
        except Exception as exc:
            self.stats.failed += 1
            self._log(f"Failed PDF {url}: {exc}")
            return

        parts = []
        for index, page in enumerate(reader.pages, start=1):
            text = page.extract_text() or ""
            text = text.strip()
            if text:
                parts.append(f"[Page {index}]\n{text}")

        content = self.extract_relevant_text("\n\n".join(parts))
        if not content:
            self.stats.skipped += 1
            return

        title = self.extract_pdf_title(url)
        self.save_record(url, title, content, self.infer_topic(url, title, content))
        self.stats.pdf_saved += 1

    def extract_html_title(self, soup, url):
        if soup.title and soup.title.string:
            return " ".join(soup.title.string.split())[:300]

        path = urlparse(url).path.rstrip("/").split("/")[-1]
        return path.replace("-", " ").replace("_", " ").strip().title() or url

    def extract_pdf_title(self, url):
        path = urlparse(url).path.rstrip("/").split("/")[-1]
        return path.replace("%20", " ").replace(".pdf", "").replace("-", " ").strip()[:300]

    def extract_relevant_text(self, text):
        lines = []
        for line in (text or "").splitlines():
            cleaned = " ".join(line.split())
            if not cleaned:
                continue
            if self.is_relevant_line(cleaned):
                lines.append(cleaned)

        if not lines:
            condensed = " ".join((text or "").split())
            if not self.contains_keyword(condensed):
                return ""
            return condensed[: self.content_limit]

        return " ".join(lines)[: self.content_limit]

    def is_relevant_line(self, line):
        if self.contains_keyword(line):
            return True

        lowered = line.lower()
        return (
            "gpa" in lowered
            or "ects" in lowered
            or "akts" in lowered
            or "semester" in lowered
            or "dönem" in lowered
            or "donem" in lowered
        )

    def contains_keyword(self, text):
        lowered = (text or "").lower()
        return any(keyword in lowered for keyword in self.keywords)

    def infer_topic(self, url, title, content):
        text = f"{url} {title} {content}".lower()

        if "double major" in text or "çift anadal" in text or "cift anadal" in text:
            if "option" in text or "department" in text or "apply for" in text:
                return "double_major_options"
            return "double_major"

        if "minor" in text or "yandal" in text:
            if "option" in text or "department" in text or "apply for" in text:
                return "minor_options"
            return "minor"

        if "curriculum" in text or "mufredat" in text or "müfredat" in text:
            return "curriculum"

        if "admission" in text or "başvuru" in text or "basvuru" in text:
            return "admission"

        if "course" in text or "ders" in text:
            return "course"

        return "general"

    def save_record(self, url, title, content, topic):
        KnowledgeBase.objects.update_or_create(
            url=url,
            defaults={"title": title[:300], "content": content, "topic": topic},
        )

    def _log(self, message):
        if self.logger:
            self.logger(message)


def crawl_default_acu_sources(
    *,
    logger=None,
    max_pages_per_source=8,
    max_pdfs_per_source=3,
):
    combined = CrawlStats()

    for source_url in DEFAULT_ACU_SOURCE_URLS:
        crawler = AcibademSiteCrawler(
            source_url,
            max_pages=max_pages_per_source,
            max_pdfs=max_pdfs_per_source,
            logger=logger,
        )
        stats = crawler.crawl()
        combined.html_saved += stats.html_saved
        combined.pdf_saved += stats.pdf_saved
        combined.html_visited += stats.html_visited
        combined.pdf_visited += stats.pdf_visited
        combined.skipped += stats.skipped
        combined.failed += stats.failed

    return combined
