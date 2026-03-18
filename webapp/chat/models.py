from django.db import models

class KnowledgeBase(models.Model):
    url = models.URLField(max_length=500)
    title = models.CharField(max_length=300)
    content = models.TextField()
    topic = models.CharField(max_length=100, default='general')
    scraped_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.title

    class Meta:
        verbose_name_plural = "Knowledge Base"


class ChatMessage(models.Model):
    question = models.TextField()
    answer = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.question[:50]} - {self.created_at.strftime('%d/%m/%Y %H:%M')}"

    class Meta:
        ordering = ['-created_at']