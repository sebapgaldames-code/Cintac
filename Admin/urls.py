from django.urls import path
from . import views

app_name = "access"

urlpatterns = [
    path("", views.panel, name="admin_dashboard"),
]