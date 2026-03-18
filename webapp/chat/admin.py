from django.contrib import admin
from .models import KnowledgeBase, ChatMessage

@admin.register(KnowledgeBase)
class KnowledgeBaseAdmin(admin.ModelAdmin):
    list_display = ['title', 'topic', 'url', 'scraped_at']
    list_filter = ['topic']
    search_fields = ['title', 'content']

@admin.register(ChatMessage)
class ChatMessageAdmin(admin.ModelAdmin):
    list_display = ['question', 'created_at']
    readonly_fields = ['question', 'answer', 'created_at']