from django.urls import path
from . import views

urlpatterns = [
    path('', views.chat_view, name='chat'),
    path('api/chat/', views.api_chat, name='api_chat'),
    path('api/conversation/<int:conv_id>/', views.get_conversation, name='get_conversation'),
]
