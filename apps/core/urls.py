from django.urls import path

from . import views

urlpatterns = [
    path("health", views.health, name="health"),
    path("now/weather", views.now_weather, name="now-weather"),
]
