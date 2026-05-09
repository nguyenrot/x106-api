from django.urls import path

from .views import AdminContentListView, AdminContentSectionView

urlpatterns = [
    path("<str:app>", AdminContentListView.as_view(), name="admin-content-list"),
    path("<str:app>/<str:section>", AdminContentSectionView.as_view(), name="admin-content-section"),
]
