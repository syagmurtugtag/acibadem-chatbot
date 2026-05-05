import json
import logging
import math
import re
import requests
import unicodedata
from difflib import SequenceMatcher

from django.shortcuts import render
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
from pgvector.django import CosineDistance

from .models import KnowledgeBase, ChatMessage, Conversation
from .embeddings import get_text_embedding
from scraper.site_crawler import AcibademSiteCrawler, is_official_acu_source_url


logger = logging.getLogger(__name__)

NOT_FOUND_MESSAGE = "I am sorry, I could not find specific information about this."

STOP_WORDS = {
    "i", "am", "a", "an", "the", "to", "of", "in", "on", "at", "for", "and",
    "or", "is", "are", "do", "does", "can", "could", "would", "should",
    "lets", "let", "say", "about", "please", "which", "what", "who", "where",
    "when", "how", "from", "into", "my", "your", "their", "our",
    # Note: "student"/"students" intentionally NOT removed — they help disambiguate
    # questions like "what are the compulsory courses for CSE students".
}

RAG_MAX_CHARS_PER_CHUNK = 900
RAG_CHUNK_OVERLAP = 150
RAG_TOP_K = 6
RAG_VECTOR_RECORD_LIMIT = 30
# Minimum cosine-similarity score the BEST passage must reach for us to
# attempt an answer. Below this we admit ignorance rather than letting the
# LLM hallucinate from an unrelated news/event/announcement page.
RAG_MIN_TOP_SCORE = -100

BROAD_INFO_HINTS = [
    "hakkinda",
    "hakkında",
    "bilgi verir",
    "bilgi ver",
    "nedir",
    "ne demek",
    "anlat",
    "kisaca",
    "kısaca",
    "about",
    "what is",
    "tell me",
]

EXTRACTIVE_NOISE_HINTS = [
    "ana icerige atla",
    "tanitim katalogu",
    "sanal tur",
    "sor cevaplayalim",
    "detayli bilgi almak icin tiklayin",
    "tum haklari saklidir",
    "aday ogrenci",
    "mezunlar",
    "search",
]

GENERIC_EXTRACTIVE_TOKENS = {
    "acibadem",
    "universitesi",
    "university",
    "hakkinda",
    "hakkında",
    "bilgi",
    "verir",
    "misin",
    "nedir",
    "anlat",
    "kisaca",
    "kısaca",
    "about",
    "what",
    "tell",
}

GENERAL_RAG_GENERIC_TOKENS = GENERIC_EXTRACTIVE_TOKENS | {
    "acibadem",
    "universite",
    "universitesi",
    "university",
    "mehmet",
    "ali",
    "aydinlar",
    "hangi",
    "nerede",
    "zaman",
}

ACU_SITEMAP_URL_CACHE = None
PDF_TABLE_CACHE = {}
PROGRAM_OPTIONS_HUB_URL = "https://www.acibadem.edu.tr/ogrenci/ogrenci-isleri/cift-anadal-yandal-programlari"

# Question-intent hints used to pick the right prompt template.
FACULTY_QUESTION_HINTS = [
    "head of department",
    "department chair",
    "chair of",
    "who is the head",
    "who leads",
    "bölüm başkanı",
    "bolum baskani",
    "bölüm baskani",
    "bolum başkanı",
    "kim",
    "akademik kadro",
    "öğretim üyesi",
    "ogretim uyesi",
    "instructors",
    "faculty members",
]

ACADEMIC_ROLE_QUESTION_HINTS = [
    "dekan",
    "dekanı",
    "dekani",
    "dean",
    "dekan yardımcısı",
    "dekan yardimcisi",
    "dean assistant",
    "vice dean",
    "fakülte sekreteri",
    "fakulte sekreteri",
    "faculty secretary",
]

FACULTY_DEPARTMENT_LIST_HINTS = [
    "kaç bölüm",
    "kac bolum",
    "hangi bölümler",
    "hangi bolumler",
    "bölümleri nelerdir",
    "bolumleri nelerdir",
    "departments",
    "how many departments",
    "which departments",
]

CURRICULUM_QUESTION_HINTS = [
    "compulsory course",
    "compulsory courses",
    "mandatory course",
    "required course",
    "course list",
    "curriculum",
    "syllabus",
    "elective course",
    "zorunlu ders",
    "secmeli ders",
    "seçmeli ders",
    "müfredat",
    "mufredat",
    "ders planı",
    "ders plani",
    "ders listesi",
    "hangi dersler",
    "dersleri nelerdir",
    # Semester-specific phrasings
    "ilk donem",
    "ilk dönem",
    "1. dönem",
    "1.dönem",
    "1.donem",
    "1. donem",
    "birinci dönem",
    "birinci donem",
    "ikinci dönem",
    "ikinci donem",
    "first semester",
    "second semester",
    "first term",
    "guz donemi dersleri",
    "güz dönemi dersleri",
    "bahar donemi dersleri",
    "bahar dönemi dersleri",
]

COMMON_COURSE_HINTS = [
    "ortak",
    "ortak ders",
    "ortak dersler",
    "dersler ortak",
    "dersleri ortak",
    "ortak olan ders",
    "common",
    "common course",
    "common courses",
    "both",
    "same course",
    "shared course",
    "shared courses",
    "aynı ders",
    "ayni ders",
]

DEPARTMENT_TITLE_MAP = {
    "biomedical engineering": "Biomedical Engineering",
    "computer engineering": "Computer Engineering",
    "molecular biology and genetics": "Molecular Biology and Genetics",
    "psychology": "Psychology",
    "sociology": "Sociology",
    "health management": "Health Management",
    "nursing": "Nursing",
    "nutrition and dietetics": "Nutrition and Dietetics",
    "nutrition": "Nutrition",
    "physiotherapy and rehabilitation": "Physiotherapy and Rehabilitation",
    "physiotherapy": "Physiotherapy",
    "medicine": "Medicine",
    "pharmacy": "Pharmacy",
}

DEPARTMENT_TURKISH_TITLE_MAP = {
    "biomedical engineering": "Biyomedikal Mühendisliği",
    "computer engineering": "Bilgisayar Mühendisliği",
    "molecular biology and genetics": "Moleküler Biyoloji ve Genetik",
    "psychology": "Psikoloji",
    "sociology": "Sosyoloji",
    "health management": "Sağlık Yönetimi",
    "nursing": "Hemşirelik",
    "nutrition and dietetics": "Beslenme ve Diyetetik",
    "nutrition": "Beslenme",
    "physiotherapy and rehabilitation": "Fizyoterapi ve Rehabilitasyon",
    "physiotherapy": "Fizyoterapi",
    "medicine": "Tıp",
    "pharmacy": "Eczacılık",
}

TRACK_TITLE_MAP = {
    "double major": "Double Major",
    "minor": "Minor",
}

# Some ACU programs are recorded under different official names depending on
# the system (public website vs. OBS Bologna). The matcher needs to know the
# full set of aliases for each canonical key so a question phrased one way
# can still find a record indexed under another name.
#
# Concrete example: "Biyomedikal Mühendisliği" on the public site is
# registered in OBS Bologna as "Tıp Mühendisliği (İngilizce)".
#
# Keys are the canonical lower-case English identifier we use everywhere
# else (matches DEPARTMENT_TITLE_MAP). Values are extra phrases that should
# also count as a match for that department.
DEPARTMENT_ALIASES = {
    "biomedical engineering": [
        "biyomedikal muhendisligi",
        "biyomedikal mühendisliği",
        "tip muhendisligi",
        "tıp mühendisliği",
        "biyomedikal-muhendisligi",
        "tip-muhendisligi",
        "medical engineering",
    ],
    # Add other departments here when their OBS / public-site names diverge.
}

KNOWN_PHRASES = [
    "double major",
    "minor",
    "biomedical engineering",
    "computer engineering",
    "molecular biology and genetics",
    "molecular biology",
    "psychology",
    "sociology",
    "health management",
    "nursing",
    "nutrition and dietetics",
    "nutrition",
    "physiotherapy and rehabilitation",
    "physiotherapy",
    "medicine",
    "pharmacy",
    "associate degree",
    "vocational school",
]

OPTION_QUESTION_HINTS = [
    "which department",
    "what department",
    "which program",
    "what program",
    "which programmes",
    "what programmes",
    "which major",
    "what major",
    "options",
    "can apply",
    "apply to",
    "eligible for",
    "available departments",
    "available programs",
    "available options",
    "pursue",
    "hangi",
    "programlara",
    "programlarina",
    "başvurabilir",
    "basvurabilir",
    "başvuru",
    "basvuru",
]

APPLICATION_REQUIREMENT_HINTS = [
    "gereklilik",
    "gereklilikleri",
    "kosul",
    "kosullari",
    "koşul",
    "koşulları",
    "sart",
    "sartlari",
    "şart",
    "şartları",
    "basvuru sarti",
    "başvuru şartı",
    "basvuru kosulu",
    "başvuru koşulu",
    "kabul kosulu",
    "kabul koşulu",
    "requirements",
    "conditions",
    "criteria",
]

TURKISH_HINTS = {
    "ç", "ğ", "ı", "İ", "ö", "ş", "ü",
    " nedir", " nasıl", " hangi", " bölüm", " fakülte", " öğrenci",
    " başvuru", " şart", " koşul", " yandal", " çift anadal",
    " var mı", " neler", " hakkında",
    " kim", " bolum", " baskani", " muhendisligi", " ogrenci",
    " basvuru", " sart", " kosul", " var mi", " hakkinda",
}


def chat_view(request):
    conversations = Conversation.objects.all().order_by("-created_at")[:20]
    return render(request, "chat/index.html", {"conversations": conversations})


def get_conversation(request, conv_id):
    try:
        conversation = Conversation.objects.get(id=conv_id)
        messages = ChatMessage.objects.filter(conversation=conversation).order_by("created_at")

        data = {
            "id": conversation.id,
            "title": conversation.title,
            "messages": [{"question": m.question, "answer": m.answer} for m in messages],
        }
        return JsonResponse(data)

    except Conversation.DoesNotExist:
        return JsonResponse({"error": "Not found"}, status=404)


def normalize_text(text):
    text = (text or "").lower()
    text = text.replace("_", " ")
    replacements = {
        "çift anadal": "double major",
        "cift anadal": "double major",
        "çap": "double major",
        "cap": "double major",
        "yandal": "minor",
        "bilgisayar mühendisliği": "computer engineering",
        "bilgisayar muhendisligi": "computer engineering",
        "biyomedikal mühendisliği": "biomedical engineering",
        "biyomedikal muhendisligi": "biomedical engineering",
        "moleküler biyoloji ve genetik": "molecular biology and genetics",
        "molekuler biyoloji ve genetik": "molecular biology and genetics",
        "psikoloji": "psychology",
        "sosyoloji": "sociology",
        "sağlık yönetimi": "health management",
        "saglik yonetimi": "health management",
        "beslenme ve diyetetik": "nutrition and dietetics",
        "fizyoterapi ve rehabilitasyon": "physiotherapy and rehabilitation",
        "hemşirelik": "nursing",
        "hemsirelik": "nursing",
        "eczacılık": "pharmacy",
        "eczacilik": "pharmacy",
        "tıp": "medicine",
        "tip": "medicine",
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def detect_question_language(question):
    normalized = normalize_text(question)

    if any(hint in question for hint in "çğıİöşüÇĞIİÖŞÜ"):
        return "Turkish"

    if any(hint in normalized for hint in TURKISH_HINTS):
        return "Turkish"

    return "English"


def build_language_rule(question):
    language = detect_question_language(question)
    return (
        f'- Respond in {language}.\n'
        '- Use the same language as the QUESTION.\n'
        '- If the source text is in another language, translate the answer into the QUESTION language.'
    )


def localized_not_found(question):
    """Return the not-found message in the user's language.

    Keeping a single helper prevents Turkish users from receiving an English
    fallback when retrieval returns no evidence or the LLM output is rejected.
    """
    if detect_question_language(question) == "Turkish":
        return "Üzgünüm, bu konuda mevcut resmi verilerde belirli bir bilgi bulamadım."
    return NOT_FOUND_MESSAGE


def detect_answer_language(answer):
    normalized = normalize_text(answer)

    if any(ch in answer for ch in "çğıİöşüÇĞIİÖŞÜ"):
        return "Turkish"

    if any(hint in normalized for hint in TURKISH_HINTS):
        return "Turkish"

    return "English"


def build_language_rewrite_prompt(question, answer):
    language = detect_question_language(question)
    return f"""You are the Acibadem University Academic Assistant.

Rewrite the ANSWER so that it is in {language}.

RULES:
- Keep the meaning the same.
- Keep the answer concise.
- Use the same language as the QUESTION.
- Do not add new facts.
- Do not mention that you translated the answer.
- If the original answer is exactly "{NOT_FOUND_MESSAGE}", translate that message into {language}.

QUESTION:
{question}

ANSWER:
{answer}

REWRITTEN ANSWER:"""


def detect_track(question):
    q = normalize_text(question)

    if "double major" in q:
        return "double major"
    if "minor" in q:
        return "minor"

    return None


# =============================================================================
# Question-type classifiers — RETRIEVAL HINTS ONLY, NOT DISPATCH
# =============================================================================
# The functions below (is_faculty_question, is_curriculum_question, etc.) used
# to act as dispatch gates: "if is_curriculum_question, run the curriculum
# extractor". That broke for any phrasing the keyword lists didn't anticipate
# (e.g. "CSE kodlu dersler nelerdir" — no list match, no extractor fired).
#
# As of the 2026 refactor, dispatch happens through `collect_structured_evidence`
# which runs every extractor unconditionally. The classifiers below are kept
# only as RETRIEVAL RANKING SIGNALS used inside `get_passage_boost` — when we
# know the question is curriculum-flavoured, we boost OBS curriculum pages in
# the RAG result list. If a new phrasing isn't matched, no boost is applied,
# but the question STILL gets answered through the extractor + vector RAG path.


def is_faculty_question(question):
    q = normalize_text(question)
    has_dept = detect_department(question) is not None or bool(extract_department_phrase(question))
    has_hint = any(hint in q for hint in FACULTY_QUESTION_HINTS)
    # Specifically: "X bölümünün başkanı kim", "Who is the chair of X"
    return has_dept and has_hint


def detect_academic_role(question):
    folded = ascii_fold(question)
    if any(term in folded for term in ["dekan yardimcisi", "dekan yrd", "vice dean", "dean assistant"]):
        return "vice_dean"
    if any(term in folded for term in ["fakulte sekreteri", "faculty secretary"]):
        return "faculty_secretary"
    if any(term in folded for term in ["dekan", "dean"]):
        return "dean"
    return None


def is_academic_role_question(question):
    folded = ascii_fold(question)
    has_role = detect_academic_role(question) is not None
    asks_person = any(hint in folded for hint in ["kim", "who", "adi", "ismi", "name"])
    has_unit = any(term in folded for term in ["fakulte", "faculty", "enstitu", "school", "yuksekokul"])
    return has_role and (asks_person or has_unit)


def is_faculty_department_list_question(question):
    folded = ascii_fold(question)
    has_faculty = "fakulte" in folded or "faculty" in folded
    has_department_list_hint = any(ascii_fold(hint) in folded for hint in FACULTY_DEPARTMENT_LIST_HINTS)
    return has_faculty and has_department_list_hint


def is_curriculum_question(question):
    q = normalize_text(question)
    has_dept = detect_department(question) is not None
    has_hint = any(hint in q for hint in CURRICULUM_QUESTION_HINTS)
    return has_dept and has_hint


def is_common_course_question(question):
    q = normalize_text(question)
    folded = ascii_fold(question)
    has_two_departments = len(get_department_mentions(question)) >= 2
    has_common_hint = any(hint in q or hint in folded for hint in COMMON_COURSE_HINTS)
    has_course_hint = any(hint in q for hint in CURRICULUM_QUESTION_HINTS)
    return has_two_departments and has_common_hint and has_course_hint


def ascii_fold(text):
    translation = str.maketrans({
        "ç": "c", "Ç": "c",
        "ğ": "g", "Ğ": "g",
        "ı": "i", "I": "i", "İ": "i",
        "ö": "o", "Ö": "o",
        "ş": "s", "Ş": "s",
        "ü": "u", "Ü": "u",
    })
    folded = (text or "").translate(translation)
    folded = unicodedata.normalize("NFKD", folded)
    return "".join(ch for ch in folded if not unicodedata.combining(ch)).lower()


def slugify_text(text):
    folded = ascii_fold(text)
    folded = re.sub(r"[^a-z0-9]+", "-", folded)
    return folded.strip("-")


def extract_department_phrase(question):
    raw = ascii_fold(question)
    raw = re.sub(r"[^a-z0-9\s]", " ", raw)
    raw = re.sub(r"\s+", " ", raw).strip()

    stop_patterns = [
        " bolum baskani",
        " bolumunun baskani",
        " bolumu baskani",
        " department head",
        " department chair",
        " head of department",
    ]

    for pattern in stop_patterns:
        if pattern in raw:
            phrase = raw.split(pattern, 1)[0]
            phrase = re.sub(r"^(who is|kim|hangi|the|of|acibadem|acu)\s+", "", phrase).strip()
            if phrase:
                return phrase

    return ""


def get_department_display_name(question):
    phrase = extract_department_phrase(question)
    if phrase:
        words = {
            "muhendisligi": "Mühendisliği",
            "biyomedikal": "Biyomedikal",
            "bilgisayar": "Bilgisayar",
            "molekuler": "Moleküler",
            "biyoloji": "Biyoloji",
            "genetik": "Genetik",
            "psikoloji": "Psikoloji",
            "sosyoloji": "Sosyoloji",
            "saglik": "Sağlık",
            "yonetimi": "Yönetimi",
            "hemsirelik": "Hemşirelik",
            "beslenme": "Beslenme",
            "diyetetik": "Diyetetik",
            "fizyoterapi": "Fizyoterapi",
            "rehabilitasyon": "Rehabilitasyon",
            "eczacilik": "Eczacılık",
            "tip": "Tıp",
        }
        return " ".join(words.get(word, word.capitalize()) for word in phrase.split())

    department = detect_department(question)
    if department:
        if detect_question_language(question) == "Turkish":
            return DEPARTMENT_TURKISH_TITLE_MAP.get(department, DEPARTMENT_TITLE_MAP[department])
        return DEPARTMENT_TITLE_MAP[department]

    return "İlgili bölüm"


def get_department_slug_candidates(question):
    phrase = extract_department_phrase(question)
    candidates = []

    if phrase:
        candidates.append(slugify_text(phrase))

    department = detect_department(question)
    if department:
        candidates.append(slugify_text(DEPARTMENT_TITLE_MAP[department]))
        candidates.append(slugify_text(department))

    expanded = []
    for candidate in candidates:
        expanded.append(candidate)
        expanded.append(candidate.replace("engineering", "muhendisligi"))
        expanded.append(candidate.replace("biomedical", "biyomedikal").replace("engineering", "muhendisligi"))
        expanded.append(candidate.replace("computer", "bilgisayar").replace("engineering", "muhendisligi"))
        expanded.append(candidate.replace("molecular-biology-and-genetics", "molekuler-biyoloji-ve-genetik"))
        expanded.append(candidate.replace("health-management", "saglik-yonetimi"))
        expanded.append(candidate.replace("nutrition-and-dietetics", "beslenme-ve-diyetetik"))
        expanded.append(candidate.replace("physiotherapy-and-rehabilitation", "fizyoterapi-ve-rehabilitasyon"))
        expanded.append(candidate.replace("nursing", "hemsirelik"))
        expanded.append(candidate.replace("pharmacy", "eczacilik"))
        expanded.append(candidate.replace("medicine", "tip"))

    return list(dict.fromkeys(item for item in expanded if item))


def get_acu_sitemap_urls():
    global ACU_SITEMAP_URL_CACHE

    if ACU_SITEMAP_URL_CACHE is not None:
        return ACU_SITEMAP_URL_CACHE

    urls = []
    try:
        index_response = requests.get("https://www.acibadem.edu.tr/sitemap.xml", timeout=20)
        index_response.raise_for_status()
        sitemap_urls = re.findall(r"<loc>(https://www\.acibadem\.edu\.tr/sitemap\.xml\?page=\d+)</loc>", index_response.text)

        for sitemap_url in sitemap_urls:
            response = requests.get(sitemap_url, timeout=20)
            response.raise_for_status()
            urls.extend(re.findall(r"<loc>(.*?)</loc>", response.text))
    except Exception:
        return []

    ACU_SITEMAP_URL_CACHE = urls
    return urls


def discover_department_head_urls(question):
    candidates = get_department_slug_candidates(question)
    if not candidates:
        return []

    urls = []
    for url in get_acu_sitemap_urls():
        folded_url = ascii_fold(url)
        if "bolum-baskaninin-mesaji" not in folded_url:
            continue
        if any(f"/{candidate}/bolum-baskaninin-mesaji" in folded_url for candidate in candidates):
            urls.append(url)

    return urls


def analyze_query(question):
    normalized = normalize_text(question)
    folded = ascii_fold(question)
    track = detect_track(question)

    if is_faculty_department_list_question(question):
        intent = "faculty_departments"
    elif is_academic_role_question(question):
        intent = "academic_role"
    elif is_faculty_question(question):
        intent = "faculty"
    elif is_curriculum_question(question):
        intent = "curriculum"
    elif is_application_requirements_question(question):
        intent = "application_requirements"
    elif is_definition_question(question):
        intent = "definition"
    elif track:
        intent = "program_options"
    else:
        intent = "general"

    return {
        "question": question,
        "normalized": normalized,
        "folded": folded,
        "intent": intent,
        "track": track,
        "department": detect_department(question),
        "department_phrase": extract_department_phrase(question),
        "keywords": extract_keywords(question),
    }


def get_query_slug_candidates(analysis):
    candidates = []

    candidates.extend(get_department_slug_candidates(analysis["question"]))

    for keyword in analysis["keywords"]:
        if len(keyword) > 2:
            candidates.append(slugify_text(keyword))
            folded_keyword = ascii_fold(keyword)
            for suffix in ["si", "sı", "su", "sü", "i", "ı", "u", "ü"]:
                if folded_keyword.endswith(ascii_fold(suffix)) and len(folded_keyword) > len(suffix) + 3:
                    candidates.append(slugify_text(folded_keyword[:-len(suffix)]))

    if analysis["track"] == "double major":
        candidates.extend(["cift-anadal", "çift-anadal", "cap", "program-listesi"])
    elif analysis["track"] == "minor":
        candidates.extend(["yandal", "minor", "program-listesi"])

    if analysis["intent"] == "curriculum":
        candidates.extend(["ders", "dersler", "mufredat", "müfredat", "curriculum", "course"])

    if analysis["intent"] == "faculty":
        candidates.extend(["bolum-baskaninin-mesaji", "akademik-kadro", "department-head"])
    elif analysis["intent"] == "academic_role":
        candidates.extend(["yonetim", "yönetim", "dekan", "dekanlik", "dekanlık", "fakulte", "fakülte"])
    elif analysis["intent"] == "faculty_departments":
        candidates.extend(["fakulte", "fakülte", "bolumler", "bölümler", "lisans"])

    return list(dict.fromkeys(slugify_text(candidate) for candidate in candidates if candidate))


def score_candidate_source_url(url, analysis, slug_candidates):
    folded_url = ascii_fold(url)
    score = 0
    department_slugs = set(get_department_slug_candidates(analysis["question"]))
    has_department_slug = any(slug and slug in folded_url for slug in department_slugs)

    for slug in slug_candidates:
        if slug and slug in folded_url:
            score += 4 if "-" in slug else 2

    if has_department_slug:
        score += 16

    intent = analysis["intent"]
    if intent == "faculty":
        if "bolum-baskaninin-mesaji" in folded_url:
            score += 12
        if "akademik-kadro" in folded_url:
            score += 8
    elif intent == "academic_role":
        if any(hint in folded_url for hint in ["yonetim", "yönetim", "dekan", "dekanlik", "dekanlık"]):
            score += 18
        if any(hint in folded_url for hint in ["akademik-kadro", "bolum-baskaninin-mesaji", "ders", "mufredat"]):
            score -= 10
    elif intent == "faculty_departments":
        if folded_url.rstrip("/").endswith("fakultesi") or "/fakultesi/" not in folded_url:
            score += 4
        if "bolumler" in folded_url or "bölümler" in folded_url:
            score += 8
        if any(hint in folded_url for hint in ["akademik-kadro", "yonetim", "dekanlik", "ders", "mufredat", "komisyon"]):
            score -= 8
    elif intent == "curriculum":
        if any(hint in folded_url for hint in ["ders", "mufredat", "curriculum", "course", "bologna"]):
            score += 10
        if department_slugs and not has_department_slug:
            score -= 12
        if any(hint in folded_url for hint in ["/haberler/", "/duyurular/"]):
            score -= 8
    elif intent == "program_options":
        track_hints = ["program-listesi", "ogrenci-isleri"]
        if analysis["track"] == "double major":
            track_hints.extend(["cift-anadal", "cap"])
        elif analysis["track"] == "minor":
            track_hints.extend(["yandal", "minor"])
        if any(hint in folded_url for hint in track_hints):
            score += 12
        if "program-listesi" in folded_url:
            score += 24
        if any(hint in folded_url for hint in ["basvuru-formu", "yonergesi", "bolum-baskaninin-mesaji", "akademik-kadro"]):
            score -= 10
    elif intent == "definition":
        if any(hint in folded_url for hint in ["cift-anadal", "yandal", "ogrenci-isleri"]):
            score += 8
    elif intent == "application_requirements":
        if any(hint in folded_url for hint in ["yonerge", "yönerge", "cift-anadal-yandal", "ogrenci-isleri"]):
            score += 14
        if any(hint in folded_url for hint in ["program-listesi", "basvuru-formu"]):
            score -= 8

    if "/en/" in folded_url and detect_question_language(analysis["question"]) == "Turkish":
        score -= 1

    return score


def discover_candidate_source_urls(question, limit=5):
    analysis = analyze_query(question)
    slug_candidates = get_query_slug_candidates(analysis)

    if analysis["intent"] == "faculty":
        head_urls = discover_department_head_urls(question)
        if head_urls:
            return head_urls[:limit]

    scored_urls = []
    for url in get_acu_sitemap_urls():
        if not is_official_acu_source_url(url):
            continue
        score = score_candidate_source_url(url, analysis, slug_candidates)
        if score > 0:
            scored_urls.append((score, url))

    scored_urls.sort(key=lambda item: item[0], reverse=True)
    urls = [url for _score, url in scored_urls[:limit]]

    if analysis["intent"] in {"program_options", "definition", "application_requirements"}:
        urls.insert(0, PROGRAM_OPTIONS_HUB_URL)

    return list(dict.fromkeys(urls))[:limit]


def ensure_source_url(url, force=False):
    if not force and KnowledgeBase.objects.filter(url=url).exists():
        return

    try:
        crawler = AcibademSiteCrawler(url, max_pages=4, max_pdfs=8)
        crawler.crawl()
    except Exception:
        pass


def ensure_sources_for_question(question):
    for url in discover_candidate_source_urls(question):
        ensure_source_url(url, force=False)


def detect_department(question):
    q = normalize_text(question)

    for dept in sorted(DEPARTMENT_TITLE_MAP.keys(), key=len, reverse=True):
        if dept in q:
            return dept

    return None


def get_department_mentions(question):
    q = normalize_text(question)
    matches = []

    for dept in DEPARTMENT_TITLE_MAP.keys():
        start = q.find(dept)
        if start != -1:
            matches.append((start, dept))

    matches.sort(key=lambda x: x[0])
    return [dept for _, dept in matches]


def detect_source_and_target_departments(question):
    mentions = get_department_mentions(question)

    if len(mentions) >= 2:
        return mentions[0], mentions[1]

    if len(mentions) == 1:
        return mentions[0], None

    return None, None


def is_option_question(question):
    q = normalize_text(question)
    return any(hint in q for hint in OPTION_QUESTION_HINTS)


def is_application_requirements_question(question):
    q = normalize_text(question)
    folded = ascii_fold(question)
    has_track = detect_track(question) is not None
    has_requirement_hint = any(hint in q or hint in folded for hint in APPLICATION_REQUIREMENT_HINTS)
    return has_track and has_requirement_hint


def is_yes_no_option_question(question):
    q = normalize_text(question)

    yes_no_hints = [
        "can ",
        "is ",
        "are ",
        "eligible for",
        "available for",
        "apply to",
    ]

    return any(hint in q for hint in yes_no_hints)


def is_definition_question(question):
    q = normalize_text(question)
    folded = ascii_fold(question)
    has_track = detect_track(question) is not None

    definition_patterns = [
        "what is minor",
        "what is a minor",
        "define minor",
        "what is double major",
        "what is a double major",
        "define double major",
    ]

    turkish_definition_hints = [
        "nedir",
        "ne demek",
        "ne anlama gelir",
        "tanimi nedir",
        "tanımı nedir",
        "aciklar misin",
        "açıklar mısın",
    ]

    return (
        any(pattern in q for pattern in definition_patterns)
        or (has_track and any(hint in q or hint in folded for hint in turkish_definition_hints))
    )


def extract_keywords(question):
    q = normalize_text(question)
    keywords = []

    for phrase in KNOWN_PHRASES:
        if phrase in q:
            keywords.append(phrase)

    words = re.findall(r"[a-zA-Z]+", q)
    for word in words:
        if len(word) > 2 and word not in STOP_WORDS:
            keywords.append(word)

    return list(dict.fromkeys(keywords))


def tokenize_for_rag(text):
    normalized = normalize_text(text)
    tokens = re.findall(r"[a-zA-ZçğıİöşüÇĞIİÖŞÜ0-9]+", normalized)
    return [token for token in tokens if len(token) > 2 and token not in STOP_WORDS]


def semantic_ngrams(text, min_n=3, max_n=5, max_grams=1200):
    folded = ascii_fold(normalize_text(text))
    words = re.findall(r"[a-z0-9]+", folded)
    grams = []

    for word in words:
        if len(word) <= min_n:
            grams.append(word)
            continue

        padded = f" {word} "
        for n in range(min_n, max_n + 1):
            if len(padded) < n:
                continue
            grams.extend(padded[index:index + n] for index in range(len(padded) - n + 1))
            if len(grams) >= max_grams:
                return grams[:max_grams]

    return grams


def build_query_search_text(question):
    analysis = analyze_query(question)
    additions = []

    additions.extend(analysis["keywords"])
    additions.extend(get_department_slug_candidates(question))

    if analysis["intent"] == "faculty":
        additions.extend(["bolum baskani", "department head", "akademik kadro"])
    elif analysis["intent"] == "academic_role":
        additions.extend(["yonetim", "dekan", "dekanlik", "fakulte yonetimi"])
    elif analysis["intent"] == "faculty_departments":
        additions.extend(["fakulte bolumler lisans programlari"])
    elif analysis["intent"] == "curriculum":
        additions.extend(["dersler", "ders plani", "curriculum", "zorunlu", "secmeli"])
    elif analysis["intent"] == "program_options":
        additions.extend(["program listesi", "basvurabilir", analysis["track"] or ""])
    elif analysis["intent"] == "application_requirements":
        additions.extend(["basvuru kabul kayit kosullari yonerge genel not ortalamasi kontenjan"])
    elif analysis["intent"] == "definition":
        if analysis["track"] == "double major":
            additions.extend(["cift anadal programi cap tanimlar madde 4 ikinci diploma"])
        elif analysis["track"] == "minor":
            additions.extend(["yandal programi tanimlar madde 4 sertifika"])

    folded_question = ascii_fold(question)
    if any(hint in folded_question for hint in ["kuruldu", "kurulus", "kurulus tarihi", "ne zaman kuruldu"]):
        additions.extend(["kuruldu kurulus 2007 universite hakkinda"])

    if any(hint in folded_question for hint in ["nerede", "adres", "konum", "kampus", "kampusu"]):
        additions.extend(["adres kampus kayisdagi atasehir iletisim"])

    return " ".join([question, *[item for item in additions if item]])


def chunk_text(text, max_chars=RAG_MAX_CHARS_PER_CHUNK, overlap=RAG_CHUNK_OVERLAP):
    text = re.sub(r"\s+", " ", (text or "").strip())

    if not text:
        return []

    chunks = []
    start = 0

    while start < len(text):
        end = min(start + max_chars, len(text))

        if end < len(text):
            sentence_end = max(text.rfind(".", start, end), text.rfind("?", start, end), text.rfind("!", start, end))
            if sentence_end > start + max_chars // 2:
                end = sentence_end + 1

        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)

        if end >= len(text):
            break

        start = max(end - overlap, start + 1)

    return chunks


def term_frequency(tokens):
    frequencies = {}
    for token in tokens:
        frequencies[token] = frequencies.get(token, 0) + 1
    return frequencies


def cosine_similarity(query_vector, doc_vector):
    numerator = sum(query_vector.get(term, 0) * doc_vector.get(term, 0) for term in query_vector)
    query_norm = sum(value * value for value in query_vector.values()) ** 0.5
    doc_norm = sum(value * value for value in doc_vector.values()) ** 0.5

    if not query_norm or not doc_norm:
        return 0

    return numerator / (query_norm * doc_norm)


def build_rag_passages(records=None):
    passages = []

    for record in (records if records is not None else KnowledgeBase.objects.all()):
        if not is_official_acu_source_url(record.url):
            continue

        source_text = (
            f"{record.title}\n"
            f"Topic: {record.topic}\n"
            f"URL: {record.url}\n"
            f"{record.content}"
        )

        for index, chunk in enumerate(chunk_text(record.content)):
            passage_text = (
                f"Source: {record.title}\n"
                f"Topic: {record.topic}\n"
                f"URL: {record.url}\n"
                f"Passage: {chunk}"
            )
            searchable_text = (
                f"{record.title}\n"
                f"Topic: {record.topic}\n"
                f"URL: {record.url}\n"
                f"{chunk}"
            )
            tokens = tokenize_for_rag(searchable_text)

            if tokens:
                passages.append({
                    "record": record,
                    "index": index,
                    "text": passage_text,
                    "tokens": tokens,
                })

    return passages


def get_passage_boost(question, passage):
    record = passage["record"]
    url = normalize_text(record.url)
    folded_url = ascii_fold(record.url)
    title = normalize_text(record.title)
    topic = normalize_text(record.topic)
    text = normalize_text(passage["text"])
    boost = 0.0
    department_slugs = set(get_department_slug_candidates(question))
    has_department_slug = any(slug and slug in folded_url for slug in department_slugs)

    if has_department_slug:
        boost += 1.0

    if is_curriculum_question(question):
        folded_url_l = folded_url.lower()

        if "progcourses.aspx" in folded_url_l:
            boost += 50.0

        elif "progabout.aspx" in folded_url_l:
            boost -= 5.0

        elif "obs.acibadem.edu.tr" in folded_url_l:
            boost += 3.0

        if any(bad in folded_url_l for bad in ["/haberler/", "/duyurular/", "summit", "codehub"]):
            boost -= 10.0

    if is_faculty_question(question):
        if "progabout.aspx" in url or "bolum baskaninin mesaji" in url or "bölüm başkanının mesajı" in title:
            boost += 1.0
        if "bölüm başkanı" in text or "bolum baskani" in text or "department head" in text:
            boost += 0.5

    if is_academic_role_question(question):
        if topic == "management" or "yonetim" in url or "yönetim" in title:
            boost += 4.0
        if any(role_hint in text for role_hint in ["dekan", "dekanlık", "dekanlik", "dean"]):
            boost += 2.0
        if any(bad_hint in url for bad_hint in ["akademik-kadro", "bolum-baskaninin-mesaji", "ders", "mufredat"]):
            boost -= 2.0

    track = detect_track(question)
    if track == "double major":
        if "cift-anadal-program-listesi" in url or topic == "double_major_options":
            boost += 1.2
        if "bolum baskaninin mesaji" in url:
            boost -= 0.5

    if track == "minor":
        if "yandal-program-listesi" in url or topic == "minor_options":
            boost += 1.2

    return boost


def get_vector_candidate_records(question, limit=RAG_VECTOR_RECORD_LIMIT):
    try:
        question_embedding = get_text_embedding(build_query_search_text(question), timeout=20)
    except Exception:
        question_embedding = None

    if not question_embedding:
        return None

    records = []
    queryset = (
        KnowledgeBase.objects
        .filter(embedding__isnull=False)
        .annotate(distance=CosineDistance("embedding", question_embedding))
        .order_by("distance")[:limit]
    )

    for record in queryset:
        if is_official_acu_source_url(record.url):
            records.append(record)

    # Force curriculum pages into candidate set for curriculum questions
    if is_curriculum_question(question):
        curriculum_records = KnowledgeBase.objects.filter(
            url__icontains="progCourses"
        )

        existing_ids = {record.id for record in records}
        for record in curriculum_records:
            if record.id not in existing_ids and is_official_acu_source_url(record.url):
                records.append(record)

    return records or None


def get_lexical_candidate_records(query_search_text, limit=RAG_VECTOR_RECORD_LIMIT):
    tokens = [
        token for token in tokenize_for_rag(query_search_text)
        if len(token) > 3 and ascii_fold(token) not in GENERAL_RAG_GENERIC_TOKENS
    ]

    if not tokens:
        return []

    try:
        from django.db.models import Q

        query = Q()
        for token in list(dict.fromkeys(tokens))[:8]:
            query |= Q(title__icontains=token)
            query |= Q(topic__icontains=token)
            query |= Q(url__icontains=token)
            query |= Q(content__icontains=token)

        records = [
            record for record in KnowledgeBase.objects.filter(query)[:limit * 2]
            if is_official_acu_source_url(record.url)
        ]
    except Exception:
        return []

    return records[:limit]


def merge_candidate_records(*record_groups):
    merged = []
    seen = set()

    for records in record_groups:
        for record in records or []:
            if record.id in seen:
                continue
            seen.add(record.id)
            merged.append(record)

    return merged


def get_rag_context(question, top_k=RAG_TOP_K):
    query_search_text = build_query_search_text(question)
    query_tokens = tokenize_for_rag(query_search_text)
    query_semantic_tokens = semantic_ngrams(query_search_text)

    if not query_tokens:
        return ""

    candidate_records = merge_candidate_records(
        get_vector_candidate_records(question),
        get_lexical_candidate_records(query_search_text),
    )
    candidate_records = candidate_records or None
    passages = build_rag_passages(candidate_records)

    if not passages and candidate_records is not None:
        passages = build_rag_passages()

    if not passages:
        return ""

    document_frequency = {}

    for passage in passages:
        for token in set(passage["tokens"]):
            document_frequency[token] = document_frequency.get(token, 0) + 1

    total_documents = len(passages)

    def tfidf_vector(tokens):
        tf = term_frequency(tokens)
        vector = {}

        for token, count in tf.items():
            df = document_frequency.get(token, 0)
            idf = math.log((1 + total_documents) / (1 + df)) + 1
            vector[token] = count * idf

        return vector

    query_vector = tfidf_vector(query_tokens)
    query_semantic_vector = term_frequency(query_semantic_tokens)
    preliminary_passages = []

    for passage in passages:
        passage_vector = tfidf_vector(passage["tokens"])
        lexical_score = cosine_similarity(query_vector, passage_vector)
        score = lexical_score + get_passage_boost(question, passage)

        if score > 0:
            preliminary_passages.append((score, passage, lexical_score))

    if not preliminary_passages:
        return ""

    preliminary_passages.sort(key=lambda item: item[0], reverse=True)

    scored_passages = []
    for preliminary_score, passage, lexical_score in preliminary_passages[: max(30, top_k * 8)]:
        passage_semantic_vector = term_frequency(semantic_ngrams(passage["text"]))
        semantic_score = cosine_similarity(query_semantic_vector, passage_semantic_vector)
        boost = get_passage_boost(question, passage)
        final_score = (0.70 * lexical_score) + (0.30 * semantic_score) + boost
        scored_passages.append((final_score, passage))

    scored_passages.sort(key=lambda item: item[0], reverse=True)

    # If the best match is too weak, refuse to answer instead of letting the
    # LLM hallucinate from an unrelated page. The threshold is intentionally
    # low — we only filter out obviously irrelevant results (e.g. a news
    # article matching one keyword by accident).
    top_score = scored_passages[0][0]
    if top_score < RAG_MIN_TOP_SCORE:
        return ""

    context_parts = []
    seen = set()

    for score, passage in scored_passages:
        key = (passage["record"].id, passage["index"])
        if key in seen:
            continue

        seen.add(key)
        context_parts.append(f"Relevance Score: {score:.3f}\n{passage['text']}")

        if len(context_parts) >= top_k:
            break

    return "\n\n".join(context_parts)


def is_broad_info_question(question):
    if is_faculty_question(question) or is_curriculum_question(question) or detect_track(question):
        return False

    folded = ascii_fold(question)
    return any(hint in folded for hint in [ascii_fold(item) for item in BROAD_INFO_HINTS])


def extract_context_passages(context):
    pattern = re.compile(
        r"Relevance Score:\s*(?P<score>[0-9.]+)\s+"
        r"Source:\s*(?P<source>.*?)\n"
        r"Topic:\s*(?P<topic>.*?)\n"
        r"URL:\s*(?P<url>.*?)\n"
        r"Passage:\s*(?P<passage>.*?)(?=\n\nRelevance Score:|\Z)",
        flags=re.DOTALL,
    )
    return [
        {
            "score": float(match.group("score")),
            "source": match.group("source").strip(),
            "topic": match.group("topic").strip(),
            "url": match.group("url").strip(),
            "passage": re.sub(r"\s+", " ", match.group("passage")).strip(),
        }
        for match in pattern.finditer(context or "")
    ]


def keyword_variants(token):
    folded = ascii_fold(token)
    variants = {folded}

    for suffix in [
        "leri", "lari", "inin", "unun", "nin", "nun", "den", "dan",
        "de", "da", "ye", "ya", "ne", "na", "si", "su", "u", "i", "e", "a",
    ]:
        if len(folded) > len(suffix) + 3 and folded.endswith(suffix):
            variants.add(folded[:-len(suffix)])

    return {variant for variant in variants if len(variant) > 2}


def split_answer_sentences(text):
    cleaned = re.sub(r"\s+", " ", (text or "")).strip()
    if not cleaned:
        return []

    parts = re.split(r"(?<=[.!?])\s+", cleaned)
    return [part.strip(" ;:-") for part in parts if len(part.strip()) > 25]


def score_extractive_sentence(question, sentence):
    folded_sentence = ascii_fold(sentence)

    if any(noise in folded_sentence for noise in EXTRACTIVE_NOISE_HINTS):
        return -1

    query_tokens = tokenize_for_rag(question)
    focus_tokens = [
        token for token in query_tokens
        if ascii_fold(token) not in GENERIC_EXTRACTIVE_TOKENS
    ]
    has_focus_match = False
    score = 0

    for token in query_tokens:
        variants = keyword_variants(token)
        if any(variant in folded_sentence for variant in variants):
            score += 3 if len(token) > 5 else 1
            if token in focus_tokens:
                has_focus_match = True

    if focus_tokens and not has_focus_match:
        return -1

    if "acibadem" in folded_sentence:
        score += 1
    if len(sentence) > 260:
        score -= 1

    return score


def build_extractive_answer(question, context, max_sentences=3):
    entries = extract_context_passages(context)
    if not entries:
        return ""

    candidates = []
    for entry_index, entry in enumerate(entries[:3]):
        for sentence_index, sentence in enumerate(split_answer_sentences(entry["passage"])):
            score = score_extractive_sentence(question, sentence)
            if score > 0:
                candidates.append((score, entry_index, sentence_index, sentence))

    if not candidates:
        return ""

    candidates.sort(key=lambda item: (-item[0], item[1], item[2]))
    selected = []
    seen = set()

    for _score, _entry_index, _sentence_index, sentence in candidates:
        key = ascii_fold(sentence)
        if key in seen:
            continue

        seen.add(key)
        selected.append(sentence)

        if len(selected) >= max_sentences:
            break

    selected.sort(key=lambda sentence: entries[0]["passage"].find(sentence) if sentence in entries[0]["passage"] else 10_000)
    answer = " ".join(selected).strip()

    if answer and answer[-1] not in ".!?":
        answer += "."

    return answer


def get_best_official_record(predicate):
    for record in KnowledgeBase.objects.all():
        if is_official_acu_source_url(record.url) and predicate(record):
            return record
    return None


def extract_department_head_from_text(text):
    patterns = [
        r"((?:Prof\.|Doç\.|Dr\. Öğr\. Üyesi|Dr\.|Öğr\. Gör\.)\s*(?:Dr\.\s*)?[A-ZÇĞİÖŞÜ][A-Za-zÇĞİÖŞÜçğıöşü]+(?:\s+[A-ZÇĞİÖŞÜ][A-Za-zÇĞİÖŞÜçğıöşü]+){1,3})\s*[-–]\s*Bölüm Başkanı",
        r"Bölüm Başkanı\s*[-:]?\s*((?:Prof\.|Doç\.|Dr\. Öğr\. Üyesi|Dr\.|Öğr\. Gör\.)\s*(?:Dr\.\s*)?[A-ZÇĞİÖŞÜ][A-Za-zÇĞİÖŞÜçğıöşü]+(?:\s+[A-ZÇĞİÖŞÜ][A-Za-zÇĞİÖŞÜçğıöşü]+){1,3})",
        r"Bölüm Başkanı\s*[-:]?\s*(.+?)(?:\s+Tarihçe|\s+Kazanılan Derece|\s+Kabul Koşulları|\s+Program İçeriği)",
        r"Bölüm Başkanı\s*[-:]?\s*(.+?)(?:\s+Sevgili Öğrenciler|\s+Üstün Başarılar|\s+Günümüzde)",
        r"Program Başkanı\s*[-:]?\s*(.+?)(?:\s+Tarihçe|\s+Kazanılan Derece|\s+Kabul Koşulları|\s+Program İçeriği)",
        r"Department Head\s*[-:]?\s*(.+?)(?:\s+History|\s+Degree|\s+Admission|\s+Program)",
        r"((?:Prof\.|Doç\.|Dr\. Öğr\. Üyesi|Dr\.|Öğr\. Gör\.)\s*(?:Dr\.\s*)?[A-ZÇĞİÖŞÜ][A-Za-zÇĞİÖŞÜçğıöşü]+(?:\s+[A-ZÇĞİÖŞÜ][A-Za-zÇĞİÖŞÜçğıöşü]+){1,3})\s+Sor Cevaplayalım",
    ]

    for pattern in patterns:
        match = re.search(pattern, text or "", flags=re.IGNORECASE)
        if match:
            head = " ".join(match.group(1).split()).strip(" .")
            for stop in [
                " Biyomedikal Mühendisliği",
                " Bilgisayar Mühendisliği",
                " Moleküler Biyoloji",
                " Tarihçe",
                " Acıbadem",
                " Sevgili",
                " Program",
                " Günümüzde",
                " Sor Cevaplayalım",
            ]:
                head = head.split(stop, 1)[0].strip(" .")
            return head

    return ""


def clean_person_name(name):
    name = (name or "").strip()
    for stop in [
        " Tarihçe",
        " Acıbadem",
        " Sor Cevaplayalım",
        " Bölüm",
        " Program",
        " Hakkında",
    ]:
        name = name.split(stop, 1)[0].strip(" .")

    parts = []
    title_parts = {"Prof.", "Doç.", "Dr.", "Öğr.", "Üyesi", "Gör."}
    stop_tokens = {
        "acibadem",
        "bolum",
        "cevaplayalim",
        "derece",
        "fakulte",
        "hakkinda",
        "kabul",
        "kosullari",
        "kurulu",
        "program",
        "sor",
        "tarihce",
    }

    for part in " ".join(name.split()).strip(" .").split():
        folded_part = ascii_fold(part.strip(" .,:;()[]"))
        if folded_part in stop_tokens:
            break

        if part in title_parts:
            parts.append(part)
        elif len(part) > 2 and part.isupper():
            parts.append(part.capitalize())
        else:
            parts.append(part)

    return " ".join(parts).strip(" .")


def get_department_search_names(question):
    names = []
    department = detect_department(question)

    if department:
        names.append(DEPARTMENT_TURKISH_TITLE_MAP.get(department, ""))
        names.append(DEPARTMENT_TITLE_MAP.get(department, ""))
        names.append(department)

    phrase = extract_department_phrase(question)
    if phrase:
        names.append(phrase)

    return list(dict.fromkeys(name for name in names if name))


def extract_department_head_near_department(text, question):
    if not text:
        return ""

    folded_text = ascii_fold(text)
    department_names = get_department_search_names(question)

    title_name_pattern = (
        r"(?:Prof\.|Doç\.|Dr\. Öğr\. Üyesi|Dr\.|Öğr\. Gör\.)\s*"
        r"(?:Dr\.\s*)?"
        r"[A-ZÇĞİÖŞÜ][A-Za-zÇĞİÖŞÜçğıöşü]+"
        r"(?:\s+[A-ZÇĞİÖŞÜ][A-Za-zÇĞİÖŞÜçğıöşü]+){1,4}"
    )

    for department_name in department_names:
        folded_department = ascii_fold(department_name)
        if not folded_department:
            continue

        for match in re.finditer(re.escape(folded_department), folded_text):
            start = max(0, match.start() - 180)
            end = min(len(text), match.end() + 220)
            window = text[start:end]

            before_patterns = [
                rf"({title_name_pattern})\s+{re.escape(department_name)}(?:\s+Bölümü)?\s+Bölüm Başkanı",
                rf"({title_name_pattern})\s+{re.escape(department_name)}(?:\s+Department)?\s+(?:Head|Chair)",
            ]
            after_patterns = [
                rf"{re.escape(department_name)}(?:\s+Bölümü)?\s+Bölüm Başkanı\s+({title_name_pattern})",
                rf"{re.escape(department_name)}(?:\s+Department)?\s+(?:Head|Chair)\s+({title_name_pattern})",
            ]

            for pattern in before_patterns + after_patterns:
                person_match = re.search(pattern, window, flags=re.IGNORECASE)
                if person_match:
                    return clean_person_name(person_match.group(1))

            if "Bölüm Başkanı" in window:
                head_candidates = list(re.finditer(title_name_pattern, window))
                if head_candidates:
                    department_position = window.lower().find(department_name.lower())
                    if department_position == -1:
                        department_position = len(window) // 2

                    closest = min(
                        head_candidates,
                        key=lambda item: abs(item.start() - department_position),
                    )
                    return clean_person_name(closest.group(0))

    return ""


def extract_unit_tokens_for_role_question(question):
    folded = ascii_fold(question)
    folded = re.sub(
        r"\b(?:kimdir|kim|who|is|the|of|adi|ismi|nedir|dekan[ia]?|dean|yardimcisi|yrd|fakulte sekreteri|faculty secretary)\b",
        " ",
        folded,
    )
    tokens = re.findall(r"[a-z0-9]+", folded)
    ignored = {
        "ve", "ile", "and", "bir", "bu", "su", "hangi", "acaba", "universitesi",
        "acibadem", "nin", "nın", "nun", "nün", "in", "un", "de", "da",
    }
    return [
        token for token in tokens
        if len(token) > 2 and token not in ignored and token not in STOP_WORDS
    ]


def extract_unit_phrase_for_role_question(question):
    cleaned = re.sub(
        r"\b(?:dekan yardımcısı|dekan yardimcisi|fakülte sekreteri|fakulte sekreteri|dekanı|dekani|dekan|dean|kimdir|kim|who\s+is)\b",
        " ",
        question or "",
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ?.")

    if not cleaned:
        return ""

    return cleaned[:1].upper() + cleaned[1:]


def extract_faculty_unit_tokens(question):
    folded = ascii_fold(question)
    folded = re.sub(
        r"\b(?:kac|kaç|hangi|bolum|bolumler|bolumleri|bölüm|bölümler|bölümleri|var|nelerdir|nedir|departments|how|many|which|are|there)\b",
        " ",
        folded,
    )
    tokens = re.findall(r"[a-z0-9]+", folded)
    ignored = {"ve", "ile", "and", "bir", "bu", "su", "universitesi", "acibadem", "in", "de", "da"}
    return [token for token in tokens if len(token) > 2 and token not in ignored and token not in STOP_WORDS]


def record_matches_faculty_unit(record, question):
    tokens = extract_faculty_unit_tokens(question)
    if not tokens:
        return False

    folded = ascii_fold(f"{record.title} {record.url} {record.content}")
    hits = sum(1 for token in tokens if token in folded)
    return hits >= min(3, len(tokens))


def extract_departments_from_faculty_record(content):
    if not content:
        return []

    text = " ".join(content.split())
    match = re.search(
        r"Bölümler\s+(.+?)(?:\s+Eğitim Kurulu|\s+Araştırma|\s+Tanıtım|\s+Sanal Tur|\s+DEKANLIK|\s+Fakülte Hakkında|\s+Vizyon)",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        return []

    section = match.group(1)
    candidates = re.findall(
        r"[A-ZÇĞİÖŞÜ][A-Za-zÇĞİÖŞÜçğıöşü]+(?:\s+ve\s+[A-ZÇĞİÖŞÜ][A-Za-zÇĞİÖŞÜçğıöşü]+)?(?:\s+[A-ZÇĞİÖŞÜ][A-Za-zÇĞİÖŞÜçğıöşü]+)*\s+(?:Mühendisliği|Biyoloji ve Genetik|Bölümü)",
        section,
    )

    departments = []
    seen = set()
    for candidate in candidates:
        cleaned = clean_response_text(candidate).strip(" .")
        folded = ascii_fold(cleaned)
        if folded in seen:
            continue
        if any(bad in folded for bad in ["fakulte", "dekan", "yonetim", "akademik"]):
            continue
        seen.add(folded)
        departments.append(cleaned)

    return departments


def faculty_base_url(url):
    match = re.match(r"(.+/fakultesi)(?:/.*)?$", url or "")
    return match.group(1) if match else (url or "").rstrip("/")


def extract_departments_from_faculty_child_pages(base_url):
    if not base_url:
        return []

    prefix = base_url.rstrip("/") + "/bolumler/"
    departments = []
    seen = set()

    for record in KnowledgeBase.objects.filter(url__startswith=prefix):
        suffix = (record.url or "")[len(prefix):].strip("/")
        if not suffix or "/" in suffix:
            continue

        title = (record.title or "").split("|", 1)[0].strip()
        folded_title = ascii_fold(title)
        if not title or any(bad in folded_title for bad in ["akademik kadro", "bolum baskani", "komisyon", "hakkinda"]):
            continue

        key = ascii_fold(title)
        if key in seen:
            continue
        seen.add(key)
        departments.append(title)

    return departments


def answer_faculty_departments_question_directly(question):
    # Content-driven: needs the question to mention a faculty (matched by
    # record_matches_faculty_unit). No hint-list gate.
    if not is_faculty_department_list_question(question):
        return ""

    records = [
        record for record in KnowledgeBase.objects.all()
        if is_official_acu_source_url(record.url)
        and record_matches_faculty_unit(record, question)
    ]
    if not records:
        return ""

    scored = []
    for record in records:
        folded = ascii_fold(f"{record.title} {record.topic} {record.url} {record.content}")
        score = 0
        if "/bolumler/" not in folded and folded.rstrip("/").endswith("fakultesi"):
            score += 20
        if "bolumler" in folded:
            score += 8
        if any(bad in folded for bad in ["akademik-kadro", "yonetim", "dekanlik", "komisyon"]):
            score -= 6
        scored.append((score, record))

    scored.sort(key=lambda item: item[0], reverse=True)

    child_department_candidates = []
    for _score, record in scored:
        child_department_candidates.append(
            extract_departments_from_faculty_child_pages(faculty_base_url(record.url))
        )
    best_child_departments = max(child_department_candidates, key=len, default=[])

    for _score, record in scored:
        departments = extract_departments_from_faculty_record(record.content)
        if len(departments) <= 1:
            child_departments = extract_departments_from_faculty_child_pages(faculty_base_url(record.url)) or best_child_departments
            if child_departments:
                departments = child_departments

        if not departments:
            continue

        unit_name = extract_unit_phrase_for_role_question(question)
        unit_name = re.sub(
            r"\b(?:kaç|kac|hangi|bölüm|bolum|bölümler|bolumler|bölümleri|bolumleri|var|nelerdir)\b",
            " ",
            unit_name,
            flags=re.IGNORECASE,
        )
        unit_name = re.sub(r"\s+", " ", unit_name).strip(" ?.")
        unit_name = unit_name[:1].upper() + unit_name[1:] if unit_name else "İlgili fakülte"

        if detect_question_language(question) == "Turkish":
            return (
                f"{unit_name} resmi sayfasında listelenen {len(departments)} bölüm vardır:\n"
                + "\n".join(f"- {department}." for department in departments)
            )

        return (
            f"The official page lists {len(departments)} departments in {unit_name}:\n"
            + "\n".join(f"- {department}." for department in departments)
        )

    return ""


def record_matches_role_unit(record, question):
    tokens = extract_unit_tokens_for_role_question(question)
    if not tokens:
        return False

    folded = ascii_fold(f"{record.title} {record.url} {record.content}")
    hits = sum(1 for token in tokens if token in folded)
    required = min(3, len(tokens))
    return hits >= required


def extract_person_for_academic_role(text, role):
    if not text:
        return ""

    title_name_pattern = (
        r"(?:Prof\.|Doç\.|Dr\. Öğr\. Üyesi|Dr\.|Öğr\. Gör\.)\s*"
        r"(?:Dr\.\s*)?"
        r"[A-ZÇĞİÖŞÜ][A-Za-zÇĞİÖŞÜçğıöşü]+"
        r"(?:\s+[A-ZÇĞİÖŞÜ][A-Za-zÇĞİÖŞÜçğıöşü]+){1,4}"
    )

    if role == "dean":
        role_patterns = [
            rf"({title_name_pattern})\s+Dekan(?!\s*(?:Yrd|Yardımcısı|Yardimcisi))",
            rf"DEKANLIK\s+({title_name_pattern})\s+Dekan",
            rf"Dekan\s+({title_name_pattern})",
        ]
    elif role == "vice_dean":
        role_patterns = [
            rf"({title_name_pattern})\s+Dekan\s*(?:Yrd\.?|Yardımcısı|Yardimcisi)",
            rf"Dekan\s*(?:Yrd\.?|Yardımcısı|Yardimcisi)\s+({title_name_pattern})",
        ]
    elif role == "faculty_secretary":
        role_patterns = [
            r"FAKÜLTE SEKRETERİ\s+([A-ZÇĞİÖŞÜ][A-Za-zÇĞİÖŞÜçğıöşü]+(?:\s+[A-ZÇĞİÖŞÜ][A-Za-zÇĞİÖŞÜçğıöşü]+){1,3})",
            r"([A-ZÇĞİÖŞÜ][A-Za-zÇĞİÖŞÜçğıöşü]+(?:\s+[A-ZÇĞİÖŞÜ][A-Za-zÇĞİÖŞÜçğıöşü]+){1,3})\s+Fakülte Sekreteri",
        ]
    else:
        return ""

    for pattern in role_patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return clean_person_name(match.group(1))

    return ""


def academic_role_label(role, language):
    labels = {
        "dean": ("dekanı", "dean"),
        "vice_dean": ("dekan yardımcısı", "vice dean"),
        "faculty_secretary": ("fakülte sekreteri", "faculty secretary"),
    }
    tr, en = labels.get(role, ("yetkilisi", "official"))
    return tr if language == "Turkish" else en


def answer_academic_role_question_directly(question):
    role = detect_academic_role(question)
    if not role:
        return ""

    records = [
        record for record in KnowledgeBase.objects.all()
        if is_official_acu_source_url(record.url)
        and record_matches_role_unit(record, question)
    ]

    scored = []
    for record in records:
        folded = ascii_fold(f"{record.title} {record.topic} {record.url} {record.content}")
        score = 0
        if "yonetim" in folded or "management" in folded:
            score += 20
        if "dekan" in folded or "dean" in folded:
            score += 8
        if any(bad in folded for bad in ["akademik-kadro", "bolum-baskaninin-mesaji", "ders plani"]):
            score -= 8
        scored.append((score, record))

    scored.sort(key=lambda item: item[0], reverse=True)

    for _score, record in scored:
        person = extract_person_for_academic_role(record.content, role)
        if not person:
            continue

        unit_name = extract_unit_phrase_for_role_question(question) or "İlgili birim"
        language = detect_question_language(question)
        label = academic_role_label(role, language)

        if language == "Turkish":
            return f"{unit_name} {label} {person}."
        return f"The {label} is {person}."

    return ""


def answer_faculty_question_from_official_records(question):
    records = [
        record for record in KnowledgeBase.objects.all()
        if is_official_acu_source_url(record.url)
        and record_matches_department(record, question)
    ]

    scored_records = []
    for record in records:
        folded = ascii_fold(f"{record.title} {record.topic} {record.url} {record.content}")
        score = 0
        if "bolum baskani" in folded or "department head" in folded or "department chair" in folded:
            score += 10
        if "yonetim" in folded or "management" in folded:
            score += 4
        if "akademik-kadro" in folded or "academic-staff" in folded:
            score += 2
        scored_records.append((score, record))

    scored_records.sort(key=lambda item: item[0], reverse=True)

    for _score, record in scored_records:
        head = extract_department_head_near_department(record.content, question)
        if head:
            if detect_question_language(question) == "Turkish":
                return f"{get_department_display_name(question)} bölüm başkanı {head}."

            return f"The head of the {get_department_display_name(question)} department is {head}."

    return ""


def answer_faculty_question_directly(question):
    record = None
    for source_url in discover_department_head_urls(question):
        record = get_best_official_record(lambda item, target=source_url: item.url == target)
        if record:
            break

    if not record:
        return ""

    head = extract_department_head_from_text(record.content)
    if not head:
        return answer_faculty_question_from_official_records(question)

    head = clean_person_name(head)

    if detect_question_language(question) == "Turkish":
        return f"{get_department_display_name(question)} bölüm başkanı {head}."

    return f"The head of the {get_department_display_name(question)} department is {head}."


# OBS's compulsory-course rows look like:
#   "AHP 103   Ameliyathane Teknolojileri    2+0+0   Zorunlu   5"
# The hour slots can be multi-digit (e.g. "0+16+0" for the AHP 102 lab).
# The earlier regex had \d (single digit) per slot and missed those rows.
# Pattern: <CODE> <NAME> <T+P+L> Zorunlu <ECTS>
COURSE_ROW_REGEX = re.compile(
    r"\b([A-Z]{2,4}\s+\d{2,4})\s+"                                    # course code
    r"((?:(?!\b[A-Z]{2,4}\s+\d{2,4}\b).)+?)\s+"                       # name (lazy)
    r"\d+\+\d+\+\d+\s+"                                                # T+P+L (multi-digit OK)
    r"Zorunlu\s+\d+",                                                  # Zorunlu + ECTS
    flags=re.UNICODE,
)

# OBS uses "<N>.Yarıyıl Ders Planı" as the section header for each semester.
# We split the body on these headers so we can isolate semester N's table.
SEMESTER_HEADER_REGEX = re.compile(
    r"(\d{1,2})\s*\.\s*Yar[ıi]y[ıi]l\s+Ders\s+Plan[ıi]",
    flags=re.UNICODE | re.IGNORECASE,
)


def split_obs_text_by_semester(text):
    """Return ``{semester_int: section_text}`` for an OBS courses page body.

    If the body is not in OBS format (no "N.Yarıyıl Ders Planı" headers)
    the entire body is returned under key 0.
    """
    if not text:
        return {0: ""}

    matches = list(SEMESTER_HEADER_REGEX.finditer(text))
    if not matches:
        return {0: text}

    sections = {}
    for index, match in enumerate(matches):
        try:
            semester = int(match.group(1))
        except ValueError:
            continue
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        sections[semester] = text[start:end]
    return sections


def _extract_courses_from_section(section_text, limit):
    courses = []
    seen = set()
    for code, raw_name in COURSE_ROW_REGEX.findall(section_text or ""):
        clean_name = " ".join(raw_name.split()).strip()
        if not clean_name or "Seçmeli" in clean_name:
            continue
        key = f"{code} {clean_name}"
        if key in seen:
            continue
        seen.add(key)
        courses.append(key)
        if len(courses) >= limit:
            break
    return courses


def extract_required_courses_from_text(text, limit=40, semester=None):
    """Pull compulsory courses from an OBS curriculum page body.

    Parameters
    ----------
    text : str
        The KnowledgeBase record's ``content`` field — typically the OBS
        progCourses page body.
    limit : int
        Maximum number of courses to return.
    semester : int or None
        If given (e.g. 1 for "1. yarıyıl"), restrict to that semester's
        section. If the body has no semester headers, return all matches.
    """
    if semester is None:
        return _extract_courses_from_section(text, limit)

    sections = split_obs_text_by_semester(text)
    target = sections.get(semester) or sections.get(0, "")
    return _extract_courses_from_section(target, limit)


def _department_aliases_for_question(question):
    """Return ascii-folded alias phrases for the department in the question.

    The matcher checks these phrases in addition to the slug candidates so a
    record indexed under a different official name (e.g. OBS's
    "Tıp Mühendisliği" for the ACU "Biyomedikal Mühendisliği" program) is
    still found.
    """
    department = detect_department(question)
    if not department:
        return []
    raw_aliases = DEPARTMENT_ALIASES.get(department, [])
    folded = []
    for alias in raw_aliases:
        folded.append(ascii_fold(alias))
        folded.append(ascii_fold(alias).replace("-", " "))
    return [phrase for phrase in dict.fromkeys(folded) if phrase]


def record_matches_department(record, question):
    """Decide if ``record`` is about the same department the question asks about.

    For OBS Bologna records we deliberately match ONLY against the title,
    because their title now carries the canonical "<faculty> / <department>"
    name (set by obs_crawler). Matching against the full body would create
    false positives — e.g. an Ameliyathane Hizmetleri page that happens to
    list "Biyomedikal Cihaz Teknolojisi" as one of its electives would
    otherwise look like a Biyomedikal Mühendisliği record.

    For non-OBS records we keep the looser title+topic+url+content match,
    because their titles are typically shorter web page titles that don't
    always include the department name.
    """
    is_obs_record = "obs.acibadem.edu.tr" in (record.url or "")

    if is_obs_record:
        searchable = record.title or ""
    else:
        searchable = f"{record.title} {record.topic} {record.url} {record.content}"

    folded = ascii_fold(searchable)

    for slug in get_department_slug_candidates(question):
        if slug in folded or slug.replace("-", " ") in folded:
            return True

    # Extra alias phrases for departments whose official OBS name differs
    # from the public-site name (e.g. Biyomedikal Müh. = Tıp Müh.).
    for alias in _department_aliases_for_question(question):
        if alias and alias in folded:
            return True

    department = detect_department(question)
    if department and department in normalize_text(searchable):
        return True

    return False


ORDINAL_WORDS = {
    1: ["birinci", "ilk"],
    2: ["ikinci"],
    3: ["ucuncu"],
    4: ["dorduncu"],
    5: ["besinci"],
    6: ["altinci"],
    7: ["yedinci"],
    8: ["sekizinci"],
}


def token_similarity(left, right):
    return SequenceMatcher(None, left or "", right or "").ratio()


def fuzzy_ordinal_value(token):
    token = ascii_fold(token).strip(" .,:;()[]")
    if not token:
        return None

    if token.isdigit():
        number = int(token)
        return number if 1 <= number <= 8 else None

    for value, words in ORDINAL_WORDS.items():
        for word in words:
            if token == word or token.startswith(word):
                return value
            if len(token) >= 4 and token_similarity(token, word) >= 0.72:
                return value

    return None


def extract_period_value_near_terms(question, period_terms):
    folded = ascii_fold(question)
    tokens = re.findall(r"[a-z0-9]+", folded)

    for index, token in enumerate(tokens):
        if not any(token.startswith(term) for term in period_terms):
            continue

        for candidate in reversed(tokens[max(0, index - 3):index]):
            value = fuzzy_ordinal_value(candidate)
            if value:
                return value

    return None


def mentions_semester_scope(question):
    folded = ascii_fold(question)
    return any(term in folded for term in ["yariyil", "donem", "semester", "term"])


def _detect_requested_semester(question):
    """Map a curriculum question to a specific semester number, or None.

    OBS Bologna pages render each semester under a "<N>.Yarıyıl Ders Planı"
    heading. We map common natural-language phrasings to that integer N so
    the extractor can return only the requested semester's compulsory list.
    """
    q = normalize_text(question)
    folded = ascii_fold(question)

    explicit_semester_patterns = [
        (1, [
            r"\b1\s*\.?\s*(?:yariyil|donem)\w*\b",
            r"\b(?:birinci|ilk)\s+(?:yariyil|donem)\w*\b",
            r"\bfirst\s+(?:semester|term)\b",
        ]),
        (2, [
            r"\b2\s*\.?\s*(?:yariyil|donem)\w*\b",
            r"\bikinci\s+(?:yariyil|donem)\w*\b",
            r"\bsecond\s+(?:semester|term)\b",
        ]),
        (3, [r"\b3\s*\.?\s*(?:yariyil|donem)\w*\b", r"\bucuncu\s+(?:yariyil|donem)\w*\b"]),
        (4, [r"\b4\s*\.?\s*(?:yariyil|donem)\w*\b", r"\bdorduncu\s+(?:yariyil|donem)\w*\b"]),
        (5, [r"\b5\s*\.?\s*(?:yariyil|donem)\w*\b", r"\bbesinci\s+(?:yariyil|donem)\w*\b"]),
        (6, [r"\b6\s*\.?\s*(?:yariyil|donem)\w*\b", r"\baltinci\s+(?:yariyil|donem)\w*\b"]),
        (7, [r"\b7\s*\.?\s*(?:yariyil|donem)\w*\b", r"\byedinci\s+(?:yariyil|donem)\w*\b"]),
        (8, [r"\b8\s*\.?\s*(?:yariyil|donem)\w*\b", r"\bsekizinci\s+(?:yariyil|donem)\w*\b"]),
    ]

    for semester, patterns in explicit_semester_patterns:
        if any(re.search(pattern, folded) for pattern in patterns):
            return semester

    fuzzy_semester = extract_period_value_near_terms(question, ["yariyil", "donem", "semester", "term"])
    if fuzzy_semester:
        return fuzzy_semester

    if any(h in q for h in [
        "ilk donem", "ilk dönem", "1. donem", "1.donem", "1. dönem", "1.dönem",
        "birinci donem", "birinci dönem",
        "first semester", "first term",
        "guz donemi dersleri", "güz dönemi dersleri",
        "1. yariyil", "1.yariyil", "1. yarıyıl", "1.yarıyıl",
    ]):
        return 1
    if any(h in q for h in [
        "ikinci donem", "ikinci dönem", "2. donem", "2.donem", "2. dönem", "2.dönem",
        "second semester",
        "bahar donemi dersleri", "bahar dönemi dersleri",
        "2. yariyil", "2.yariyil", "2. yarıyıl", "2.yarıyıl",
    ]):
        return 2
    return None


def _detect_requested_semesters(question):
    semester = _detect_requested_semester(question)
    if semester:
        return [semester]

    q = normalize_text(question)
    folded = ascii_fold(question)

    year_patterns = [
        (1, ["birinci sinif", "1. sinif", "1.sinif", "ilk sinif", "first year"]),
        (2, ["ikinci sinif", "2. sinif", "2.sinif", "second year"]),
        (3, ["ucuncu sinif", "üçüncü sınıf", "3. sinif", "3.sinif", "third year"]),
        (4, ["dorduncu sinif", "dördüncü sınıf", "4. sinif", "4.sinif", "fourth year"]),
    ]

    for year, patterns in year_patterns:
        if any(pattern in q or pattern in folded for pattern in patterns):
            start = (year - 1) * 2 + 1
            return [start, start + 1]

    return None


def _curriculum_record_priority(record):
    """Rank curriculum records so the active undergrad version wins.

    OBS Bologna keeps stale graduate / legacy faculty entries alongside the
    real one (e.g. an older 'Mühendislik Fakültesi / Tıp Mühendisliği'
    program AND a 'Fen Bilimleri Enstitüsü / ... Tezli Yüksek Lisans'
    program both show up for the Biyomedikal Mühendisliği question). We
    explicitly prefer:

      1. Records from a current undergraduate faculty (lower `Lisans` or
         `Lisans Programı` / "(İngilizce)" without graduate suffix)
      2. Records whose URL is in the high curSunit band (>= 6000), which is
         where ACU registers its currently-active programs.

    Higher score = preferred.
    """
    title = (record.title or "").lower()
    url = (record.url or "").lower()

    score = 0

    # 1. Currently-active faculty name. ACU has renamed "Mühendislik Fakültesi"
    #    (legacy) to "Mühendislik ve Doğa Bilimleri Fakültesi". Same for
    #    "Fen Edebiyat" → split into "Fen Bilimleri" and "İnsan ve Toplum
    #    Bilimleri". We boost the modern names and penalise legacy ones.
    if "ve doga bilimleri fakultesi" in ascii_fold(title):
        score += 30
    elif "muhendislik fakultesi" in ascii_fold(title) and "ve doga bilimleri" not in ascii_fold(title):
        score -= 20  # legacy

    # 2. Penalise graduate / vocational suffixes when the question is about
    #    a regular undergraduate department.
    grad_markers = (
        "yuksek lisans", "doktora", "tezsiz", "tezli",
        "meslek yuksekokulu", "(i.o.)", "ikinci ogretim",
    )
    if any(marker in ascii_fold(title) for marker in grad_markers):
        score -= 25

    # 3. High curSunit band = current active program.
    sunit_match = re.search(r"cursunit=(\d+)", url)
    if sunit_match:
        try:
            sunit_int = int(sunit_match.group(1))
            if sunit_int >= 6000:
                score += 15
        except ValueError:
            pass

    return score


def get_department_display_name_from_key(department, language="Turkish"):
    if language == "Turkish":
        return DEPARTMENT_TURKISH_TITLE_MAP.get(department, DEPARTMENT_TITLE_MAP.get(department, department))
    return DEPARTMENT_TITLE_MAP.get(department, department)


def get_curriculum_record_for_department(department):
    records = [
        record for record in KnowledgeBase.objects.all()
        if is_official_acu_source_url(record.url)
        and normalize_text(record.topic) == "curriculum"
        and record_matches_department(record, department)
    ]
    records.sort(key=_curriculum_record_priority, reverse=True)
    return records[0] if records else None


def get_required_courses_for_department(department, semesters=None):
    record = get_curriculum_record_for_department(department)
    if not record:
        return []

    if not semesters:
        return extract_required_courses_from_text(record.content)

    courses = []
    seen = set()
    for semester in semesters:
        for course in extract_required_courses_from_text(record.content, semester=semester):
            code = course.split(" ", 2)
            key = " ".join(code[:2]) if len(code) >= 2 else course
            if key in seen:
                continue
            seen.add(key)
            courses.append(course)
    return courses


def course_code(course):
    match = re.match(r"([A-Z]{2,4}\s+\d{2,4})\b", course or "")
    return match.group(1) if match else ""


def answer_common_courses_from_official_records(question):
    # Content-driven: this fires only when the question mentions at least
    # two departments. No hint-list gate.
    departments = get_department_mentions(question)[:2]
    if len(departments) < 2:
        return ""

    semesters = _detect_requested_semesters(question)
    if semesters is None and mentions_semester_scope(question):
        return "Soruda hangi yarıyıl/dönem istendiğini net anlayamadım. Lütfen örneğin \"3. yarıyıl\" veya \"üçüncü yarıyıl\" şeklinde yazın."

    first_courses = get_required_courses_for_department(departments[0], semesters=semesters)
    second_courses = get_required_courses_for_department(departments[1], semesters=semesters)
    if not first_courses or not second_courses:
        return ""

    second_by_code = {course_code(course): course for course in second_courses if course_code(course)}
    common_courses = [
        course for course in first_courses
        if course_code(course) and course_code(course) in second_by_code
    ]

    if not common_courses:
        return NOT_FOUND_MESSAGE

    language = detect_question_language(question)
    first_name = get_department_display_name_from_key(departments[0], language)
    second_name = get_department_display_name_from_key(departments[1], language)

    if semesters == [1, 2]:
        scope_tr = "birinci sınıfta"
        scope_en = "in the first year"
    elif semesters and len(semesters) == 1:
        scope_tr = f"{semesters[0]}. yarıyılda"
        scope_en = f"in semester {semesters[0]}"
    elif semesters:
        scope_tr = "istenen dönemde"
        scope_en = "in the requested period"
    else:
        scope_tr = "müfredatta"
        scope_en = "in the curriculum"

    if language == "Turkish":
        return (
            f"Resmi OBS müfredat kayıtlarına göre {first_name} ile {second_name} için {scope_tr} ortak görünen zorunlu dersler:\n"
            + "\n".join(f"- {course}." for course in common_courses)
        )

    return (
        f"According to the official OBS curriculum records, the required courses common to {first_name} and {second_name} {scope_en} are:\n"
        + "\n".join(f"- {course}." for course in common_courses)
    )


# Detect a course-code PREFIX the user is asking about. Examples that should
# match: "CSE kodlu dersler", "MAT prefix", "BME ile başlayan dersler",
# "ENG dersleri". Case-insensitive — users often type lowercase.
_COURSE_PREFIX_RE = re.compile(
    r"\b([A-Za-z]{2,4})\b\s*"
    r"(?:kodlu|kodu|kod|prefix|ile\s+ba(?:s|ş)layan|ders)",
    re.IGNORECASE,
)

# Course-code prefixes that uniquely belong to a department offered by ACU.
# The values are the canonical lower-case English department key (matches
# DEPARTMENT_TITLE_MAP). When the user asks "BME kodlu dersler nelerdir"
# without naming a department, we infer that they mean Biomedical
# Engineering. Service-course prefixes that span many departments
# (MAT, PHY, CHE, ENG, TUR, ATA, …) are deliberately NOT in this map —
# they don't map to a single owning program.
COURSE_CODE_TO_DEPARTMENT = {
    "CSE": "computer engineering",
    "BME": "biomedical engineering",
    "MBG": "molecular biology and genetics",
    "PSY": "psychology",
    "SOC": "sociology",
    "NUR": "nursing",
    "NUT": "nutrition and dietetics",
    "PTR": "physiotherapy and rehabilitation",
    "PHY": None,    # service course (used by every engineering dept)
    "MAT": None,    # service course
    "CHE": None,    # service course
    "ENG": None,    # English service course
    "TUR": None,    # Turkish service course
    "ATA": None,    # Atatürk service course
}


def infer_department_from_code(prefix):
    """Map a course-code prefix to its owning department (English canonical),
    or None if the prefix is shared / unknown.
    """
    if not prefix:
        return None
    return COURSE_CODE_TO_DEPARTMENT.get(prefix.upper())

# Common stop-words that the regex above would otherwise mistake for a
# course code (because they're 2–4 letters next to "ders").
_COURSE_PREFIX_STOPWORDS = {
    "VE", "ILE", "BU", "AT", "ON", "BIR", "TUM", "TÜM",
    "HER", "BAZI", "BAZ", "NE", "OL", "SON", "ILK", "İLK",
    "YENI", "YENİ", "ESKI", "ESKİ", "DERSI", "DERSİ", "DERS",
    "OLAN", "TUM",
}


def detect_course_code_prefix(question):
    """Return e.g. 'CSE' if the user asks for "CSE kodlu dersler".

    Case-insensitive, but always returns the prefix uppercased (course
    codes in ACU records are uppercase). When the prefix is detected the
    curriculum extractor pre-filters the course list so the LLM doesn't
    have to do its own (unreliable) filtering on a long list of items.
    """
    match = _COURSE_PREFIX_RE.search(question or "")
    if not match:
        return None
    prefix = match.group(1).upper()
    if len(prefix) < 2 or prefix in _COURSE_PREFIX_STOPWORDS:
        return None
    return prefix


def _filter_courses_by_prefix(courses, prefix):
    return [c for c in courses if c.upper().startswith(f"{prefix} ")]


def answer_curriculum_from_official_record(question):
    # NOTE: We deliberately do NOT gate on a hint list (e.g. "is this a
    # curriculum question?"). The extractor is content-driven: if the
    # question mentions a department AND the KB has a curriculum record
    # for it, we extract courses. The LLM downstream decides whether the
    # extracted evidence is relevant to the user's actual question.

    # Did the user ask for a specific course-code prefix (e.g. "CSE kodlu")?
    code_prefix = detect_course_code_prefix(question)

    # If the question doesn't name a department but does name a code prefix
    # owned by a single department (e.g. "BME kodlu dersler" implies
    # Biomedical Engineering), use that as the matching question.
    matching_question = question
    if not detect_department(question):
        inferred = infer_department_from_code(code_prefix)
        if inferred:
            display = (
                DEPARTMENT_TURKISH_TITLE_MAP.get(inferred)
                or DEPARTMENT_TITLE_MAP.get(inferred)
                or inferred
            )
            matching_question = f"{display} {question}"

    records = [
        record for record in KnowledgeBase.objects.all()
        if is_official_acu_source_url(record.url)
        and normalize_text(record.topic) == "curriculum"
        and record_matches_department(record, matching_question)
    ]

    # Sort so the most current / active program is tried first. Stale
    # legacy / graduate entries fall to the end.
    records.sort(key=_curriculum_record_priority, reverse=True)

    semester = _detect_requested_semester(question)
    if semester is None and mentions_semester_scope(question):
        return "Soruda hangi yarıyıl/dönem istendiğini net anlayamadım. Lütfen örneğin \"3. yarıyıl\" veya \"üçüncü yarıyıl\" şeklinde yazın."

    # `code_prefix` and `matching_question` were already computed above to
    # decide which records to consider. Reuse them below for course filtering
    # and the answer label.

    for record in records:
        # OBS pages have explicit "<N>.Yarıyıl Ders Planı" sections; respect
        # them when the user asks about a specific semester. Other (non-OBS)
        # curriculum pages fall back to extracting all compulsory courses.
        courses = extract_required_courses_from_text(record.content, semester=semester)
        if not courses:
            continue

        # Apply the prefix filter HERE in Python rather than relying on the
        # LLM to do it correctly. LLMs frequently truncate or miscopy long
        # filtered lists (e.g. 17 → 7 items, with one item having the wrong
        # course name). Deterministic filtering avoids both failure modes.
        if code_prefix:
            filtered = _filter_courses_by_prefix(courses, code_prefix)
            if filtered:
                courses = filtered

        # Use matching_question (which includes the inferred department name
        # if the user only gave a code prefix) so the label has the correct
        # department even when the original question was just "BME kodlu
        # dersler nelerdir".
        department_name = get_department_display_name(matching_question)
        is_turkish = detect_question_language(question) == "Turkish"

        if is_turkish:
            if code_prefix:
                base_label = f"{code_prefix} kodlu zorunlu dersleri"
            elif semester == 1:
                base_label = "ilk dönem zorunlu dersleri"
            elif semester == 2:
                base_label = "ikinci dönem zorunlu dersleri"
            elif semester:
                base_label = f"{semester}. yarıyıl zorunlu dersleri"
            else:
                base_label = "zorunlu dersleri"
            return f"{department_name} {base_label}: " + ", ".join(courses) + "."

        if code_prefix:
            base_label = f"required courses with prefix {code_prefix}"
        elif semester == 1:
            base_label = "first-semester required courses"
        elif semester == 2:
            base_label = "second-semester required courses"
        elif semester:
            base_label = f"semester-{semester} required courses"
        else:
            base_label = "required courses"
        return f"{department_name} {base_label}: " + ", ".join(courses) + "."

    return ""


def parse_options_from_content(content):
    text = (content or "").strip()

    if not text:
        return []

    cleaned = text.replace("\n", " ").strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = cleaned.rstrip(".")

    match = re.search(r"\b(?:double major|minor)\s+in\s+(.*)$", cleaned, re.IGNORECASE)
    if match:
        options_text = match.group(1).strip()
    else:
        parts = cleaned.split(":", 1)
        options_text = parts[1].strip() if len(parts) == 2 else cleaned

    raw_parts = [p.strip() for p in options_text.split(",") if p.strip()]

    options = []
    for part in raw_parts:
        part = re.sub(r"^\band\b\s+", "", part, flags=re.IGNORECASE).strip()
        part = part.strip(" .;")
        if part:
            options.append(part)

    return options


def clean_program_name(value):
    cleaned = re.sub(r"\s+", " ", (value or "").replace("\n", " ")).strip()
    cleaned = re.sub(r"\s*\([^)]*\)", "", cleaned).strip()
    return cleaned.strip(" .;:-")


def split_program_options(value):
    if not value:
        return []

    parts = []
    for line in re.split(r"[\n\r]+", value):
        line = clean_program_name(line)
        line = re.sub(r"^\*+", "", line).strip()
        if len(line) > 2:
            parts.append(line)

    return list(dict.fromkeys(parts))


def simplify_program_option(value):
    cleaned = clean_program_name(value)
    cleaned = re.sub(r"\s*\b(?:Türkçe|İngilizce|Turkish|English)\b\s*", " ", cleaned, flags=re.IGNORECASE)
    cleaned = cleaned.replace("&", " ")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def cell_matches_program(cell, question):
    target_names = []
    department = detect_department(question)

    if department:
        target_names.append(DEPARTMENT_TITLE_MAP[department])

    display_name = get_department_display_name(question)
    if display_name:
        target_names.append(display_name)

    phrase = extract_department_phrase(question)
    if phrase:
        target_names.append(phrase)

    cell_folded = ascii_fold(clean_program_name(cell))
    for slug in get_department_slug_candidates(question):
        if slug.replace("-", " ") in cell_folded:
            return True

    for name in target_names:
        name_folded = ascii_fold(clean_program_name(name))
        if name_folded and name_folded in cell_folded:
            return True

    return False


def program_names_match(value, target):
    value_folded = ascii_fold(simplify_program_option(value))
    target_folded = ascii_fold(simplify_program_option(target))
    if not value_folded or not target_folded:
        return False

    return target_folded in value_folded or value_folded in target_folded


def get_pdf_tables(url):
    if url in PDF_TABLE_CACHE:
        return PDF_TABLE_CACHE[url]

    try:
        import pdfplumber

        response = requests.get(url, timeout=30)
        response.raise_for_status()
    except Exception:
        return []

    tables = []

    try:
        from io import BytesIO

        with pdfplumber.open(BytesIO(response.content)) as pdf:
            for page in pdf.pages:
                for table in page.extract_tables() or []:
                    if table:
                        tables.append(table)
    except Exception:
        return []

    PDF_TABLE_CACHE[url] = tables
    return tables


def find_program_table_columns(table, track):
    header_index = None
    source_col = None
    target_col = None

    for row_index, row in enumerate(table[:5]):
        normalized_cells = [ascii_fold(cell or "") for cell in row]
        for col_index, cell in enumerate(normalized_cells):
            if (
                "anadal" in cell
                and ("bolum" in cell or "program" in cell)
                and "cift" not in cell
                and "yapilacak" not in cell
            ):
                source_col = col_index
            if (
                ("cift anadal" in cell and track == "double major")
                or ("yandal" in cell and track == "minor")
                or "yapilacak program" in cell
            ):
                target_col = col_index

        if source_col is not None and target_col is not None:
            header_index = row_index
            break

    return header_index, source_col, target_col


def extract_options_from_pdf_table(url, question, track):
    options = []

    try:
        for table in get_pdf_tables(url):
            header_index, source_col, target_col = find_program_table_columns(table, track)

            if header_index is None:
                continue

            current_source = ""
            for row in table[header_index + 1:]:
                if not row:
                    continue

                source = row[source_col] if source_col < len(row) else ""
                target = row[target_col] if target_col < len(row) else ""

                if source and clean_program_name(source):
                    current_source = source

                if current_source and cell_matches_program(current_source, question):
                    options.extend(split_program_options(target))
    except Exception:
        return []

    return list(dict.fromkeys(option for option in options if option))


def extract_all_target_programs_from_pdf_table(url, track):
    programs = []

    try:
        for table in get_pdf_tables(url):
            header_index, _source_col, target_col = find_program_table_columns(table, track)

            if header_index is None or target_col is None:
                continue

            for row in table[header_index + 1:]:
                if not row or target_col >= len(row):
                    continue

                target = row[target_col] or ""
                programs.extend(split_program_options(target))
    except Exception:
        return []

    return list(dict.fromkeys(simplify_program_option(program) for program in programs if program))


def extract_reverse_options_from_pdf_table(url, question, track):
    source_programs = []
    department_names = get_department_search_names(question)

    try:
        for table in get_pdf_tables(url):
            header_index, source_col, target_col = find_program_table_columns(table, track)

            if header_index is None:
                continue

            current_source = ""
            for row in table[header_index + 1:]:
                if not row:
                    continue

                source = row[source_col] if source_col < len(row) else ""
                target = row[target_col] if target_col < len(row) else ""

                if source and clean_program_name(source):
                    current_source = source

                if not current_source or not target:
                    continue

                target_parts = split_program_options(target)
                for target_part in target_parts:
                    if any(program_names_match(target_part, name) for name in department_names):
                        source_programs.append(simplify_program_option(current_source))
                        break
    except Exception:
        return []

    return list(dict.fromkeys(option for option in source_programs if option))


def should_include_reverse_program_options(question):
    q = ascii_fold(question)

    # Source-to-target wording: "Computer Engineering students can apply to
    # which departments?" The answer should contain only the target programs.
    if any(hint in q for hint in [
        "ogrencileri hangi",
        "ogrencileri ne",
        "hangi bolumlerle",
        "hangi programlarla",
        "students can",
        "major students can",
        "with which",
        "which departments can",
    ]):
        return False

    # Reverse wording: "Which majors can apply to this program?"
    return any(hint in q for hint in [
        "bu programa",
        "programina",
        "bolumune",
        "kimler",
        "hangi anadal",
        "basvurabilen anadal",
        "source majors",
        "can apply for a double major in",
        "can apply for a minor in",
    ])


def sort_program_list_records(records, question):
    folded_question = ascii_fold(question)

    def score(record):
        title_url = ascii_fold(f"{record.title} {record.url}")
        value = 0
        if "program-listesi" in title_url:
            value += 20
        if "yonerge" in title_url:
            value -= 20
        if "onlisans" in title_url:
            value += 8 if "onlisans" in folded_question else -3
        if "lisans" in title_url and "onlisans" not in title_url:
            value += 8 if "onlisans" not in folded_question else -3
        return value

    return sorted(records, key=score, reverse=True)


def answer_general_program_options_from_official_table(question, records, track):
    if detect_department(question) or extract_department_phrase(question):
        return ""

    q = ascii_fold(question)
    general_hints = [
        "hangi bolumler",
        "hangi programlar",
        "bolumler var",
        "programlar var",
        "cap yapilabilecek",
        "cift anadal yapilabilecek",
        "yandal yapilabilecek",
        "available programs",
        "available departments",
    ]
    if not any(hint in q for hint in general_hints):
        return ""

    for record in sort_program_list_records(records, question):
        programs = extract_all_target_programs_from_pdf_table(record.url, track)
        if not programs:
            continue

        if detect_question_language(question) == "Turkish":
            track_label = "ÇAP" if track == "double major" else "yandal"
            scope = "önlisans" if "onlisans" in ascii_fold(record.url) else "lisans"
            return (
                f"Resmi {scope} {track_label} program listesine göre {track_label} yapılabilecek programlar şunlardır:\n"
                + "\n".join(f"- {program}" for program in programs)
            )

        track_label = "double major" if track == "double major" else "minor"
        return (
            f"According to the official {track_label} program list, the available target programs are:\n"
            + "\n".join(f"- {program}" for program in programs)
        )

    return ""


def answer_program_options_from_official_table(question):
    # Content-driven extraction: we only need a track and a department in the
    # question. No intent hint list — if the user mentions "yandal" + a
    # department, we extract the matching options table; the LLM later
    # decides whether to include them in the final answer.
    track = detect_track(question)
    if not track:
        return ""

    records = [
        record for record in KnowledgeBase.objects.all()
        if is_official_acu_source_url(record.url)
        and record.url.lower().endswith(".pdf")
        and (
            (track == "double major" and "cift-anadal" in ascii_fold(record.url))
            or (track == "minor" and "yandal" in ascii_fold(record.url))
        )
    ]

    general_answer = answer_general_program_options_from_official_table(question, records, track)
    if general_answer:
        return general_answer

    for record in sort_program_list_records(records, question):
        options = extract_options_from_pdf_table(record.url, question, track)
        if not options:
            continue

        reverse_options = []
        if track == "double major" and should_include_reverse_program_options(question):
            reverse_options = extract_reverse_options_from_pdf_table(record.url, question, track)

        track_label_tr = "çift anadal" if track == "double major" else "yandal"
        track_label_en = "double major" if track == "double major" else "minor"
        department_name = get_department_display_name(question)

        if detect_question_language(question) == "Turkish":
            answer = f"{department_name} anadal öğrencileri {track_label_tr} için şu programlara başvurabilir: " + ", ".join(options) + "."
            if reverse_options:
                answer += f" Ayrıca {department_name} programına {track_label_tr} yapmak için başvurabilen anadal programları: " + ", ".join(reverse_options) + "."
            return answer

        answer = f"{department_name} major students can apply for a {track_label_en} in: " + ", ".join(options) + "."
        if reverse_options:
            answer += f" Also, the source majors that can apply for a {track_label_en} in {department_name} are: " + ", ".join(reverse_options) + "."
        return answer

    return ""


def find_program_regulation_record(question):
    track = detect_track(question)
    records = [
        record for record in KnowledgeBase.objects.all()
        if is_official_acu_source_url(record.url)
        and record.url.lower().endswith(".pdf")
        and (
            "yonerge" in ascii_fold(record.url)
            or "yönerge" in normalize_text(record.title)
            or "yonerge" in ascii_fold(record.title)
        )
        and (
            "cift anadal" in ascii_fold(record.content)
            or "yandal" in ascii_fold(record.content)
        )
    ]

    if track == "double major":
        records.sort(key=lambda record: ("cift anadal" not in ascii_fold(record.content), record.id))
    elif track == "minor":
        records.sort(key=lambda record: ("yandal" not in ascii_fold(record.content), record.id))

    return records[0] if records else None


def extract_regulation_section(content, start_heading, end_heading):
    normalized = re.sub(r"\s+", " ", content or "").strip()
    start_match = re.search(start_heading, normalized, flags=re.IGNORECASE)
    if not start_match:
        return ""

    end_match = re.search(end_heading, normalized[start_match.end():], flags=re.IGNORECASE)
    if not end_match:
        return normalized[start_match.start(): start_match.start() + 3500]

    return normalized[start_match.start(): start_match.end() + end_match.start()]


def clean_regulation_clause(text):
    cleaned = re.sub(r"\[Page \d+\]", " ", text or "")
    cleaned = re.sub(r"Senato\s+\d{2}\.\d{2}\.\d{4}\s*-\s*\d{4}/\d+\s*-\d+\s*\d*", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .;:-")
    cleaned = cleaned.replace(" dipl oma ", " diploma ")
    cleaned = cleaned.replace(" programın da ", " programında ")
    cleaned = cleaned.replace(" yap ılan ", " yapılan ")
    cleaned = cleaned.replace(" sırala nması ", " sıralanması ")
    return cleaned


def extract_requirement_clauses(section, track=None):
    section = clean_regulation_clause(section)
    clauses = []

    cap_patterns = [
        r"Öğrenciler,.*?başvurabilirler",
        r"ÇAP’a kabul edilecek.*?belirlenir",
        r"başvurduğu yarıyıla kadar.*?tamamlamış olması",
        r"genel not ortalamasının en az 2\.80.*?ilk %20’ye girmiş olması",
        r"genel not ortalamasının en az 2\.80 olan ancak.*?değerlendirmeye alınır",
        r"Öğrenim dili Türkçe olan.*?sağlamaları şarttır",
        r"Anadili Türkçe olmayan.*?sağlamaları şarttır",
        r"yetenek sınavından da başarılı olması şarttır",
        r"ek olarak getirilen başvuru ve kabul koşullarını sağlaması gerekir",
        r"Çift anadal programlarına başvurular.*?yaptırırlar",
        r"Çift anadal öğrenimine kabul edilen öğrencilerin.*?başlar",
    ]

    minor_patterns = [
        r"Öğrenci, yandal programına.*?başvurabilir",
        r"Öğrencinin yandal programına başvurabilmesi için.*?gerekir",
        r"Kurumlar arası yatay geçiş veya dikey.*?zorunludur",
        r"Öğrenim dili Türkçe olan lisans diploma programına.*?sağlamaları şarttır",
        r"Anadili Türkçe olmayan yabancı uyruklu öğrencilerin.*?sağlamaları şarttır",
        r"Öğrencinin yetenek sınavı.*?başarılı olması şarttır",
        r"Yandal programlarına başvurular.*?yaptırırlar",
        r"Öğrenciler, aynı anda ikinci anadal.*?öğrenim görebilirler",
        r"Bir yandal programından ayrılan öğrenci.*?kayıt yaptıramaz",
    ]

    if track == "minor":
        patterns = minor_patterns
    elif track == "double major":
        patterns = cap_patterns
    else:
        patterns = cap_patterns + minor_patterns

    for pattern in patterns:
        match = re.search(pattern, section, flags=re.IGNORECASE)
        if match:
            clause = clean_regulation_clause(match.group(0))
            if clause and clause not in clauses:
                clauses.append(clause)

    return clauses


def answer_application_requirements_from_regulation(question):
    # Content-driven: we only require that the question mentions a track
    # (double major or minor). The function returns "" if no regulation
    # record matches or no clauses were extracted; the LLM decides relevance.
    if not detect_track(question):
        return ""

    record = find_program_regulation_record(question)
    if not record:
        return ""

    track = detect_track(question)
    if track == "minor":
        section = extract_regulation_section(record.content, r"Yandal Programına Başvuru,\s*Kabul ve Kayıt Koşulları\s+Madde\s+19", r"Danışmanlık\s+Madde\s+20")
        label = "Yandal"
    else:
        section = extract_regulation_section(record.content, r"Başvuru,\s*Kabul ve Kayıt\s+Madde\s+6", r"Danışmanlık\s+Madde\s+8")
        label = "ÇAP"

    clauses = extract_requirement_clauses(section, track)
    if not clauses:
        return ""

    if detect_question_language(question) == "Turkish":
        intro = f"{label} başvuru/kabul gereklilikleri resmi yönergeye göre şunlardır:"
        return intro + "\n- " + "\n- ".join(clauses[:8]) + "."

    intro = f"According to the official regulation, {label} application/admission requirements are:"
    return intro + "\n- " + "\n- ".join(clauses[:8]) + "."


def score_option_record(record, department, track):
    title = normalize_text(record.title)
    topic = normalize_text(record.topic)
    content = normalize_text(record.content)

    score = 0

    if department in title:
        score += 20
    if department in topic:
        score += 12
    if department in content:
        score += 8

    if track in title:
        score += 10
    if track in topic:
        score += 7
    if track in content:
        score += 5

    if "option" in title or "options" in title:
        score += 5

    return score


def is_program_option_record(record, track):
    title = normalize_text(record.title)
    topic = normalize_text(record.topic)
    url = ascii_fold(record.url)
    combined = f"{title} {topic} {url}"

    if "curriculum" in topic or "mufredat" in topic:
        return False

    if "option" in combined or "options" in combined:
        return True

    if track == "double major":
        return "cift-anadal" in url and "program-listesi" in url

    if track == "minor":
        return "yandal" in url and "program-listesi" in url

    return False


def find_exact_option_record(question):
    # Content-driven: track + department detection is enough; no hint-list gate.
    track = detect_track(question)
    source_department, _ = detect_source_and_target_departments(question)

    if not track or not source_department:
        return None

    department_title = DEPARTMENT_TITLE_MAP[source_department].lower()
    track_title = TRACK_TITLE_MAP[track].lower()

    records = [
        record for record in KnowledgeBase.objects.all()
        if is_official_acu_source_url(record.url)
        and is_program_option_record(record, track)
    ]

    for record in records:
        title = normalize_text(record.title)
        if department_title in title and track_title in title and "option" in title:
            return record

    best_record = None
    best_score = 0

    for record in records:
        score = score_option_record(record, source_department, track)
        if score > best_score:
            best_score = score
            best_record = record

    return best_record


def find_definition_record(question):
    q = normalize_text(question)

    if not is_definition_question(q):
        return None

    if "minor" in q:
        candidate_topics = ["minor"]
    elif "double major" in q:
        candidate_topics = ["double_major", "double major"]
    else:
        return None

    records = KnowledgeBase.objects.all()

    for target_topic in candidate_topics:
        for record in records:
            topic = normalize_text(record.topic)
            if topic == target_topic:
                return record

    best_record = None
    best_score = 0

    for record in records:
        title = normalize_text(record.title)
        topic = normalize_text(record.topic)
        content = normalize_text(record.content)

        score = 0

        for target_topic in candidate_topics:
            if target_topic in title:
                score += 8
            if target_topic in topic:
                score += 10
            if target_topic in content:
                score += 4

        if "option" in title or "options" in title or "option" in topic or "options" in topic:
            score -= 10

        if score > best_score:
            best_score = score
            best_record = record

    return best_record


def extract_program_definition_from_regulation(question):
    track = detect_track(question)
    if track not in {"double major", "minor"}:
        return ""

    record = find_program_regulation_record(question)
    if not record or not record.content:
        return ""

    content = clean_regulation_clause(record.content)

    if track == "double major":
        pattern = (
            r"Çift\s+Anadal\s+Programı\s*\(ÇAP\)\s*:\s*"
            r"(?P<definition>.*?iki\s+ayrı\s+diploma\s+alabilmesini\s+sağlayan\s+program\s*ı)"
        )
        heading = "ÇAP (Çift Anadal Programı)"
    else:
        pattern = (
            r"Yandal\s+Programı\s*:\s*"
            r"(?P<definition>.*?programı\s+ifade\s+eder)"
        )
        heading = "Yandal Programı"

    match = re.search(pattern, content, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return ""

    definition = clean_regulation_clause(match.group("definition"))
    definition = re.sub(r"\s+program\s*ı$", " programı", definition, flags=re.IGNORECASE)
    definition = re.sub(r"\s+ifade\s+eder\.?$", "", definition, flags=re.IGNORECASE).strip(" .")
    definition = re.sub(r"\böğrenci\s+nin\b", "öğrencinin", definition, flags=re.IGNORECASE)
    definition = re.sub(r"\bya\s+ndal\b", "yandal", definition, flags=re.IGNORECASE)

    if not definition:
        return ""

    return (
        f"{heading} resmi yönergede şöyle tanımlanır:\n"
        f"- {definition}."
    )


def is_program_comparison_question(question):
    folded = ascii_fold(question)
    mentions_double_major = any(term in folded for term in ["cap", "cift anadal", "double major"])
    mentions_minor = any(term in folded for term in ["yandal", "minor"])
    asks_comparison = any(term in folded for term in [
        "fark", "farki", "arasinda", "arasindaki", "karsilastir",
        "difference", "compare", "comparison", "between",
    ])
    return mentions_double_major and mentions_minor and asks_comparison


def extract_definition_bullet(answer):
    match = re.search(r"^-\s*(.+)$", answer or "", flags=re.MULTILINE)
    if not match:
        return ""

    definition = clean_response_text(match.group(1)).strip(" .")
    definition = re.sub(r"\böğrenci\s+nin\b", "öğrencinin", definition, flags=re.IGNORECASE)
    definition = re.sub(r"\bya\s+ndal\b", "yandal", definition, flags=re.IGNORECASE)
    return definition


def answer_program_comparison_from_regulation(question):
    if not is_program_comparison_question(question):
        return ""

    double_major_definition = extract_definition_bullet(
        extract_program_definition_from_regulation("çap nedir")
    )
    minor_definition = extract_definition_bullet(
        extract_program_definition_from_regulation("yandal nedir")
    )

    if not double_major_definition or not minor_definition:
        return ""

    if detect_question_language(question) == "English":
        return (
            "According to the official regulation, the main difference is:\n"
            f"- Double Major (CAP): {double_major_definition}.\n"
            f"- Minor: {minor_definition}.\n"
            "- In short: Double major is for earning two separate diplomas; "
            "minor is for earning a minor certificate that does not replace a diploma."
        )

    return (
        "Resmi yönergeye göre ÇAP ve yandal arasındaki temel fark:\n"
        f"- ÇAP: {double_major_definition}.\n"
        f"- Yandal: {minor_definition}.\n"
        "- Kısa fark: ÇAP iki ayrı diploma almayı; yandal ise diploma yerine geçmeyen "
        "yandal sertifikası almayı sağlar."
    )


def build_structured_context_from_record(record):
    title = (record.title or "").strip()
    topic = (record.topic or "").strip()
    url = (record.url or "").strip()
    content = (record.content or "").strip()

    track = "Unknown"
    if "double major" in normalize_text(title + " " + topic + " " + content):
        track = "Double Major"
    elif "minor" in normalize_text(title + " " + topic + " " + content):
        track = "Minor"

    program_name = title
    for suffix in [
        " Double Major Option",
        " Double Major Options",
        " Minor Option",
        " Minor Options",
    ]:
        if program_name.endswith(suffix):
            program_name = program_name[:-len(suffix)].strip()

    options = parse_options_from_content(content)

    lines = [
        f"Record Title: {title}",
        f"Topic: {topic}",
        f"Source URL: {url}",
        f"Program: {program_name}",
        f"Track: {track}",
        "Allowed Options:",
    ]

    if options:
        for item in options:
            lines.append(f"- {item}")
    else:
        lines.append(f"- {content}")

    lines.append("")
    lines.append(f"Original Record Content: {content}")

    return "\n".join(lines), options


def score_general_record(record, keywords):
    title_text = normalize_text(record.title)
    topic_text = normalize_text(record.topic)
    content_text = normalize_text(record.content)

    score = 0

    for kw in keywords:
        is_phrase = " " in kw

        if kw in title_text:
            score += 8 if is_phrase else 4
        if kw in topic_text:
            score += 6 if is_phrase else 3
        if kw in content_text:
            score += 5 if is_phrase else 2

    return score


def get_general_context(question, limit=3):
    keywords = extract_keywords(question)

    if not keywords:
        return ""

    scored_records = []
    for record in KnowledgeBase.objects.all():
        score = score_general_record(record, keywords)
        if score > 0:
            scored_records.append((score, record))

    if not scored_records:
        return ""

    scored_records.sort(key=lambda x: x[0], reverse=True)
    top_records = [record for _, record in scored_records[:limit]]

    context_parts = []
    for record in top_records:
        context_parts.append(
            f"Source: {record.title}\n"
            f"Topic: {record.topic}\n"
            f"URL: {record.url}\n"
            f"{(record.content or '')[:2500]}"
        )

    return "\n\n".join(context_parts)


def build_general_prompt(question, context):
    """Generic prompt for questions that are NOT about double major / minor option lists.

    Used for things like "Who is the head of department?", "What are the
    compulsory courses?", general ACU info, etc.
    """
    return f"""You are the Acibadem University Academic Assistant.

RULES:
- Answer using ONLY the TEXT provided below.
- {build_language_rule(question).splitlines()[0][2:]}
- {build_language_rule(question).splitlines()[1][2:]}
- {build_language_rule(question).splitlines()[2][2:]}
- Do NOT invent information that is not explicitly written in the TEXT.
- Be concise. Answer in 1-3 sentences.
- Do NOT start with phrases like "According to the text" or "Based on the text".
- If the TEXT lists multiple items relevant to the QUESTION (e.g. courses, names),
  include all of the relevant items.
- If the answer is not found in the TEXT, say exactly: "{NOT_FOUND_MESSAGE}"

TEXT:
{context}

QUESTION:
{question}

ANSWER:"""


def build_faculty_prompt(question, context):
    """Prompt for 'who is the head of X department' type questions."""
    return f"""You are the Acibadem University Academic Assistant.

RULES:
- Answer using ONLY the TEXT provided below.
- {build_language_rule(question).splitlines()[0][2:]}
- {build_language_rule(question).splitlines()[1][2:]}
- {build_language_rule(question).splitlines()[2][2:]}
- Identify the person and give their title and full name (e.g. "Prof. Dr. ...").
- Keep the answer to one sentence.
- Do NOT list other faculty members unless asked.
- Do NOT start with phrases like "According to the text" or "Based on the text".
- If the answer is not found in the TEXT, say exactly: "{NOT_FOUND_MESSAGE}"

TEXT:
{context}

QUESTION:
{question}

ANSWER:"""


def build_curriculum_prompt(question, context):
    """Prompt for 'what are the compulsory/elective courses' type questions."""
    return f"""You are the Acibadem University Academic Assistant.

RULES:
- Answer using ONLY the TEXT provided below.
- {build_language_rule(question).splitlines()[0][2:]}
- {build_language_rule(question).splitlines()[1][2:]}
- {build_language_rule(question).splitlines()[2][2:]}
- List the course names from the TEXT, separated by commas or as a bullet list.
- Include EVERY course that the TEXT marks as relevant to the QUESTION.
- Do NOT invent course names or codes that are not in the TEXT.
- Do NOT start with phrases like "According to the text" or "Based on the text".
- If the answer is not found in the TEXT, say exactly: "{NOT_FOUND_MESSAGE}"

TEXT:
{context}

QUESTION:
{question}

ANSWER:"""


def build_prompt(question, context):
    return f"""You are the Acibadem University Academic Assistant.

RULES:
- Answer using ONLY the TEXT provided below.
- {build_language_rule(question).splitlines()[0][2:]}
- {build_language_rule(question).splitlines()[1][2:]}
- {build_language_rule(question).splitlines()[2][2:]}
- Do NOT add information that is not explicitly written in the TEXT.
- If the TEXT contains an "Allowed Options" list, include EVERY option from that list.
- Do NOT omit any option.
- Do NOT summarize or shorten the list.
- If the list contains 5 options, your answer must contain 5 options.
- If the answer is not found in the TEXT, say exactly: "{NOT_FOUND_MESSAGE}"

TEXT:
{context}

QUESTION:
{question}

ANSWER:"""


def build_revision_prompt(question, context, missing_options):
    missing_lines = "\n".join(f"- {item}" for item in missing_options)

    return f"""You are the Acibadem University Academic Assistant.

You previously omitted some options.

RULES:
- Answer using ONLY the TEXT provided below.
- {build_language_rule(question).splitlines()[0][2:]}
- {build_language_rule(question).splitlines()[1][2:]}
- {build_language_rule(question).splitlines()[2][2:]}
- Include EVERY option from the "Allowed Options" list exactly once.
- Do NOT omit any option.
- Do NOT summarize or shorten the list.
- The following missing options MUST appear in your answer:
{missing_lines}
- If the answer is not found in the TEXT, say exactly: "{NOT_FOUND_MESSAGE}"

TEXT:
{context}

QUESTION:
{question}

REWRITE THE ANSWER:"""


def build_yes_no_prompt(question, context, target_department):
    target_title = DEPARTMENT_TITLE_MAP.get(target_department, target_department.title())

    return f"""You are the Acibadem University Academic Assistant.

RULES:
- Answer using ONLY the TEXT provided below.
- {build_language_rule(question).splitlines()[0][2:]}
- {build_language_rule(question).splitlines()[1][2:]}
- {build_language_rule(question).splitlines()[2][2:]}
- Start your answer with only one word: Yes or No.
- Then write one short sentence.
- Mention only the requested target program: {target_title}.
- Do NOT list all available options.
- Do NOT start with phrases like "According to the text" or "Based on the text".
- If the answer is not found in the TEXT, say exactly: "{NOT_FOUND_MESSAGE}"

TEXT:
{context}

QUESTION:
{question}

ANSWER:"""


def build_definition_prompt(question, context):
    return f"""You are the Acibadem University Academic Assistant.

RULES:
- Answer using ONLY the TEXT provided below.
- {build_language_rule(question).splitlines()[0][2:]}
- {build_language_rule(question).splitlines()[1][2:]}
- {build_language_rule(question).splitlines()[2][2:]}
- Do NOT list department options.
- Do NOT list application options.
- Do NOT add examples unless explicitly asked.
- Do NOT start with phrases like "According to the text" or "Based on the text".
- If the answer is not found in the TEXT, say exactly: "{NOT_FOUND_MESSAGE}"

TEXT:
{context}

QUESTION:
{question}

ANSWER:"""


def compact_evidence_for_generation(evidence, max_chars=3200):
    """Compact evidence without destroying factual lists.

    The previous version cut each line at ~240 chars and often removed the tail
    of course/option lists. This version keeps bullets/table-like rows intact
    and only trims very long prose lines.
    """
    lines = []
    current_size = 0

    for line in (evidence or "").splitlines():
        cleaned = re.sub(r"\s+", " ", line).strip()
        if not cleaned:
            continue

        # Keep bullet/list rows longer than normal prose, because they often
        # contain course codes, program options, or official requirements.
        line_limit = 520 if cleaned.startswith(("-", "•")) or re.search(r"\b[A-Z]{2,4}\s+\d{2,4}\b", cleaned) else 360
        if len(cleaned) > line_limit:
            cleaned = cleaned[: line_limit - 3].rstrip(" ,;") + "..."

        projected = current_size + len(cleaned) + 1
        if projected > max_chars:
            break

        lines.append(cleaned)
        current_size = projected

    return "\n".join(lines).strip()


def build_grounded_generation_prompt(question, evidence):
    language_lines = build_language_rule(question).splitlines()
    return f"""You are the Acıbadem University Academic Assistant.

TASK
Answer the user's QUESTION using only the official ACU / OBS / PDF information inside EVIDENCE.

STRICT GROUNDING RULES
- Use ONLY facts that are explicitly present in EVIDENCE.
- Do NOT use general knowledge, assumptions, memory, or guesses.
- If EVIDENCE does not contain the requested fact, say exactly: "{NOT_FOUND_MESSAGE}"
- If EVIDENCE contains partial information, answer only that partial information and do not invent the rest.
- If several evidence blocks are present, choose the block that directly matches the QUESTION and ignore unrelated blocks.
- If the QUESTION asks for a subset, filter the list and return only matching items.

WRITING RULES
- {language_lines[0][2:]}
- {language_lines[1][2:]}
- {language_lines[2][2:]}
- Answer the QUESTION only. Do not add extra faculty descriptions, chair names, URLs, or background unless asked.
- Preserve official course codes, names, program names, people names, numbers, dates, ECTS values, and percentages exactly.
- Use short bullets for lists.
- Do not copy long raw paragraphs; rewrite clearly while preserving official facts.
- Do NOT mention the words "evidence", "context", "retrieved", "semantic search", "block", or "RAG" in the final answer.
- Do NOT include separators like "---".

EVIDENCE:
{evidence}

QUESTION:
{question}

FINAL ANSWER:"""


def clean_response_text(answer):
    cleaned = answer or ""
    replacements = {
        "Ã‡": "Ç",
        "Ã§": "ç",
        "Ã–": "Ö",
        "Ã¶": "ö",
        "Ãœ": "Ü",
        "Ã¼": "ü",
        "ÄŸ": "ğ",
        "Ä±": "ı",
        "Ä°": "İ",
        "ÅŸ": "ş",
        "Å": "Ş",
        "AyrÄ±ca": "Ayrıca",
        " diplo ma ": " diploma ",
        " dipl oma ": " diploma ",
        " programın da ": " programında ",
        " ge çiş ": " geçiş ",
        " öğ rencilerin ": " öğrencilerin ",
        " den ": "den ",
        " yap ılan ": " yapılan ",
        " sırala nması ": " sıralanması ",
        " yürütüldü ğü ": " yürütüldüğü ",
        " er, ": "ler, ",
    }
    for source, target in replacements.items():
        cleaned = cleaned.replace(source, target)

    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = re.sub(r"(?m)^\s*[•*]\s*", "- ", cleaned)
    cleaned = re.sub(r"(?m)^\s*-\s*$\n?", "", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def shorten_requirement_clause(clause):
    clause = clean_response_text(clause).strip(" .")
    clause = re.sub(r"^\*+\s*", "", clause)
    clause = re.sub(r"^(?:Öğrencinin|Öğrenciler,|Öğrenci,)\s+", "", clause)
    clause = clause[:1].upper() + clause[1:] if clause else clause
    return clause


def format_program_options_answer(answer):
    cleaned = clean_response_text(answer)
    if "anadal öğrencileri" not in cleaned or "başvurabilen anadal programları" not in cleaned:
        return cleaned

    first_part, second_part = cleaned.split("Ayrıca", 1)
    first_options = first_part.split(":", 1)[1].strip(" .") if ":" in first_part else ""
    second_options = second_part.split(":", 1)[1].strip(" .") if ":" in second_part else ""
    department_match = re.match(r"(.+?) anadal öğrencileri", first_part)
    department_name = department_match.group(1).strip() if department_match else "Bu program"

    lines = [
        f"{department_name} için ÇAP seçenekleri resmi program listesine göre şöyle:",
    ]
    if first_options:
        lines.append(f"- Anadal öğrencileri için başvurulabilecek programlar: {first_options}.")
    if second_options:
        lines.append(f"- Bu programa ÇAP yapmak için başvurabilen anadal programları: {second_options}.")

    return "\n".join(lines)


def format_requirement_answer(answer):
    cleaned = clean_response_text(answer)
    if "başvuru/kabul gereklilikleri" not in cleaned or "\n- " not in cleaned:
        return cleaned

    intro, raw_items = cleaned.split("\n-", 1)
    items = [item.strip(" -.") for item in raw_items.split("\n-") if item.strip()]
    formatted_items = [shorten_requirement_clause(item) for item in items[:8]]

    return intro.strip() + "\n" + "\n".join(f"- {item}." for item in formatted_items if item)


def format_curriculum_answer(answer):
    cleaned = clean_response_text(answer)
    marker = "zorunlu dersleri:"
    if marker not in cleaned.lower():
        return cleaned

    heading, courses_text = cleaned.split(":", 1)
    courses = [
        course.strip(" .")
        for course in courses_text.split(",")
        if course.strip(" .")
    ]

    if not courses:
        return cleaned

    return heading.strip() + ":\n" + "\n".join(f"- {course}." for course in courses)


def format_general_answer(answer):
    cleaned = clean_response_text(answer)

    if "\n-" in cleaned or cleaned.startswith("- "):
        return cleaned

    if len(cleaned) < 40:
        return cleaned

    role_markers = [
        " head of ",
        " department is ",
        " dekanı ",
        " dekani ",
        " dekan yardımcısı ",
        " dekan yardimcisi ",
        " fakülte sekreteri ",
        " fakulte sekreteri ",
        " dean ",
        " vice dean ",
        " faculty secretary ",
    ]
    if any(marker in ascii_fold(f" {cleaned} ") for marker in [ascii_fold(item) for item in role_markers]):
        return "- " + cleaned.strip(" .") + "."

    if (
        " bölüm başkanı " in cleaned.lower()
        or "department head" in cleaned.lower()
        or "head of the" in cleaned.lower()
    ):
        return "- " + cleaned.strip(" .") + "."

    sentences = split_answer_sentences(cleaned)
    if not sentences:
        return cleaned

    if len(sentences) == 1:
        return "- " + sentences[0].strip(" .") + "."

    heading = sentences[0].strip(" .")
    bullets = [sentence.strip(" .") for sentence in sentences[1:4] if sentence.strip()]

    if not bullets:
        return "Özet:\n- " + heading + "."

    return heading + ":\n" + "\n".join(f"- {bullet}." for bullet in bullets)


def evidence_content_tokens(text):
    folded = ascii_fold(text)
    tokens = re.findall(r"[a-z0-9çğıöşü]+", folded)
    ignored = {
        "yanit", "cevap", "ozet", "resmi", "gore", "icin", "olan", "olarak",
        "hakkinda", "bilgi", "program", "programi", "programlari", "basvuru",
        "basvurabilir", "ogrenci", "ogrencileri", "universitesi", "acibadem",
        "maddeler", "sunlardir", "sekilde", "asagidaki", "ilgili",
    }
    return [
        token for token in tokens
        if len(token) > 4 and token not in ignored and token not in STOP_WORDS
    ]


def answer_is_grounded(answer, evidence):
    evidence_folded = ascii_fold(evidence)
    answer_tokens = evidence_content_tokens(answer)

    if not answer_tokens:
        return True

    unsupported = [
        token for token in answer_tokens
        if token not in evidence_folded
        and not any(variant in evidence_folded for variant in keyword_variants(token))
    ]

    return (len(unsupported) / max(len(answer_tokens), 1)) <= 0.25


def extract_course_codes(text):
    return set(re.findall(r"\b[A-Z]{2,4}\s+\d{2,4}\b", text or ""))


def answer_preserves_course_codes(answer, evidence):
    evidence_codes = extract_course_codes(evidence)
    if not evidence_codes:
        return True

    answer_codes = extract_course_codes(answer)
    return evidence_codes.issubset(answer_codes)


def extract_titled_person_names(text):
    pattern = (
        r"(?:Prof\.|Doç\.|Dr\. Öğr\. Üyesi|Dr\.|Öğr\. Gör\.)\s*"
        r"(?:Dr\.\s*)?"
        r"[A-ZÇĞİÖŞÜ][A-Za-zÇĞİÖŞÜçğıöşü]+"
        r"(?:\s+[A-ZÇĞİÖŞÜ][A-Za-zÇĞİÖŞÜçğıöşü]+){1,4}"
    )
    return [clean_person_name(match.group(0)) for match in re.finditer(pattern, text or "")]


def answer_preserves_person_names(answer, evidence):
    names = extract_titled_person_names(evidence)
    if not names:
        return True

    folded_answer = ascii_fold(answer)
    for name in names:
        name_tokens = [
            token for token in re.findall(r"[a-z]+", ascii_fold(name))
            if token not in {"prof", "doc", "dr", "ogr", "uyesi", "gor"}
        ]
        if name_tokens and all(token in folded_answer for token in name_tokens):
            return True

    return False


def extract_bullet_items(text):
    return [
        line.strip(" -.•*")
        for line in (text or "").splitlines()
        if line.strip().startswith("- ")
    ]


def answer_preserves_bullet_items(answer, evidence):
    items = extract_bullet_items(evidence)
    if not items:
        return True

    if "\n-" not in (answer or ""):
        return False

    folded_answer = ascii_fold(answer)
    for item in items:
        tokens = [
            token for token in re.findall(r"[a-z0-9]+", ascii_fold(item))
            if len(token) > 3 and token not in STOP_WORDS
        ]
        if tokens and not all(token in folded_answer for token in tokens):
            return False

    return True


def format_answer_for_display(question, answer):
    if not answer or answer.strip() == NOT_FOUND_MESSAGE:
        return answer

    formatted = clean_response_text(answer)
    formatted = format_program_options_answer(formatted)
    formatted = format_requirement_answer(formatted)
    formatted = format_curriculum_answer(formatted)
    formatted = format_general_answer(formatted)

    return formatted


def generate_grounded_answer(question, evidence, fallback_answer):
    if not evidence:
        return format_answer_for_display(question, fallback_answer)

    compact_evidence = compact_evidence_for_generation(evidence)

    try:
        answer = call_llm(
            build_grounded_generation_prompt(question, compact_evidence),
            timeout=60,
            num_predict=260,
        )
        answer = enforce_answer_language(question, answer)
    except Exception:
        return format_answer_for_display(question, fallback_answer)

    if not answer or answer.strip() == NOT_FOUND_MESSAGE:
        return format_answer_for_display(question, fallback_answer)

    if "bölüm başkanı" in fallback_answer.lower():
        fallback_name = fallback_answer.split("bölüm başkanı", 1)[-1].strip(" .")
        if fallback_name and fallback_name not in answer:
            return format_answer_for_display(question, fallback_answer)

    if "Ayrıca" in fallback_answer and "Ayrıca" not in answer:
        return format_answer_for_display(question, fallback_answer)

    if "Also" in fallback_answer and "Also" not in answer:
        return format_answer_for_display(question, fallback_answer)

    evidence_numbers = set(re.findall(r"\d+(?:[.,]\d+)?%?", evidence))
    answer_numbers = set(re.findall(r"\d+(?:[.,]\d+)?%?", answer))
    if any(number not in evidence_numbers for number in answer_numbers):
        return format_answer_for_display(question, fallback_answer)

    if not answer_is_grounded(answer, evidence):
        return format_answer_for_display(question, fallback_answer)

    if not answer_preserves_course_codes(answer, evidence):
        return format_answer_for_display(question, fallback_answer)

    if not answer_preserves_person_names(answer, evidence):
        return format_answer_for_display(question, fallback_answer)

    if not answer_preserves_bullet_items(answer, evidence):
        return format_answer_for_display(question, fallback_answer)

    evidence_sentences = split_answer_sentences(evidence)
    answer_sentences = split_answer_sentences(answer)
    if evidence_sentences and len(evidence_sentences) <= 3:
        if len(answer_sentences) > len(evidence_sentences) + 1:
            return format_answer_for_display(question, fallback_answer)
        if len(answer) > max(len(fallback_answer) * 2, len(fallback_answer) + 240):
            return format_answer_for_display(question, fallback_answer)

    return format_answer_for_display(question, answer)


def is_degenerate_output(answer):
    """Detect runaway / repetitive LLM output.

    Llama can sometimes get stuck repeating the same phrase 20+ times when
    the context contains a lot of duplicated tokens (e.g. a department name
    that appears in every page header). When that happens we want to throw
    the answer away and fall back to the not-found message rather than
    surface garbage to the user.
    """
    if not answer:
        return False

    # Tokenize on whitespace; we only care about word-level repetition.
    words = answer.split()
    if len(words) < 12:
        return False

    # 1) Same single token repeats more than half the answer.
    counts = {}
    for word in words:
        key = word.lower().strip(",.;:!?")
        if key:
            counts[key] = counts.get(key, 0) + 1
    if counts:
        most_common = max(counts.values())
        if most_common / len(words) > 0.5:
            return True

    # 2) Any 2-word phrase repeats more than 4 times in a row.
    for window in (2, 3, 4):
        if len(words) < window * 5:
            continue
        runs = 1
        prev = tuple(w.lower() for w in words[:window])
        for i in range(window, len(words) - window + 1, window):
            current = tuple(w.lower() for w in words[i:i + window])
            if current == prev and len(prev) == window:
                runs += 1
                if runs >= 4:
                    return True
            else:
                runs = 1
            prev = current

    # 3) Truncated mid-word ("...Bil." style) is also unusable.
    if answer.rstrip().endswith(("Bil.", "Bil", "Müh.", "Muh.")):
        # Heuristic: short trailing fragment after a long repetition usually
        # means num_predict cut off a stuck loop.
        last_word = words[-1].lower().strip(",.;:!?")
        if last_word and last_word in counts and counts[last_word] / len(words) > 0.3:
            return True

    return False


def call_llm(prompt, timeout=90, num_predict=220):
    """Call the local Ollama API with conservative generation settings.

    The chatbot is evaluated on factuality, not creativity. Low temperature,
    repeat penalties and stop strings reduce hallucination and runaway output.
    """
    ollama_url = getattr(settings, "OLLAMA_URL", "http://ollama:11434").rstrip("/")
    model_name = getattr(settings, "OLLAMA_CHAT_MODEL", "qwen2.5:3b")

    response = requests.post(
        f"{ollama_url}/api/generate",
        json={
            "model": model_name,
            "prompt": prompt,
            "stream": False,
            "keep_alive": "15m",
            "options": {
                "temperature": 0.0,
                "top_p": 0.8,
                "top_k": 20,
                "num_predict": num_predict,
                "num_ctx": 4096,
                "repeat_penalty": 1.35,
                "repeat_last_n": 160,
                "stop": [
                    "\nQUESTION:",
                    "\nEVIDENCE:",
                    "\nCONTEXT:",
                    "\nUSER:",
                    "\nASSISTANT:",
                ],
            }
        },
        timeout=timeout
    )

    if response.status_code == 404:
        raise requests.exceptions.HTTPError(
            f"Ollama endpoint/model not found. Check OLLAMA_URL={ollama_url} and model={model_name}.",
            response=response,
        )

    response.raise_for_status()

    answer = response.json().get("response", "").strip()

    answer = re.sub(
        r'^(according to the text|based on the text|according to the context|based on the context)\s*,?\s*',
        '',
        answer,
        flags=re.IGNORECASE
    ).strip()

    answer = answer.replace("FINAL ANSWER:", "").strip()

    if is_degenerate_output(answer):
        return ""

    return answer.strip()


def enforce_answer_language(question, answer):
    if not answer:
        return answer

    question_language = detect_question_language(question)
    answer_language = detect_answer_language(answer)

    if question_language == answer_language:
        return answer

    rewritten = call_llm(build_language_rewrite_prompt(question, answer))
    return rewritten or answer


def option_present_in_answer(option, answer):
    option_norm = normalize_text(option)
    answer_norm = normalize_text(answer)
    return option_norm in answer_norm


# Question-type → preferred evidence-kind mapping for the authoritative fast
# path. Every extractor in collect_structured_evidence already produces a
# clean, ready-to-display Turkish/English sentence. When a question maps
# unambiguously to one of these extractors, we show its output directly
# to preserve exact course lists, names, numbers, and program options.
#
# Each rule is (keyword_hints, evidence_kind_prefix). A question matches a
# rule when ANY of the keywords appears in the ascii-folded question text.
# Rules are evaluated in order; the first match wins.
_BYPASS_RULES = (
    # 1) Person inquiries — "who is the chair / dean?"
    (
        ("kim", "kimdir", "who is", "who's", "who leads",
         "head of", "department head", "department chair", "chair of",
         "başkan", "baskan", "dekan", "dean", "rector", "rektor", "rektör"),
        ("Department head", "Academic role holder"),
    ),
    # 2) Program options — "which departments can X students do double major / minor with?"
    (
        ("cap yapabilir", "çap yapabilir", "yandal yapabilir",
         "cap yapilabilecek", "çap yapılabilecek",
         "cift anadal yapilabilecek", "çift anadal yapılabilecek",
         "yandal yapilabilecek", "yandal yapılabilecek",
         "hangi bolumler var", "hangi bölümler var",
         "hangi programlar var",
         "double major", "minor program", "minor with",
         "hangi programlarla", "hangi bolumlerle", "hangi bölümlerle",
         "hangi programlara", "hangi bölümlere", "hangi bolumlere",
         "with which", "available for double major", "available for minor",
         "basvurabilir", "başvurabilir", "apply for"),
        ("Program options table", "Direct option-record match"),
    ),
    # 3) Application requirements — "what are the prerequisites?"
    (
        ("gereklilik", "gereklikleri", "şart", "sart", "şartları", "sartlari",
         "koşul", "kosul", "koşulları", "kosullari", "başvuru", "basvuru",
         "kabul kosulu", "kabul koşulu", "requirement", "criteria",
         "conditions", "prerequisite"),
        ("Application requirements",),
    ),
    # 4) Faculty → list of departments — "what departments are in X faculty?"
    (
        ("hangi bolumler", "hangi bölümler", "what departments",
         "kaç bölüm", "kac bolum", "kaç tane bölüm", "kac tane bolum",
         "kaç bölüm var", "kac bolum var", "bölüm var", "bolum var",
         "how many departments", "number of departments",
         "fakultesinde hangi", "fakültesinde hangi",
         "departments in", "fakultenin bolumleri", "fakültenin bölümleri"),
        ("Faculty department list",),
    ),
    # 5) Common courses — "what courses do A and B share?"
    (
        ("ortak", "ortak ders", "ortak dersler", "dersler ortak",
         "dersleri ortak", "ortak olan ders", "common", "common course",
         "common courses", "ikisinde de", "her iki", "shared course",
         "shared courses"),
        ("Common courses",),
    ),
    # 6) Curriculum lookups — only fire when question clearly asks about
    #    courses (prefix, semester, or generic). Lists are deterministically
    #    filtered in Python before the bypass returns them, so the LLM is
    #    never asked to truncate them.
    (
        ("kodlu", "ile başlayan", "ile baslayan",
         "ilk donem", "ilk dönem", "ikinci donem", "ikinci dönem",
         "1. donem", "1.donem", "1. dönem", "1.dönem",
         "2. donem", "2.donem", "2. dönem", "2.dönem",
         "yariyil", "yarıyıl", "first semester", "second semester",
         "müfredat", "mufredat", "ders planı", "ders plani",
         "ders listesi", "course list", "compulsory course",
         "zorunlu ders", "required course",
         "hangi dersler", "dersleri nelerdir", "course code"),
        ("Department curriculum",),
    ),
    # 7) Definition — "what is double major / minor?"
    (
        ("nedir", "ne demek", "what is", "tanım", "tanim", "define"),
        ("Program definition",),
    ),
    # 7b) Definition comparison — "what is the difference between CAP and minor?"
    (
        ("fark", "farki", "arasinda", "arasindaki", "difference", "compare", "comparison"),
        ("Program comparison",),
    ),
    # 8) Library / campus information — broad official-source questions.
    (
        ("kütüphane", "kutuphane", "library", "e-kaynak", "elektronik kaynak"),
        ("Library information",),
    ),
)


def _pick_authoritative_answer(question, evidence):
    """Return a single ready-to-display answer if exactly one evidence kind
    cleanly matches the question shape; otherwise return "".

    The structured extractors in collect_structured_evidence produce
    well-formed answers for exact factual lookups. This path shows the
    extractor's output when the question shape is unambiguous, which keeps
    course lists, person names, numbers, and program names intact.

    When no rule matches, the api_chat path falls through to the regular
    LLM flow with all evidence as context — that is the only path that
    needs LLM judgement (e.g. open-ended general questions).
    """
    if not evidence:
        return ""

    folded = ascii_fold((question or "").lower())

    for hints, allowed_kinds in _BYPASS_RULES:
        if not any(hint in folded for hint in hints):
            continue

        for block in evidence:
            if not isinstance(block, dict):
                continue
            kind = block.get("kind", "")
            body = (block.get("body") or "").strip()
            if not body:
                continue
            if any(kind.startswith(prefix) for prefix in allowed_kinds):
                return body

    return ""


# Backwards-compatibility alias for the older single-purpose helper.
def _person_answer_if_question_asks_for_one(question, evidence):
    return _pick_authoritative_answer(question, evidence)


def is_library_question(question):
    folded = ascii_fold(question)
    return any(term in folded for term in [
        "kutuphane",
        "library",
        "e kaynak",
        "elektronik kaynak",
    ])


def answer_library_question_from_official_records(question):
    if not is_library_question(question):
        return ""

    records = [
        record for record in KnowledgeBase.objects.all()
        if is_official_acu_source_url(record.url)
        and (
            "kutuphane" in ascii_fold(record.title)
            or "kutuphane" in ascii_fold(record.url)
            or "library.acibadem.edu.tr" in (record.url or "").lower()
        )
    ]
    if not records:
        return ""

    def priority(record):
        title = ascii_fold(record.title)
        url = (record.url or "").lower()
        score = 0
        if "kutuphane ve elektronik kaynaklar" in title:
            score += 20
        if "library.acibadem.edu.tr" in url:
            score += 12
        if "kampus-olanaklari" in url:
            score += 8
        return score

    record = max(records, key=priority)
    def clean_library_fact(sentence):
        cleaned = re.sub(r"\s+", " ", sentence or "").strip(" .")
        if not cleaned:
            return ""
        if "Ana içeriğe atla" in cleaned:
            marker = "Kerem Aydınlar Kampüsü"
            if marker in cleaned:
                cleaned = cleaned[cleaned.index(marker):].strip()
            else:
                return ""
        if len(cleaned) > 420:
            for marker in [
                "Merkez Kütüphane",
                "Okuyucularına",
                "Kütüphanede",
                "Basılı Koleksiyon",
                "Elektronik Koleksiyon",
            ]:
                if marker in cleaned:
                    cleaned = cleaned[cleaned.index(marker):].strip()
                    break
        return cleaned[:520].strip(" .")

    sentences = split_answer_sentences(record.content)
    useful = []
    for sentence in sentences:
        folded = ascii_fold(sentence)
        if any(term in folded for term in [
            "kutuphane",
            "koleksiyon",
            "elektronik",
            "veritabani",
            "e kitap",
            "calisma",
            "7/24",
            "oturma",
            "materyal",
            "kaynak",
            "rezervasyon",
            "iletisim",
        ]):
            fact = clean_library_fact(sentence)
            if fact:
                useful.append(fact)
        if len(useful) >= 7:
            break

    if not useful:
        useful = [record.content[:1200].strip()]

    if detect_question_language(question) == "Turkish":
        return (
            "ACU kütüphanesi hakkında resmi kaynaklardan çıkarılan bilgiler:\n- "
            + "\n- ".join(useful)
            + "."
        )

    return (
        "Information about the ACU library from official sources:\n- "
        + "\n- ".join(useful)
        + "."
    )


def collect_structured_evidence(question):
    """Run every high-precision extractor and collect non-empty results.

    Each extractor below is content-driven: it returns a non-empty string
    only when the knowledge base contains a record that legitimately answers
    the question (a curriculum page for the named department, a regulation
    PDF for the named track, etc.). Extractors that don't apply silently
    return "".

    Why this lives in one place
    ---------------------------
    The previous architecture sprinkled intent-classifier checks throughout
    api_chat: "is this a curriculum question?" → run curriculum extractor.
    That broke for any phrasing the hint list didn't anticipate
    (e.g. "CSE kodlu dersler nelerdir"). The new approach runs every
    extractor unconditionally and lets the LLM (which sees the resulting
    evidence + the original question) decide what is relevant.

    Returns
    -------
    list[str]: zero or more strings, each one a self-contained piece of
    high-precision evidence (a course list, a person's name, a list of
    regulation clauses, etc.). The grounded LLM is given them all.
    """
    evidence = []

    def _add(kind, body):
        """Append a labelled evidence block. The label is what the LLM sees so
        it can reason about whether this block is actually relevant to the
        user's question.
        """
        if body:
            evidence.append({"kind": kind, "body": body.strip()})

    # 1. Curriculum: course list from an OBS / department curriculum page
    _add(
        "Department curriculum (compulsory course list)",
        answer_curriculum_from_official_record(question),
    )

    # 2. Definition: "what is double major / minor"
    _add(
        "Program definition (official regulation definition clause)",
        extract_program_definition_from_regulation(question),
    )
    _add(
        "Program comparison (official regulation definition clauses)",
        answer_program_comparison_from_regulation(question),
    )

    def_record = find_definition_record(question)
    if def_record and def_record.content:
        snippet = def_record.content[:1500].strip()
        _add(
            "Program definition (e.g. what is double major / minor)",
            f"{def_record.title}\n\n{snippet}",
        )

    # 3. Department head / faculty member
    _add(
        "Department head (chair of the named department)",
        answer_faculty_question_directly(question),
    )

    # 4. Faculty → list of departments (if applicable)
    try:
        dept_list = answer_faculty_departments_question_directly(question)
    except Exception:
        dept_list = ""
    _add(
        "Faculty department list (departments inside a faculty)",
        dept_list,
    )

    # 5. Academic role question (rector, dean, vice-dean, etc.)
    try:
        role_answer = answer_academic_role_question_directly(question)
    except Exception:
        role_answer = ""
    _add(
        "Academic role holder (dean / vice-dean / faculty secretary)",
        role_answer,
    )

    # 6. Common courses across departments
    try:
        common_courses_answer = answer_common_courses_from_official_records(question)
    except Exception:
        common_courses_answer = ""
    _add(
        "Common courses (courses shared by two named departments)",
        common_courses_answer,
    )

    # 7. Program options table (PDF) for double major / minor
    _add(
        "Program options table (double major / minor target departments)",
        answer_program_options_from_official_table(question),
    )

    # 8. Application requirements from the official regulation PDF
    _add(
        "Application requirements (official regulation clauses)",
        answer_application_requirements_from_regulation(question),
    )

    # 9. Library / electronic resources from official ACU library pages
    _add(
        "Library information (official ACU library pages)",
        answer_library_question_from_official_records(question),
    )

    # 10. Direct option-record match (e.g. "Computer Engineering Double Major Options")
    option_record = find_exact_option_record(question)
    if option_record:
        structured_context, _options = build_structured_context_from_record(option_record)
        _add(
            "Direct option-record match (department-specific options page)",
            structured_context,
        )

    return evidence


def select_relevant_evidence_for_prompt(question, evidence):
    if not evidence:
        return []

    folded = ascii_fold((question or "").lower())
    selected = []

    for hints, allowed_kinds in _BYPASS_RULES:
        if not any(hint in folded for hint in hints):
            continue

        for block in evidence:
            if not isinstance(block, dict):
                continue
            kind = block.get("kind", "")
            if any(kind.startswith(prefix) for prefix in allowed_kinds):
                selected.append(block)

        if selected:
            return selected

    return evidence[:3]


def format_evidence_for_prompt(evidence, max_body_chars=1800):
    """Render labelled evidence blocks into a single string for the LLM prompt.

    Each block becomes:

        ── Evidence #N — <kind> ──
        <body>

    The numeric IDs and explicit kind labels make it easy for the model to
    refer back to a specific piece — and to ignore the ones that don't
    answer the user's question.
    """
    if not evidence:
        return ""
    parts = []
    for index, block in enumerate(evidence, start=1):
        if isinstance(block, dict):
            kind = block.get("kind", "evidence")
            body = block.get("body", "")
        else:
            # Backwards-compatibility for old plain-string evidence.
            kind = "evidence"
            body = str(block)
        body = compact_evidence_for_generation(body, max_chars=max_body_chars)
        parts.append(f"── Evidence #{index} — {kind} ──\n{body}")
    return "\n\n".join(parts)


def build_llm_grounding_text(evidence_text, rag_context):
    """Combine structured evidence with semantic-search context for the LLM.

    Structured evidence is precise, while the vector-RAG context proves that
    the answer still comes from semantic retrieval over official ACU content.
    The semantic context is compacted so it helps the model without drowning
    the high-confidence evidence.
    """
    sections = []

    if evidence_text:
        sections.append(
            "STRUCTURED OFFICIAL EVIDENCE\n"
            "These facts were extracted from official ACU pages, OBS pages, "
            "or official PDF tables.\n\n"
            f"{evidence_text}"
        )

    if rag_context:
        sections.append(
            "SEMANTIC SEARCH RESULTS\n"
            "These passages were retrieved with embedding similarity from "
            "the ACU knowledge base. Use them as additional grounding.\n\n"
            f"{compact_evidence_for_generation(rag_context, max_chars=1800)}"
        )

    return "\n\n".join(sections).strip()


def has_encoding_or_language_artifacts(answer):
    folded = ascii_fold(answer)
    if re.search(r"[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]", answer or ""):
        return True

    artifact_markers = [
        "Ã", "Ä", "Å", "�",
        "muhesenligi", "muheşenligi", "molukuler", "doya biyoloji",
        "universitesinin muhendislik ve doya",
        "cift anadol", "biyoistatik", "yari-yili", "4.yari",
        "muhendisligi 4", "zorunlu yaz staji",
        "diplomasal", "contanj", "kontenjinin", "chap'ta", "chap ta",
        "students double major", "total student", "related programme",
        "programme contanj", "program contanj",
        "question'a", "passage", "relevant olan", "information'i",
        "context'e", "source:", "url:",
    ]
    return any(marker in answer for marker in artifact_markers[:4]) or any(
        marker in folded for marker in artifact_markers[4:]
    )


def llm_answer_failed_validation(answer, grounding_text, fallback_answer):
    if not answer or answer.strip() == NOT_FOUND_MESSAGE:
        return True

    if is_degenerate_output(answer):
        return True

    if answer.count("── Evidence #") >= 2:
        return True

    if has_encoding_or_language_artifacts(answer):
        return True

    validation_text = fallback_answer or grounding_text
    if not validation_text:
        return False

    # For exact person/role answers, 3B models sometimes keep the person's
    # name but mangle the department/faculty title ("Mühendisliği" →
    # "Mühendisi"). If the authoritative fallback has a titled role, require
    # the answer to preserve the meaningful title tokens as well.
    if any(role in ascii_fold(validation_text) for role in [
        "bolum baskani",
        "department head",
        "dekani",
        "dean",
    ]):
        normalized_fallback = re.sub(r"[^a-z0-9]+", " ", ascii_fold(validation_text)).strip()
        normalized_answer = re.sub(r"[^a-z0-9]+", " ", ascii_fold(answer)).strip()
        if normalized_fallback and normalized_fallback not in normalized_answer:
            return True

        fallback_tokens = [
            token for token in re.findall(r"[a-z0-9]+", ascii_fold(validation_text))
            if len(token) > 4
            and token not in STOP_WORDS
            and token not in {"resmi", "sayfasinda", "listelenen", "vardir"}
        ]
        answer_folded = ascii_fold(answer)
        missing = [token for token in fallback_tokens if token not in answer_folded]
        if missing:
            return True

    evidence_numbers = set(re.findall(r"\d+(?:[.,]\d+)?%?", validation_text))
    answer_numbers = set(re.findall(r"\d+(?:[.,]\d+)?%?", answer))
    if any(number not in evidence_numbers for number in answer_numbers):
        return True

    if not answer_is_grounded(answer, validation_text):
        return True

    if not answer_preserves_course_codes(answer, validation_text):
        return True

    if not answer_preserves_person_names(answer, validation_text):
        return True

    if not answer_preserves_bullet_items(answer, validation_text):
        return True

    return False


def general_rag_answer_failed_validation(answer, grounding_text):
    """Lighter validation for open-ended semantic RAG answers.

    Structured extractors have exact official facts, so their validation can be
    strict. General RAG answers are paraphrases over longer retrieved passages;
    requiring every content token to appear verbatim rejects useful answers.
    This check still blocks empty, degenerate, numerically unsupported, and
    clearly ungrounded responses.
    """
    if not answer or answer.strip() == NOT_FOUND_MESSAGE:
        return True

    if is_degenerate_output(answer):
        return True

    if answer.count("── Evidence #") >= 2:
        return True

    if has_encoding_or_language_artifacts(answer):
        return True

    if not grounding_text:
        return True

    evidence_numbers = set(re.findall(r"\d+(?:[.,]\d+)?%?", grounding_text))
    answer_numbers = set(re.findall(r"\d+(?:[.,]\d+)?%?", answer))
    if any(number not in evidence_numbers for number in answer_numbers):
        return True

    answer_tokens = evidence_content_tokens(answer)
    if not answer_tokens:
        return True

    grounding_folded = ascii_fold(grounding_text)
    supported = [
        token for token in answer_tokens
        if token in grounding_folded
        or any(variant in grounding_folded for variant in keyword_variants(token))
    ]

    # Open-ended answers may include small connective words or paraphrases, but
    # most meaningful terms should still be traceable to official context.
    return (len(supported) / max(len(answer_tokens), 1)) < 0.50


def general_rag_extractive_fallback(question, grounding_text):
    if not grounding_text:
        return ""

    folded_question = ascii_fold(question)
    topic_hints = []
    if any(hint in folded_question for hint in ["kuruldu", "kurulus", "ne zaman"]):
        topic_hints.extend(["2007", "kurulan", "kuruldu", "kuruluş", "kurulus"])
    if any(hint in folded_question for hint in ["nerede", "adres", "konum", "kampus", "kampusu"]):
        topic_hints.extend(["istanbul", "anadolu", "kampus", "kampüs", "kayışdağı", "kayisdagi", "ataşehir", "atasehir", "adres"])
    if any(hint in folded_question for hint in ["hakkinda", "hakkında", "bilgi", "kisaca", "kısaca"]):
        topic_hints.extend(["kurulan", "tematik", "sağlık bilimleri", "saglik bilimleri", "akademik kadro", "teknolojik altyapı"])

    query_tokens = [
        token for token in evidence_content_tokens(question)
        if ascii_fold(token) not in GENERAL_RAG_GENERIC_TOKENS
    ]

    raw_text = re.sub(r"Relevance Score:\s*[0-9.]+", " ", grounding_text)
    raw_text = re.sub(r"Source:.*?(?=Topic:)", " ", raw_text, flags=re.DOTALL)
    raw_text = re.sub(r"Topic:.*?(?=URL:)", " ", raw_text, flags=re.DOTALL)
    raw_text = re.sub(r"URL:.*?(?=Passage:)", " ", raw_text, flags=re.DOTALL)
    raw_text = raw_text.replace("Passage:", " ")
    raw_text = re.sub(r"\s+", " ", raw_text).strip()

    sentences = [
        sentence.strip(" -")
        for sentence in re.split(r"(?<=[.!?])\s+", raw_text)
        if 40 <= len(sentence.strip()) <= 450
    ]

    scored = []
    for sentence in sentences:
        sentence = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]+", " ", sentence)
        sentence = re.sub(r"\s+", " ", sentence).strip()
        readable = re.sub(r"[^\w\s.,;:!?()/%'’&\-\u00c0-\u024f]", "", sentence, flags=re.UNICODE)
        if sentence and (len(readable) / max(len(sentence), 1)) < 0.90:
            continue
        if "[Page" in sentence or "[page" in sentence:
            continue
        start_match = re.search(r"Acıbadem Üniversitesi,\s*2007", sentence, flags=re.IGNORECASE)
        if start_match:
            sentence = sentence[start_match.start():].strip()

        folded_sentence = ascii_fold(sentence)

        score = 0
        score += sum(3 for hint in topic_hints if ascii_fold(hint) in folded_sentence)
        score += sum(1 for token in query_tokens if token in folded_sentence)

        if "acibadem universitesi" in folded_sentence:
            score += 2
        if any(noise in folded_sentence for noise in EXTRACTIVE_NOISE_HINTS) and score < 6:
            continue
        if score > 0:
            scored.append((score, sentence))

    if not scored:
        return ""

    scored.sort(key=lambda item: item[0], reverse=True)
    best_sentences = []
    seen = set()
    for _score, sentence in scored:
        normalized = ascii_fold(sentence)
        if normalized in seen:
            continue
        seen.add(normalized)
        best_sentences.append(sentence)
        if len(best_sentences) >= 2:
            break

    if not best_sentences:
        return ""

    if detect_question_language(question) == "Turkish":
        return "Resmi kaynaklardan alınan ilgili bilgi:\n" + "\n".join(
            f"- {sentence.rstrip('.') }." for sentence in best_sentences
        )

    return "Relevant information from official sources:\n" + "\n".join(
        f"- {sentence.rstrip('.') }." for sentence in best_sentences
    )


@csrf_exempt
def api_chat(request):
    """ACU chatbot endpoint.

    Pipeline (LLM-first RAG flow):

        1. Vector RAG (pgvector + nomic-embed) finds the most relevant
           knowledge-base passages for the question.
        2. If retrieval is empty, fetch question-specific sources from the
           live ACU site once and retry.
        3. Run every high-precision structured extractor (curriculum, faculty,
           regulation, options, …) on the content. Each extractor is
           content-driven — it returns evidence only when its preconditions
           (matching department, track, etc.) are satisfied.
        4. The grounded LLM (Qwen) is given:
              - all extractor evidence
              - the RAG context as additional grounding
           and produces the final answer in the question's language.
        5. Deterministic extractor output is used only as a safety fallback
           when the LLM answer is empty, degenerate, or fails grounding checks.
    """
    if request.method != "POST":
        return JsonResponse({"error": "Only POST allowed"}, status=405)

    try:
        data = json.loads(request.body)
        question = data.get("question", "").strip()
        conversation_id = data.get("conversation_id")

        if not question:
            return JsonResponse({"error": "Question is required"}, status=400)

        if conversation_id:
            try:
                conversation = Conversation.objects.get(id=conversation_id)
            except Conversation.DoesNotExist:
                conversation = Conversation.objects.create(title=question[:50])
        else:
            conversation = Conversation.objects.create(title=question[:50])

        # ------------------------------------------------------------------
        # Helper: fetch fresh sources from acibadem.edu.tr at most once per
        # request. Used as a fallback when the existing KB has no relevant
        # records for the question.
        # ------------------------------------------------------------------
        sources_already_ensured = False

        def _lazy_ensure_sources():
            nonlocal sources_already_ensured
            if sources_already_ensured:
                return False
            sources_already_ensured = True
            before = KnowledgeBase.objects.count()
            try:
                ensure_sources_for_question(question)
            except Exception:
                return False
            return KnowledgeBase.objects.count() > before

        # ------------------------------------------------------------------
        # 1. Vector RAG retrieval (with one lazy-ensure retry)
        # ------------------------------------------------------------------
        context = get_rag_context(question)
        if not context and _lazy_ensure_sources():
            context = get_rag_context(question)

        # ------------------------------------------------------------------
        # 2. Structured evidence: every extractor runs unconditionally and
        #    returns "" when its preconditions aren't met. We retry once
        #    after fetching fresh sources if we got nothing.
        # ------------------------------------------------------------------
        evidence = collect_structured_evidence(question)
        if not evidence and not sources_already_ensured and _lazy_ensure_sources():
            evidence = collect_structured_evidence(question)
            if not context:
                context = get_rag_context(question)

        # ------------------------------------------------------------------
        # 3. LLM-first RAG generation. The exact extractor answer is kept
        #    only as a safety fallback; it is not the primary response path.
        # ------------------------------------------------------------------
        if not evidence and not context:
            answer = localized_not_found(question)
        else:
            relevant_evidence = select_relevant_evidence_for_prompt(question, evidence)
            evidence_text = format_evidence_for_prompt(relevant_evidence)
            prompt_input = build_llm_grounding_text(evidence_text, context)
            fallback_answer = _pick_authoritative_answer(question, evidence)

            if prompt_input:
                try:
                    answer = call_llm(
                        build_grounded_generation_prompt(question, prompt_input),
                        timeout=105,
                        num_predict=220,
                    )
                except Exception:
                    answer = ""
                answer = enforce_answer_language(question, answer)
            else:
                answer = ""

            if fallback_answer:
                if llm_answer_failed_validation(answer, prompt_input, fallback_answer):
                    answer = fallback_answer
            elif general_rag_answer_failed_validation(answer, prompt_input):
                answer = general_rag_extractive_fallback(question, context) or localized_not_found(question)

            answer = format_answer_for_display(question, answer)

        ChatMessage.objects.create(
            conversation=conversation,
            question=question,
            answer=answer,
        )

        return JsonResponse({
            "answer": answer,
            "conversation_id": conversation.id,
        })

    except requests.exceptions.ConnectionError:
        return JsonResponse({"error": "The AI service is currently unavailable. Please try again in a moment."}, status=503)

    except requests.exceptions.Timeout:
        return JsonResponse({"error": "The AI took too long to respond. Please try again."}, status=504)

    except Exception as exc:
        logger.exception("api_chat failed")
        payload = {"error": "An unexpected error occurred. Please try again."}
        if getattr(settings, "DEBUG", False):
            payload["details"] = str(exc)
        return JsonResponse(payload, status=500)
