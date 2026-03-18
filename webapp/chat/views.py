import json
import requests
from django.shortcuts import render
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
from .models import KnowledgeBase, ChatMessage


def chat_view(request):
    messages = ChatMessage.objects.all()[:20]
    return render(request, 'chat/index.html', {'messages': messages})


@csrf_exempt
def api_chat(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'Only POST allowed'}, status=405)

    try:
        data = json.loads(request.body)
        question = data.get('question', '').strip()
        if not question:
            return JsonResponse({'error': 'Question is required'}, status=400)

        # Retrieve relevant context from database
        results = KnowledgeBase.objects.filter(
            content__icontains=question.split()[0]
        )[:3]

        context = '\n\n'.join([
            f"Source: {r.title}\n{r.content[:500]}"
            for r in results
        ])

        if not context:
            context = "No specific information found in the knowledge base."

        # Build prompt
        prompt = f"""You are an assistant for Acibadem University. Answer questions using only the context below.
If the answer is not in the context, say so clearly. Do not invent information.

Context:
{context}

Question: {question}
Answer:"""

        # Call Ollama API
        response = requests.post(
            f"{settings.OLLAMA_URL}/api/generate",
            json={
                "model": "llama3.2:3b",
                "prompt": prompt,
                "stream": False
            },
            timeout=60
        )
        response.raise_for_status()
        answer = response.json().get('response', '').strip()

        # Save to database
        ChatMessage.objects.create(question=question, answer=answer)

        return JsonResponse({'answer': answer})

    except requests.exceptions.ConnectionError:
        return JsonResponse({'error': 'LLM service is currently unavailable. Please try again later.'}, status=503)
    except requests.exceptions.Timeout:
        return JsonResponse({'error': 'The request timed out. Please try again.'}, status=503)
    except Exception as e:
        return JsonResponse({'error': 'An unexpected error occurred.'}, status=500)