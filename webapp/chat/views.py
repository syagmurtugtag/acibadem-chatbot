import json
import requests
from django.shortcuts import render
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
from django.db.models import Q
from .models import KnowledgeBase, ChatMessage, Conversation

def chat_view(request):
    conversations = Conversation.objects.all().order_by('-created_at')[:20]
    return render(request, 'chat/index.html', {'conversations': conversations})

def get_conversation(request, conv_id):
    try:
        conversation = Conversation.objects.get(id=conv_id)
        messages = ChatMessage.objects.filter(conversation=conversation).order_by('created_at')
        data = {
            'id': conversation.id,
            'title': conversation.title,
            'messages': [
                {'question': m.question, 'answer': m.answer}
                for m in messages
            ]
        }
        return JsonResponse(data)
    except Conversation.DoesNotExist:
        return JsonResponse({'error': 'Not found'}, status=404)

def expand_query_terms(question):
    q = question.lower()
    keyword_map = {
        "double major": ["cift anadal", "cap", "double major", "ikinci anadal"],
        "minor": ["yandal", "minor", "yan dal"],
        "gpa": ["gano", "ortalama", "gpa", "basari notu"],
        "application": ["basvuru", "application", "apply", "muracaat"],
        "requirements": ["kosullar", "sartlar", "gereklilikler", "requirements", "criteria"],
        "course": ["ders", "course", "dersler"],
        "credits": ["kredi", "ects", "akts", "credit"],
        "biomedical": ["biomedical", "biyomedikal"],
        "computer engineering": ["computer engineering", "bilgisayar muhendisligi"],
        "molecular": ["molecular biology", "molekuler biyoloji"],
        "psychology": ["psychology", "psikoloji"],
        "nursing": ["nursing", "hemsirelik"],
        "nutrition": ["nutrition", "beslenme"],
        "physiotherapy": ["physiotherapy", "fizyoterapi"],
        "health management": ["health management", "saglik yonetimi"],
        "sociology": ["sociology", "sosyoloji"],
    }
    terms = [question.strip()]
    for eng, variants in keyword_map.items():
        if eng in q:
            terms.extend(variants)
    return list(set([t for t in terms if t]))

@csrf_exempt
def api_chat(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'Only POST allowed'}, status=405)
    try:
        data = json.loads(request.body)
        question = data.get('question', '').strip()
        conversation_id = data.get('conversation_id')

        if not question:
            return JsonResponse({'error': 'Question is required'}, status=400)

        if conversation_id:
            try:
                conversation = Conversation.objects.get(id=conversation_id)
            except Conversation.DoesNotExist:
                conversation = Conversation.objects.create(title=question[:50])
        else:
            conversation = Conversation.objects.create(title=question[:50])

        search_terms = expand_query_terms(question)
        query = Q()
        for term in search_terms:
            query |= Q(title__icontains=term)
            query |= Q(content__icontains=term)
            query |= Q(topic__icontains=term)

        results = KnowledgeBase.objects.filter(query).distinct()[:5]
        if not results.exists():
            results = KnowledgeBase.objects.all().order_by('-scraped_at')[:5]

        context = '\n\n'.join([
            f"Source: {r.title}\n{r.content[:3000]}"
            for r in results
        ])

        if not context or results.count() == 0:
            context = "The knowledge base is currently empty."

        prompt = f"""You are the Acibadem University Academic Assistant.

RULES:
- Answer using ONLY the TEXT provided below.
- When listing departments or options, include the COMPLETE list exactly as written in the TEXT.
- Do NOT summarize or shorten any list.
- Do NOT add information not in the TEXT.
- If not found, say: "I am sorry, I could not find specific information about this."
- Answer in the same language as the QUESTION.

TEXT:
{context}

QUESTION:
{question}

ANSWER (include complete lists):"""

        response = requests.post(
            f"{settings.OLLAMA_URL}/api/generate",
            json={
                "model": "llama3.2:3b",
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature": 0.1,
                    "top_p": 0.9,
                    "num_predict": 400
                }
            },
            timeout=120
        )
        response.raise_for_status()
        answer = response.json().get('response', '').strip()

        for phrase in ["Based on the text,", "According to the context,", "In other words,"]:
            answer = answer.replace(phrase, "")
        answer = answer.strip()

        ChatMessage.objects.create(
            conversation=conversation,
            question=question,
            answer=answer
        )

        return JsonResponse({'answer': answer, 'conversation_id': conversation.id})

    except requests.exceptions.ConnectionError:
        return JsonResponse({'error': 'LLM service is not running.'}, status=503)
    except Exception as e:
        return JsonResponse({'error': f'System Error: {str(e)}'}, status=500)
