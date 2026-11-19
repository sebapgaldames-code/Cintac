from django.urls import path
from . import views

urlpatterns = [
    path("", views.inicio, name="inicio"),
    path("operacion/", views.operacion, name="operacion"),
    path("registro/", views.registro, name="registro"),
    path("cerrar-sesion/", views.cerrar_sesion, name="cerrar_sesion"),
]
