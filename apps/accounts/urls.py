from django.urls import path

from .views import GoogleAuthView, LoginView, LogoutView, RegisterView

urlpatterns = [
    path("register", RegisterView.as_view(), name="auth-register"),
    path("login", LoginView.as_view(), name="auth-login"),
    path("google", GoogleAuthView.as_view(), name="auth-google"),
    path("logout", LogoutView.as_view(), name="auth-logout"),
]
