import json
import re
import requests

from django.shortcuts import render
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings

from .models import KnowledgeBase, ChatMessage, Conversation


NOT_FOUND_MESSAGE = "I am sorry, I could not find specific information about this."

STOP_WORDS = {
    "i", "am", "a", "an", "the", "to", "of", "in", "on", "at", "for", "and",
    "or", "is", "are", "do", "does", "can", "could", "would", "should",
    "lets", "let", "say", "about", "please", "which", "what", "who", "where",
    "when", "how", "from", "into", "my", "your", "their", "our", "student",
    "students"
}

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

TRACK_TITLE_MAP = {
    "double major": "Double Major",
    "minor": "Minor",
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
]

TURKISH_HINTS = {
    "ç", "ğ", "ı", "İ", "ö", "ş", "ü",
    " nedir", " nasıl", " hangi", " bölüm", " fakülte", " öğrenci",
    " başvuru", " şart", " koşul", " yandal", " çift anadal",
    " var mı", " neler", " hakkında",
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

    definition_patterns = [
        "what is minor",
        "what is a minor",
        "define minor",
        "what is double major",
        "what is a double major",
        "define double major",
    ]

    return any(pattern in q for pattern in definition_patterns)


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


def find_exact_option_record(question):
    track = detect_track(question)
    source_department, _ = detect_source_and_target_departments(question)

    if not track or not source_department:
        return None

    if not is_option_question(question) and not is_yes_no_option_question(question):
        return None

    department_title = DEPARTMENT_TITLE_MAP[source_department].lower()
    track_title = TRACK_TITLE_MAP[track].lower()

    records = KnowledgeBase.objects.all()

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


def call_llm(prompt):
    response = requests.post(
        f"{settings.OLLAMA_URL}/api/generate",
        json={
            "model": "llama3.2:3b",
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": 0.0,
                "top_p": 0.9,
                "num_predict": 400
            }
        },
        timeout=120
    )
    response.raise_for_status()

    answer = response.json().get("response", "").strip()

    answer = re.sub(
        r'^(according to the text|based on the text|according to the context|based on the context)\s*,?\s*',
        '',
        answer,
        flags=re.IGNORECASE
    ).strip()

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


@csrf_exempt
def api_chat(request):
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

        definition_record = find_definition_record(question)

        if definition_record:
            context = (
                f"Source: {definition_record.title}\n"
                f"Topic: {definition_record.topic}\n"
                f"URL: {definition_record.url}\n"
                f"{definition_record.content}"
            )

            answer = call_llm(build_definition_prompt(question, context))
            answer = enforce_answer_language(question, answer)

            if not answer:
                answer = NOT_FOUND_MESSAGE

            ChatMessage.objects.create(
                conversation=conversation,
                question=question,
                answer=answer
            )

            return JsonResponse({
                "answer": answer,
                "conversation_id": conversation.id
            })

        exact_option_record = find_exact_option_record(question)

        if exact_option_record:
            context, expected_options = build_structured_context_from_record(exact_option_record)

            source_department, target_department = detect_source_and_target_departments(question)

            if is_yes_no_option_question(question) and target_department:
                answer = call_llm(build_yes_no_prompt(question, context, target_department))
            else:
                answer = call_llm(build_prompt(question, context))

                if expected_options:
                    missing_options = [
                        option for option in expected_options
                        if not option_present_in_answer(option, answer)
                    ]

                    if missing_options:
                        answer = call_llm(build_revision_prompt(question, context, missing_options))

            answer = enforce_answer_language(question, answer)

            if not answer:
                answer = NOT_FOUND_MESSAGE

            ChatMessage.objects.create(
                conversation=conversation,
                question=question,
                answer=answer
            )

            return JsonResponse({
                "answer": answer,
                "conversation_id": conversation.id
            })

        context = get_general_context(question)

        if not context:
            ChatMessage.objects.create(
                conversation=conversation,
                question=question,
                answer=NOT_FOUND_MESSAGE
            )

            return JsonResponse({
                "answer": NOT_FOUND_MESSAGE,
                "conversation_id": conversation.id
            })

        answer = call_llm(build_prompt(question, context))
        answer = enforce_answer_language(question, answer)

        if not answer:
            answer = NOT_FOUND_MESSAGE

        ChatMessage.objects.create(
            conversation=conversation,
            question=question,
            answer=answer
        )

        return JsonResponse({
            "answer": answer,
            "conversation_id": conversation.id
        })

    except requests.exceptions.ConnectionError:
        return JsonResponse({"error": "The AI service is currently unavailable. Please try again in a moment."}, status=503)

    except requests.exceptions.Timeout:
        return JsonResponse({"error": "The AI took too long to respond. Please try again."}, status=504)

    except Exception as e:
        return JsonResponse({"error": f"An unexpected error occurred. Please try again."}, status=500)
