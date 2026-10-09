from django.urls import path
from . import views

urlpatterns = [
    path('', views.cotizador, name='cotizador'),
    path('tarifas/', views.tarifas, name='tarifas'),
]
