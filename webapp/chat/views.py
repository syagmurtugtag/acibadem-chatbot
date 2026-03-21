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
            'messages': [{'question': m.question, 'answer': m.answer} for m in messages]
        }
        return JsonResponse(data)
    except Conversation.DoesNotExist:
        return JsonResponse({'error': 'Not found'}, status=404)

def get_relevant_context(question):
    q = question.lower()
    
    department_keywords = {
        'undergraduate_cap': [
            'biomedical engineering', 'molecular biology', 'psychology', 
            'computer engineering', 'nursing', 'nutrition', 'physiotherapy',
            'health management', 'sociology', 'medicine', 'pharmacy',
            'biyomedikal', 'psikoloji', 'hemsirelik', 'beslenme', 'saglik',
            'sosyoloji', 'tip', 'eczacilik'
        ],
        'minor': ['minor', 'yandal'],
        'double_major_rules': ['gpa', 'gano', 'requirement', 'apply', 'application', 
                               'semester', 'credit', 'basvuru', 'sart', 'koşul'],
        'associate': ['vocational', 'associate', 'onlisans', 'meslek'],
    }
    
    matched_topics = []
    for topic, keywords in department_keywords.items():
        for kw in keywords:
            if kw in q:
                matched_topics.append(topic)
                break
    
    if not matched_topics:
        results = KnowledgeBase.objects.all().order_by('-scraped_at')[:3]
        return '\n\n'.join([f"Source: {r.title}\n{r.content[:2000]}" for r in results])
    
    context_parts = []
    
    if 'undergraduate_cap' in matched_topics:
        records = KnowledgeBase.objects.filter(
            Q(title__icontains='Undergraduate') | Q(title__icontains='Department Options')
        )[:3]
        for r in records:
            context_parts.append(f"Source: {r.title}\n{r.content[:3000]}")
    
    if 'minor' in matched_topics:
        records = KnowledgeBase.objects.filter(
            Q(title__icontains='Minor') | Q(topic__icontains='minor')
        )[:3]
        for r in records:
            context_parts.append(f"Source: {r.title}\n{r.content[:2000]}")
    
    if 'double_major_rules' in matched_topics:
        records = KnowledgeBase.objects.filter(
            Q(title__icontains='Application') | Q(title__icontains='Requirements') | 
            Q(title__icontains='Directive')
        )[:3]
        for r in records:
            context_parts.append(f"Source: {r.title}\n{r.content[:2000]}")
    
    if 'associate' in matched_topics:
        records = KnowledgeBase.objects.filter(
            Q(title__icontains='Associate') | Q(title__icontains='Vocational')
        )[:2]
        for r in records:
            context_parts.append(f"Source: {r.title}\n{r.content[:2000]}")
    
    if not context_parts:
        records = KnowledgeBase.objects.all().order_by('-scraped_at')[:3]
        context_parts = [f"Source: {r.title}\n{r.content[:2000]}" for r in records]
    
    return '\n\n'.join(context_parts)

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

        context = get_relevant_context(question)

        if not context:
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
