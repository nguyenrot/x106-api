"""Root URL configuration."""

from django.contrib import admin
from django.http import HttpResponse
from django.urls import include, path
from django.views.decorators.cache import cache_control
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularSwaggerView,
)


@cache_control(max_age=86400)
def robots_txt(_request):
    """api.kynguyen.cc is a pure JSON/admin backend — no public content to index."""
    return HttpResponse("User-agent: *\nDisallow: /\n", content_type="text/plain")


urlpatterns = [
    path("robots.txt", robots_txt),
    path("admin/", admin.site.urls),
    path("api/v1/", include("apps.core.urls")),
    path("api/v1/auth/", include("apps.accounts.urls")),
    path("api/v1/users/", include("apps.accounts.users_urls")),
    path("api/v1/journal/", include("apps.journal.urls")),
    path("api/v1/", include("apps.habits.urls")),
    path("api/v1/ledger/", include("apps.ledger.urls")),
    path("api/v1/quotes/", include("apps.quotes.urls")),
    path("api/v1/", include("apps.studio.urls")),
    path("api/v1/", include("apps.studio.public_urls")),
    path("api/v1/", include("apps.content.urls")),
    path("api/v1/admin/", include("apps.accounts.admin_urls")),
    path("api/v1/admin/content/", include("apps.content.admin_urls")),
    path("api/v1/admin/console/", include("apps.console.urls")),
    path("api/v1/admin/quotes/", include("apps.quotes.admin_urls")),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="docs"),
]
