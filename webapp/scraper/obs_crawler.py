"""Playwright-based scraper for ACU's OBS Bologna pages.

Why this exists
---------------
ACU's authoritative source for course curricula is the OBS Bologna interface
at obs.acibadem.edu.tr. Those pages are ASP.NET WebForms with client-side
JavaScript that populates tables after the initial page load — the static
``requests`` + BeautifulSoup crawler in ``site_crawler.py`` cannot read them
reliably. Running a real browser via Playwright gives us authoritative
course/program data straight from the official source, and remains correct
even if ACU upgrades the OBS interface.

Architecture notes
------------------
* All Django ORM access happens *outside* the Playwright context. Playwright's
  sync API runs against an asyncio event loop, and Django refuses ORM calls
  inside one (``SynchronousOnlyOperation``). The crawler therefore reads
  seed URLs into a list first, runs the entire browser session, and writes
  results back to the database afterwards.
* Seed discovery: we deliberately do NOT rely on expanding the OBS index
  treeview (its expansion is JS post-back driven and brittle). Instead we
  treat ACU's public department pages as the source of truth — they all
  link to a "Bilgi Paketi" entry on OBS with the correct ``curSunit`` IDs.
  Those URLs already land in the knowledge base via the static crawler, so
  we just read them out of ``KnowledgeBase`` here and re-fetch each with a
  real browser to capture the JS-rendered course tables.
* Fallback discovery: if the KB has no OBS URLs at all (cold start), we try
  the index page and look for any anchor whose href contains ``curSunit``.

Output
------
Each program scraped becomes one ``KnowledgeBase`` record, keyed by URL:

    title   = "<program name> - Müfredat (OBS)"  /  "... - Program Bilgileri (OBS)"
    topic   = "curriculum"                       /  "department"
    content = a normalized text dump of the rendered page

Downstream RAG / extractors already know how to parse this format (see
``extract_required_courses_from_text`` in views.py).
"""

from __future__ import annotations

import re
from contextlib import contextmanager
from urllib.parse import parse_qs, urlparse

from chat.models import KnowledgeBase


OBS_HOST = "obs.acibadem.edu.tr"
OBS_INDEX_URL = f"https://{OBS_HOST}/oibs/bologna/index.aspx?lang=tr"
OBS_PROG_COURSES_TEMPLATE = (
    f"https://{OBS_HOST}/oibs/bologna/progCourses.aspx?curSunit={{sunit}}&lang=tr"
)
OBS_PROG_ABOUT_TEMPLATE = (
    f"https://{OBS_HOST}/oibs/bologna/progAbout.aspx?curSunit={{sunit}}&lang=tr"
)

PAGE_LOAD_TIMEOUT_MS = 30_000
TABLE_RENDER_TIMEOUT_MS = 8_000     # short — invalid sunits never render this fast
PROBE_TIMEOUT_MS = 12_000           # navigation timeout while probing curSunit IDs

MAX_CONTENT_CHARS = 80_000

# ACU's program IDs in OBS Bologna live in two distinct bands:
#   * 1–200    — legacy / historical entries (e.g. older Tıp Mühendisliği)
#   * 6000+    — currently-active programs (e.g. curSunit=6247 = Biyomedikal)
# The static crawler can only reach IDs that ACU's public website links to,
# which usually does not include the high band. So we ALWAYS probe the high
# band on a refresh run, and only fall back to probing the low band when
# the KB has very few seeds (cold start).
HIGH_BAND_RANGE = list(range(6000, 6501))
LOW_BAND_RANGE = list(range(1, 201))
LOW_BAND_THRESHOLD = 5
# Minimum number of distinct course codes a page must contain to be considered
# a real curriculum page (filters out OBS error/empty placeholder responses).
MIN_COURSE_CODES_FOR_VALID = 3
COURSE_CODE_PATTERN = re.compile(r"\b[A-Z]{2,4}\s+\d{3,4}\b")


# ---------------------------------------------------------------------------
# Seed-URL discovery (DB and HTTP — runs OUTSIDE the playwright context)
# ---------------------------------------------------------------------------


def _extract_sunit(url):
    qs = parse_qs(urlparse(url or "").query)
    values = qs.get("curSunit") or qs.get("cursunit")
    if not values:
        return None
    raw = values[0]
    return raw if raw.isdigit() else None


def collect_obs_sunits_from_kb():
    """Return ``[(sunit, name)]`` from OBS URLs the static crawler stored.

    The static crawler followed Bilgi Paketi links from the ACU department
    pages and stored their target URLs in the knowledge base, even though
    the page bodies were empty / unusable. We mine those rows for the
    ``curSunit`` parameter so we can re-fetch them with a real browser.
    """
    seen = set()
    pairs = []

    queryset = KnowledgeBase.objects.filter(url__contains=OBS_HOST).values_list(
        "url", "title"
    )
    for url, title in queryset:
        sunit = _extract_sunit(url)
        if not sunit or sunit in seen:
            continue
        seen.add(sunit)
        pairs.append({"sunit": sunit, "name": title or f"Program {sunit}"})

    return pairs


def enumerate_obs_sunits(page, logger, sunit_range=None):
    """Brute-force curSunit IDs and keep the ones whose progCourses page renders.

    The OBS Bologna index page is a JS-driven treeview that does not always
    expose its program anchors to a headless browser. Rather than fight the
    treeview, we directly probe the curSunit URL space and keep IDs whose
    rendered ``progCourses.aspx`` page contains at least
    :data:`MIN_COURSE_CODES_FOR_VALID` distinct course codes. ACU's ID space
    is small and contiguous, so this finishes in a few minutes and is
    deterministic regardless of how the index page evolves.
    """
    if sunit_range is None:
        sunit_range = HIGH_BAND_RANGE + LOW_BAND_RANGE

    found = []
    for sunit_int in sunit_range:
        sunit = str(sunit_int)
        url = OBS_PROG_COURSES_TEMPLATE.format(sunit=sunit)
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=PROBE_TIMEOUT_MS)
        except Exception as exc:
            logger(f"OBS probe: skip sunit={sunit} (nav: {exc})")
            continue

        # Brief wait for the table to populate. Invalid IDs render an
        # empty-body placeholder and never produce a course code at all.
        try:
            page.wait_for_function(
                """() => /\\b[A-Z]{2,4}\\s+\\d{3,4}\\b/.test(document.body.innerText)""",
                timeout=TABLE_RENDER_TIMEOUT_MS,
            )
        except Exception:
            continue

        body = page.evaluate("() => document.body.innerText") or ""
        unique_codes = set(COURSE_CODE_PATTERN.findall(body))
        if len(unique_codes) < MIN_COURSE_CODES_FOR_VALID:
            continue

        # Try to recover the program name from the rendered page so the saved
        # KnowledgeBase title is human-friendly. ACU OBS shows the name in
        # an <h1> / <h2> on the courses page; fall back to the page title.
        try:
            heading = page.evaluate(
                """() => {
                    const h = document.querySelector('h1, h2, .baslik, .header, .pageTitle');
                    if (h && h.innerText) return h.innerText.trim();
                    return (document.title || '').trim();
                }"""
            )
        except Exception:
            heading = ""
        name = (heading or f"Program {sunit}").strip()[:200]

        found.append({"sunit": sunit, "name": name})
        logger(f"OBS probe: sunit={sunit} valid ({len(unique_codes)} courses) — {name}")

    return found


def collect_obs_sunits_from_index(page, logger):
    """Last-resort: scrape sunit values from the OBS Bologna index page."""
    try:
        page.goto(OBS_INDEX_URL, wait_until="networkidle")
    except Exception as exc:
        logger(f"OBS: index navigation failed: {exc}")
        return []

    try:
        page.evaluate(
            """
            () => {
                const expanders = document.querySelectorAll(
                    'input[id*="ImgExpandAll"], input[type="image"][src*="plus"]'
                );
                for (const btn of expanders) { try { btn.click(); } catch (_) {} }
            }
            """
        )
        page.wait_for_load_state("networkidle", timeout=10_000)
    except Exception:
        pass

    anchors = []
    try:
        anchors = page.evaluate(
            """
            () => Array.from(document.querySelectorAll('a[href*="curSunit"]')).map(a => ({
                href: a.href,
                text: (a.innerText || a.textContent || '').trim()
            }))
            """
        )
    except Exception as exc:
        logger(f"OBS: index DOM read failed: {exc}")

    seen = set()
    pairs = []
    for anchor in anchors:
        sunit = _extract_sunit(anchor.get("href"))
        if not sunit or sunit in seen:
            continue
        seen.add(sunit)
        pairs.append({"sunit": sunit, "name": anchor.get("text") or f"Program {sunit}"})

    return pairs


# ---------------------------------------------------------------------------
# Playwright fetch (runs INSIDE the browser context, no ORM here)
# ---------------------------------------------------------------------------


@contextmanager
def _playwright_browser():
    """Launch headless Chromium with sane defaults; close it on exit."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as runtime:
        browser = runtime.chromium.launch(headless=True, args=["--no-sandbox"])
        try:
            context = browser.new_context(
                locale="tr-TR",
                user_agent=(
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36 "
                    "ACU-Chatbot/1.0"
                ),
            )
            page = context.new_page()
            page.set_default_timeout(PAGE_LOAD_TIMEOUT_MS)
            try:
                yield page
            finally:
                context.close()
        finally:
            browser.close()


def parse_obs_canonical_name(body):
    """Pull '<faculty> / <department>' out of an OBS page body.

    OBS pages start with a line like:
        Mühendislik ve Doğa Bilimleri Fakültesi / Biyomedikal Mühendisliği Lisans Programı (İngilizce) - Dersler
    or for an "about" page:
        ... / Biyomedikal Mühendisliği Lisans Programı (İngilizce) - Hakkında

    We strip the trailing ``- Dersler`` / ``- Hakkında`` suffix (and any
    other suffix after the last ' - ') so the title becomes a clean
    canonical name suitable for matching against user questions.
    """
    if not body:
        return ""

    # Use the first non-empty stripped line.
    first = ""
    for raw in body.splitlines():
        candidate = " ".join(raw.split()).strip()
        if candidate:
            first = candidate
            break

    if not first:
        return ""

    # Strip a trailing " - <suffix>" if present (e.g. " - Dersler", " - Hakkında").
    if " - " in first:
        first = first.rsplit(" - ", 1)[0].strip()

    return first[:280]


def _fetch_courses(page, sunit, logger):
    url = OBS_PROG_COURSES_TEMPLATE.format(sunit=sunit)
    page.goto(url, wait_until="networkidle")
    try:
        page.wait_for_function(
            """() => /\\b[A-Z]{2,4}\\s+\\d{3,4}\\b/.test(document.body.innerText)""",
            timeout=TABLE_RENDER_TIMEOUT_MS,
        )
    except Exception:
        logger(f"OBS: no course code detected on courses sunit={sunit}, keeping body anyway")
    body = page.evaluate("() => document.body.innerText") or ""
    body = body.strip()
    canonical = parse_obs_canonical_name(body)
    return url, body, canonical


def _fetch_about(page, sunit, logger):
    url = OBS_PROG_ABOUT_TEMPLATE.format(sunit=sunit)
    try:
        page.goto(url, wait_until="networkidle")
    except Exception as exc:
        logger(f"OBS: about navigation failed sunit={sunit}: {exc}")
        return url, "", ""
    body = page.evaluate("() => document.body.innerText") or ""
    body = body.strip()
    canonical = parse_obs_canonical_name(body)
    return url, body, canonical


# ---------------------------------------------------------------------------
# Persistence (runs OUTSIDE the playwright context)
# ---------------------------------------------------------------------------


def _save(url, *, title, topic, content):
    if not content:
        return False
    KnowledgeBase.objects.update_or_create(
        url=url,
        defaults={
            "title": (title or "")[:300],
            "topic": topic,
            "content": content[:MAX_CONTENT_CHARS],
        },
    )
    return True


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def crawl_obs_bologna(logger=None, only_missing=True, max_programs=None):
    """Re-fetch every known OBS Bologna program with a real browser.

    Parameters
    ----------
    logger : callable, optional
        Receives status strings.
    only_missing : bool
        Skip programs whose Playwright-sourced records (title contains
        ``(OBS)``) are already in the knowledge base.
    max_programs : int, optional
        Hard cap on how many programs to scrape this run.
    """
    log = logger or (lambda _msg: None)

    # ------------------------------------------------------------------
    # Phase 1 — DB reads. Done before opening the browser.
    # ------------------------------------------------------------------
    seed_pairs = collect_obs_sunits_from_kb()
    log(f"OBS: discovered {len(seed_pairs)} sunits from existing KB rows")

    already_done = set()
    if only_missing:
        already_done = set(
            KnowledgeBase.objects
            .filter(url__contains=OBS_HOST, title__contains="(OBS)")
            .values_list("url", flat=True)
        )

    # ------------------------------------------------------------------
    # Phase 2 — Playwright. NO ORM calls here.
    # ------------------------------------------------------------------
    fetched = []  # list of (kind, url, title, topic, content)
    failed = 0
    skipped = 0

    with _playwright_browser() as page:
        existing_sunits = {p["sunit"] for p in seed_pairs}

        # 1) ALWAYS probe the high band (6000+). The static crawler does not
        #    reach it, but ACU's currently-active programs live there
        #    (e.g. curSunit=6247 is the active Biyomedikal Mühendisliği).
        high_band_remaining = [s for s in HIGH_BAND_RANGE if str(s) not in existing_sunits]
        if high_band_remaining:
            log(
                f"OBS: probing high band — {len(high_band_remaining)} candidate "
                "curSunit IDs (6000+) not yet in KB"
            )
            probed_high = enumerate_obs_sunits(page, log, sunit_range=high_band_remaining)
            for prog in probed_high:
                if prog["sunit"] not in existing_sunits:
                    seed_pairs.append(prog)
                    existing_sunits.add(prog["sunit"])

        # 2) Cold-start: probe the low band only when KB is essentially empty.
        if len(seed_pairs) < LOW_BAND_THRESHOLD:
            log("OBS: cold start — probing low band (1-200) too")
            low_band_remaining = [s for s in LOW_BAND_RANGE if str(s) not in existing_sunits]
            probed_low = enumerate_obs_sunits(page, log, sunit_range=low_band_remaining)
            for prog in probed_low:
                if prog["sunit"] not in existing_sunits:
                    seed_pairs.append(prog)
                    existing_sunits.add(prog["sunit"])

        # 3) Last-resort fallback: try the index page DOM. (Rarely useful in
        #    practice but kept for completeness.)
        if not seed_pairs:
            log("OBS: KB has no OBS URLs and probing returned nothing; trying index discovery")
            seed_pairs = collect_obs_sunits_from_index(page, log)

        if max_programs:
            seed_pairs = seed_pairs[:max_programs]

        for prog in seed_pairs:
            sunit = prog["sunit"]
            name = prog["name"]
            courses_url = OBS_PROG_COURSES_TEMPLATE.format(sunit=sunit)
            about_url = OBS_PROG_ABOUT_TEMPLATE.format(sunit=sunit)

            if courses_url in already_done and about_url in already_done:
                skipped += 1
                continue

            try:
                # Re-fetch always (even for already-done) when --refresh is on,
                # because we want canonical titles (the previous version saved
                # generic 'Program <id>' titles).
                _, courses_text, courses_canonical = _fetch_courses(page, sunit, log)
                _, about_text, about_canonical = _fetch_about(page, sunit, log)

                # Prefer the canonical name parsed from the page body. Fall back
                # to whatever name we had as seed only if both pages were empty.
                canonical = courses_canonical or about_canonical or name

                if courses_text:
                    fetched.append((
                        "curriculum", courses_url,
                        f"{canonical} - Müfredat (OBS)", "curriculum",
                        courses_text,
                    ))

                if about_text:
                    fetched.append((
                        "about", about_url,
                        f"{canonical} - Program Bilgileri (OBS)", "department",
                        about_text,
                    ))

                log(f"OBS: OK sunit={sunit} '{canonical}'")
            except Exception as exc:
                failed += 1
                log(f"OBS: FAIL sunit={sunit} '{name}': {exc}")

    # ------------------------------------------------------------------
    # Phase 3 — DB writes. Browser is closed.
    # ------------------------------------------------------------------
    saved = 0
    for _kind, url, title, topic, content in fetched:
        if _save(url, title=title, topic=topic, content=content):
            saved += 1

    return {
        "saved": saved,
        "skipped": skipped,
        "failed": failed,
        "programs": len(seed_pairs),
    }
