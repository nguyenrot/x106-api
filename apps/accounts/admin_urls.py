from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import AdminLoginView, AdminLogoutView, AdminUsersViewSet

router = DefaultRouter(trailing_slash=False)
router.register(r"users", AdminUsersViewSet, basename="admin-users")

urlpatterns = [
    path("login", AdminLoginView.as_view(), name="admin-login"),
    path("logout", AdminLogoutView.as_view(), name="admin-logout"),
    *router.urls,
]
