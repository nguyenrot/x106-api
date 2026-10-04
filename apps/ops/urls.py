from django.urls import path

from .views import OverviewView, ServiceActionView, SnippetsView, TerminalDetailView, TerminalListView

urlpatterns = [
    path("overview", OverviewView.as_view(), name="ops-overview"),
    path("terminals", TerminalListView.as_view(), name="ops-terminals"),
    path("terminals/<str:name>", TerminalDetailView.as_view(), name="ops-terminal"),
    path("snippets", SnippetsView.as_view(), name="ops-snippets"),
    path("services/action", ServiceActionView.as_view(), name="ops-service-action"),
]
