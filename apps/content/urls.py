from django.urls import path

from .views import PublicContentView

urlpatterns = [
    path("content/<str:app>/<str:section>", PublicContentView.as_view(), name="public-content"),
]
