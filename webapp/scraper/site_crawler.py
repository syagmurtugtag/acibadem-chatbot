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
    # Department / faculty / curriculum keywords
    "bölüm",
    "bolum",
    "department",
    "fakülte",
    "fakulte",
    "faculty",
    "bölüm başkanı",
    "bolum baskani",
    "head of department",
    "department chair",
    "akademik",
    "akademik kadro",
    "kadro",
    "öğretim",
    "ogretim",
    "instructor",
    "prof",
    "doç",
    "doc",
    "assoc",
    "asst",
    "dr.",
    "phd",
    "ders",
    "dersler",
    "zorunlu",
    "compulsory",
    "mandatory",
    "required",
    "seçmeli",
    "secmeli",
    "elective",
    "müfredat",
    "mufredat",
    "ders planı",
    "ders plani",
    "ders listesi",
    "course list",
    "syllabus",
    "credit",
    "kredi",
    "biyomedikal",
    "biomedical",
    "bilgisayar",
    "computer engineering",
    "moleküler",
    "molekuler",
    "psikoloji",
    "psychology",
    "sosyoloji",
    "sociology",
    "sağlık",
    "saglik",
    "health",
    "hemşire",
    "hemsire",
    "nursing",
    "tıp",
    "medicine",
    "eczacılık",
    "eczacilik",
    "pharmacy",
    "fizyoterapi",
    "physiotherapy",
    "beslenme",
    "nutrition",
]

DEFAULT_ACU_SOURCE_URLS = [
    # Çift anadal / yandal
    "https://www.acibadem.edu.tr/ogrenci/ogrenci-isleri/cift-anadal-yandal-programlari",
    "https://www.acibadem.edu.tr/sites/default/files/document/2025/lisans-cift-anadal-program-listesi-16.10.2023.pdf",
    "https://www.acibadem.edu.tr/sites/default/files/document/2025/%C3%B6nlisans-cift-anadal-program-listesi-05.09.2025.pdf",
    "https://www.acibadem.edu.tr/sites/default/files/document/2024/yandal-program-listesi-15.12.2023.pdf",
    "https://www.acibadem.edu.tr/duyurular/2024-2025-guz-donemi-cift-anadal-yandal-basvurulari-ve-takvimi",
    "https://www.acibadem.edu.tr/sites/default/files/document/2025/ACU%20%C3%87ift%20Anadal%20Yandal%20Y%C3%B6nergesi%2014.11.2023.pdf",
    # Genel akademik giriş sayfaları (link kaynağı olarak crawl edilir)
    "https://www.acibadem.edu.tr/en/academic",
    "https://www.acibadem.edu.tr/en",
    "https://www.acibadem.edu.tr/akademik",
    # Mühendislik ve Doğa Bilimleri Fakültesi - bölüm sayfaları
    # Doğru URL deseni: /akademik/lisans/<fakulte>/bolumler/<bolum>/<altsayfa>
    "https://www.acibadem.edu.tr/akademik/lisans/muhendislik-ve-doga-bilimleri-fakultesi",
    "https://www.acibadem.edu.tr/akademik/lisans/muhendislik-ve-doga-bilimleri-fakultesi/bolumler/biyomedikal-muhendisligi",
    "https://www.acibadem.edu.tr/akademik/lisans/muhendislik-ve-doga-bilimleri-fakultesi/bolumler/biyomedikal-muhendisligi/bolum-baskaninin-mesaji",
    "https://www.acibadem.edu.tr/akademik/lisans/muhendislik-ve-doga-bilimleri-fakultesi/bolumler/biyomedikal-muhendisligi/akademik-kadro",
    "https://www.acibadem.edu.tr/akademik/lisans/muhendislik-ve-doga-bilimleri-fakultesi/bolumler/biyomedikal-muhendisligi/ogretim-plani",
    "https://www.acibadem.edu.tr/akademik/lisans/muhendislik-ve-doga-bilimleri-fakultesi/bolumler/bilgisayar-muhendisligi",
    "https://www.acibadem.edu.tr/akademik/lisans/muhendislik-ve-doga-bilimleri-fakultesi/bolumler/bilgisayar-muhendisligi/bolum-baskaninin-mesaji",
    "https://www.acibadem.edu.tr/akademik/lisans/muhendislik-ve-doga-bilimleri-fakultesi/bolumler/bilgisayar-muhendisligi/akademik-kadro",
    "https://www.acibadem.edu.tr/akademik/lisans/muhendislik-ve-doga-bilimleri-fakultesi/bolumler/bilgisayar-muhendisligi/ogretim-plani",
    "https://www.acibadem.edu.tr/akademik/lisans/muhendislik-ve-doga-bilimleri-fakultesi/bolumler/molekuler-biyoloji-ve-genetik",
    "https://www.acibadem.edu.tr/akademik/lisans/muhendislik-ve-doga-bilimleri-fakultesi/bolumler/molekuler-biyoloji-ve-genetik/akademik-kadro",
    "https://www.acibadem.edu.tr/akademik/lisans/muhendislik-ve-doga-bilimleri-fakultesi/bolumler/molekuler-biyoloji-ve-genetik/ogretim-plani",
    # Genel akademik kadro toplu sayfası (fakülte bazında)
    "https://www.acibadem.edu.tr/akademik/lisans/muhendislik-ve-doga-bilimleri-fakultesi/akademik-kadro",
    # Sağlık Bilimleri Fakültesi
    "https://www.acibadem.edu.tr/akademik/lisans/saglik-bilimleri-fakultesi",
    "https://www.acibadem.edu.tr/akademik/lisans/saglik-bilimleri-fakultesi/bolumler/hemsirelik",
    "https://www.acibadem.edu.tr/akademik/lisans/saglik-bilimleri-fakultesi/bolumler/hemsirelik/akademik-kadro",
    "https://www.acibadem.edu.tr/akademik/lisans/saglik-bilimleri-fakultesi/bolumler/beslenme-ve-diyetetik",
    "https://www.acibadem.edu.tr/akademik/lisans/saglik-bilimleri-fakultesi/bolumler/beslenme-ve-diyetetik/akademik-kadro",
    "https://www.acibadem.edu.tr/akademik/lisans/saglik-bilimleri-fakultesi/bolumler/fizyoterapi-ve-rehabilitasyon",
    "https://www.acibadem.edu.tr/akademik/lisans/saglik-bilimleri-fakultesi/bolumler/fizyoterapi-ve-rehabilitasyon/akademik-kadro",
    "https://www.acibadem.edu.tr/akademik/lisans/saglik-bilimleri-fakultesi/bolumler/saglik-yonetimi",
    "https://www.acibadem.edu.tr/akademik/lisans/saglik-bilimleri-fakultesi/bolumler/saglik-yonetimi/akademik-kadro",
    # İnsan ve Toplum Bilimleri Fakültesi
    "https://www.acibadem.edu.tr/akademik/lisans/insan-ve-toplum-bilimleri-fakultesi",
    "https://www.acibadem.edu.tr/akademik/lisans/insan-ve-toplum-bilimleri-fakultesi/bolumler/psikoloji",
    "https://www.acibadem.edu.tr/akademik/lisans/insan-ve-toplum-bilimleri-fakultesi/bolumler/psikoloji/akademik-kadro",
    "https://www.acibadem.edu.tr/akademik/lisans/insan-ve-toplum-bilimleri-fakultesi/bolumler/sosyoloji",
    "https://www.acibadem.edu.tr/akademik/lisans/insan-ve-toplum-bilimleri-fakultesi/bolumler/sosyoloji/akademik-kadro",
    # Tıp ve Eczacılık
    "https://www.acibadem.edu.tr/akademik/lisans/tip-fakultesi",
    "https://www.acibadem.edu.tr/akademik/lisans/eczacilik-fakultesi",
    # Öğrenci yaşamı / kampüs olanakları
    "https://www.acibadem.edu.tr/ogrenci/acuda-yasam/kutuphane",
    "https://www.acibadem.edu.tr/universite/ogretim-elemani-el-kitabi/kampus-olanaklari/kutuphane-ve-elektronik-kaynaklar",
    # OBS / Bologna official curriculum pages
    "https://obs.acibadem.edu.tr/oibs/bologna/index.aspx?lang=tr",
    "https://obs.acibadem.edu.tr/oibs/bologna/progAbout.aspx?curSunit=6246&lang=tr",
    "https://obs.acibadem.edu.tr/oibs/bologna/progCourses.aspx?curSunit=6246&lang=tr",
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

# URL path fragments we never want to crawl. These pages produce a lot of
# noise — news, announcements, events, virtual tour, strategic plans — and
# RAG can falsely match keywords from them when the user asks about courses
# or department chairs.
SKIP_PATH_FRAGMENTS = (
    "/haberler/",
    "/haberler",
    "/duyurular/",
    "/duyurular",
    "/etkinlikler/",
    "/etkinlikler",
    "/galeri/",
    "/galeri",
    "/medya/",
    "/medya",
    "/blog/",
    "/blog",
    "/stratejik-plan",
    "/sss",        # frequently-asked-questions noise
    "/iletisim",   # contact pages — emails / addresses, not academic content
    "/node/",      # generic Drupal node URLs (often duplicates)
)

# Hosts we should not crawl at all (often misconfigured SSL, login required,
# or JS-only single-page apps that produce empty-content records).
SKIP_HOSTS = (
    "tour.acibadem.edu.tr",
    "bademnet.acibadem.edu.tr",
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
        content_limit=80000,
        logger=None,
        shared_seen=None,
    ):
        self.start_url = start_url
        self.allowed_domain = allowed_domain.lower()
        self.keywords = [keyword.lower() for keyword in (keywords or DEFAULT_KEYWORDS)]
        self.max_pages = max_pages
        self.max_pdfs = max_pdfs
        self.request_timeout = request_timeout
        self.content_limit = content_limit
        self.logger = logger
        # When `crawl_default_acu_sources` runs many crawlers back-to-back,
        # passing a shared seen set avoids re-fetching the same URL from
        # multiple starting points (which used to make bootstrap take 30+ min).
        self.shared_seen = shared_seen
        self.stats = CrawlStats()
        self.session = requests.Session()
        self.session.headers.update(
            {"User-Agent": "Mozilla/5.0 (compatible; ACU-Chatbot/1.0)"}
        )

    def crawl(self):
        queue = deque([self.normalize_url(self.start_url)])
        seen = self.shared_seen if self.shared_seen is not None else set()

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

        # Reject hosts that are known to produce garbage records.
        host = (parsed.netloc or "").lower()
        if host in SKIP_HOSTS:
            return None

        lower_url = normalized.lower()
        if any(lower_url.endswith(ext) for ext in SKIP_EXTENSIONS):
            return None

        # Reject obvious noise: news, announcements, events, marketing.
        path_lower = (parsed.path or "").lower()
        if any(fragment in path_lower for fragment in SKIP_PATH_FRAGMENTS):
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
        table_text = self.extract_table_text(soup)
        if table_text:
            raw_text = f"{raw_text}\n\n{table_text}"
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

    def extract_table_text(self, soup):
        tables = []

        for table_index, table in enumerate(soup.find_all("table"), start=1):
            rows = []

            for tr in table.find_all("tr"):
                cells = [
                    " ".join(cell.get_text(separator=" ", strip=True).split())
                    for cell in tr.find_all(["th", "td"])
                ]
                cells = [cell for cell in cells if cell]
                if cells:
                    rows.append(" | ".join(cells))

            if rows:
                tables.append(f"[HTML Table {table_index}]\n" + "\n".join(rows))

        return "\n\n".join(tables)

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

        text_title = " ".join(soup.get_text(separator=" ", strip=True).split())[:300]
        if text_title:
            return text_title

        path = urlparse(url).path.rstrip("/").split("/")[-1]
        return path.replace("-", " ").replace("_", " ").strip().title() or url

    def extract_pdf_title(self, url):
        path = urlparse(url).path.rstrip("/").split("/")[-1]
        return path.replace("%20", " ").replace(".pdf", "").replace("-", " ").strip()[:300]

    def extract_relevant_text(self, text):
        # If the page mentions ANY of our keywords, keep the full text up to
        # the content limit. Filtering line-by-line was throwing away things
        # like "Prof. Dr. ..." rows on department pages because the surrounding
        # name line did not contain a keyword by itself.
        condensed = " ".join((text or "").split())
        if not condensed:
            return ""

        if self.contains_keyword(condensed):
            return condensed[: self.content_limit]

        # Fall back to per-line filtering for pages with no global keyword hit.
        lines = []
        for line in (text or "").splitlines():
            cleaned = " ".join(line.split())
            if not cleaned:
                continue
            if self.is_relevant_line(cleaned):
                lines.append(cleaned)

        if not lines:
            return ""

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
            or "prof." in lowered
            or "doç." in lowered
            or "doc." in lowered
            or "dr." in lowered
        )

    def contains_keyword(self, text):
        lowered = (text or "").lower()
        return any(keyword in lowered for keyword in self.keywords)

    def infer_topic(self, url, title, content):
        text = f"{url} {title} {content}".lower()

        if "double major" in text or "çift anadal" in text or "cift anadal" in text:
            if (
                "option" in text
                or "department" in text
                or "apply for" in text
                or "program listesi" in text
                or "program-listesi" in text
                or "programları" in text
            ):
                return "double_major_options"
            return "double_major"

        if "minor" in text or "yandal" in text:
            if (
                "option" in text
                or "department" in text
                or "apply for" in text
                or "program listesi" in text
                or "program-listesi" in text
                or "programları" in text
            ):
                return "minor_options"
            return "minor"

        # Department / faculty pages — used for "who is the chair", contact, etc.
        if (
            "akademik-kadro" in url
            or "akademik kadro" in text
            or "head of department" in text
            or "bölüm başkanı" in text
            or "bolum baskani" in text
        ):
            return "faculty"

        if (
            "/biyomedikal-muhendisligi" in url
            or "/bilgisayar-muhendisligi" in url
            or "/molekuler-biyoloji" in url
            or "/psikoloji" in url
            or "/sosyoloji" in url
            or "/saglik-yonetimi" in url
            or "/hemsirelik" in url
            or "/beslenme-ve-diyetetik" in url
            or "/fizyoterapi" in url
            or "/tip-fakultesi" in url
            or "/eczacilik" in url
        ):
            return "department"

        if (
            "curriculum" in text
            or "mufredat" in text
            or "müfredat" in text
            or "progcourses.aspx" in url.lower()
            or "dersler yıl" in text
            or "ders planı" in text
            or "ders plani" in text
            or "ders listesi" in text
            or "course list" in text
            or "zorunlu ders" in text
            or "compulsory course" in text
        ):
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
    # Shared across every starting URL so the same page is never fetched
    # twice during a single bootstrap run.
    shared_seen = set()

    for source_url in DEFAULT_ACU_SOURCE_URLS:
        crawler = AcibademSiteCrawler(
            source_url,
            max_pages=max_pages_per_source,
            max_pdfs=max_pdfs_per_source,
            logger=logger,
            shared_seen=shared_seen,
        )
        stats = crawler.crawl()
        combined.html_saved += stats.html_saved
        combined.pdf_saved += stats.pdf_saved
        combined.html_visited += stats.html_visited
        combined.pdf_visited += stats.pdf_visited
        combined.skipped += stats.skipped
        combined.failed += stats.failed

    return combined


def is_official_acu_source_url(url):
    parsed = urlparse(url or "")
    host = (parsed.netloc or "").lower()
    return parsed.scheme in {"http", "https"} and (
        host == "acibadem.edu.tr" or host.endswith(".acibadem.edu.tr")
    )
