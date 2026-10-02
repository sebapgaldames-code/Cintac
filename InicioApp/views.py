from django.contrib.auth import login, logout
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST
from .forms import LoginForm


def inicio(request):
    form = LoginForm(
        request=request,
        data=request.POST if request.method == "POST" else None,
    )

    if request.method == "POST" and form.is_valid():
        login(request, form.get_user())
        return redirect("inicio")

    return render(request, "inicioApp/inicioApp.html", {"form": form})


@require_POST
def cerrar_sesion(request):
    logout(request)
    return redirect("inicio")