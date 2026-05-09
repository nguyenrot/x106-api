from django.urls import path

from .views import AdminLoginView, AdminLogoutView

urlpatterns = [
    path("login", AdminLoginView.as_view(), name="admin-login"),
    path("logout", AdminLogoutView.as_view(), name="admin-logout"),
]
