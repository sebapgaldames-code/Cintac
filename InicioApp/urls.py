from django.urls import path
from . import views

urlpatterns = [
    path("", views.inicio, name="inicio"),
    path("cerrar-sesion/", views.cerrar_sesion, name="cerrar_sesion"),
]
