from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import AdminLoginView, AdminLogoutView, AdminUsersViewSet, AdminVerifyView

router = DefaultRouter(trailing_slash=False)
router.register(r"users", AdminUsersViewSet, basename="admin-users")

urlpatterns = [
    path("login", AdminLoginView.as_view(), name="admin-login"),
    path("logout", AdminLogoutView.as_view(), name="admin-logout"),
    path("verify", AdminVerifyView.as_view(), name="admin-verify"),
    *router.urls,
]
