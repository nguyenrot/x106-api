from django.urls import path

from .views import GameSaveView

urlpatterns = [
    path("save", GameSaveView.as_view(), name="vandao-save"),
]
