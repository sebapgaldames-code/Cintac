from django.contrib.auth import login, logout
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST
from Admin.models import UserAccess
from Admin.permissions import is_admin_user
from .forms import LoginForm, RegistroForm


def inicio(request):
    if request.user.is_authenticated:
        return redirect("access:admin_dashboard" if is_admin_user(request.user) else "cotizador")

    form = LoginForm(
        request=request,
        data=request.POST if request.method == "POST" else None,
    )

    if request.method == "POST" and form.is_valid():
        user = form.get_user()
        login(request, user)
        return redirect("access:admin_dashboard" if is_admin_user(user) else "cotizador")

    return render(request, "inicioApp/inicioApp.html", {"form": form})


def registro(request):
    if request.user.is_authenticated:
        return redirect("inicio")

    form = RegistroForm(
        request.POST if request.method == "POST" else None,
    )
    if request.method == "POST" and form.is_valid():
        user = form.save()
        UserAccess.objects.get_or_create(user=user)
        return redirect("inicio")

    return render(request, "inicioApp/registro.html", {"form": form})


@require_POST
def cerrar_sesion(request):
    logout(request)
    return redirect("inicio")