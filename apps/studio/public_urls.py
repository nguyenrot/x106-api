from django.urls import path

from .views import PublicArtworkView


urlpatterns = [
    path("public/artworks/<str:token>", PublicArtworkView.as_view(), name="public-artwork"),
]
