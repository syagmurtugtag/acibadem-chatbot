from django.db import models


class KnowledgeBase(models.Model):
    url = models.URLField(max_length=500, blank=True, default='')
    title = models.CharField(max_length=300)
    content = models.TextField()
    topic = models.CharField(max_length=100, default='general')
    pdf_file = models.FileField(upload_to='knowledge_pdfs/', null=True, blank=True)
    scraped_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.title

    class Meta:
        verbose_name_plural = "Knowledge Base"


class Conversation(models.Model):
    title = models.CharField(max_length=255, default="New Conversation")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.title} - {self.created_at.strftime('%d/%m/%Y %H:%M')}"


class ChatMessage(models.Model):
    conversation = models.ForeignKey(
        Conversation, on_delete=models.CASCADE,
        related_name='messages', null=True, blank=True
    )
    question = models.TextField()
    answer = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.question[:50]} - {self.created_at.strftime('%d/%m/%Y %H:%M')}"

    class Meta:
        ordering = ['-created_at']
