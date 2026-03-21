from django.contrib import admin
from .models import KnowledgeBase, ChatMessage, Conversation

@admin.register(KnowledgeBase)
class KnowledgeBaseAdmin(admin.ModelAdmin):
    list_display = ['title', 'topic', 'url', 'scraped_at']
    list_filter = ['topic']
    search_fields = ['title', 'content']

@admin.register(ChatMessage)
class ChatMessageAdmin(admin.ModelAdmin):
    list_display = ['question', 'created_at']
    readonly_fields = ['question', 'answer', 'created_at']

@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = ['title', 'created_at']
    search_fields = ['title']
