from django.urls import path

from .views import UsersMeView

urlpatterns = [
    path("me", UsersMeView.as_view(), name="users-me"),
]
