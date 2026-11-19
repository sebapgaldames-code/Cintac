import hashlib

from django.core.cache import cache
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods, require_POST
from Admin.models import UserAccess
from Admin.permissions import is_admin_user
from .forms import LoginForm, RegistroForm

LOGIN_ATTEMPT_LIMIT = 5
LOGIN_LOCKOUT_SECONDS = 60


def _login_cache_keys(username):
    username_hash = hashlib.sha256(username.strip().casefold().encode()).hexdigest()
    return f"login-attempts:{username_hash}", f"login-blocked:{username_hash}"


def inicio(request):
    if request.user.is_authenticated:
        return redirect("operacion")

    username = request.POST.get("username", "") if request.method == "POST" else ""
    attempts_key, blocked_key = _login_cache_keys(username)
    form = LoginForm(request=request, data=request.POST if request.method == "POST" else None)

    if cache.get(blocked_key):
        form.add_error(None, "Has superado los 5 intentos. El acceso estará bloqueado por 1 minuto.")
    elif request.method == "POST":
        if form.is_valid():
            cache.delete(attempts_key)
            cache.delete(blocked_key)
            user = form.get_user()
            login(request, user)
            return redirect("operacion")

        if form.non_field_errors():
            attempts = cache.get(attempts_key, 0) + 1
            if attempts >= LOGIN_ATTEMPT_LIMIT:
                cache.delete(attempts_key)
                cache.set(blocked_key, True, timeout=LOGIN_LOCKOUT_SECONDS)
                form.add_error(None, "Has superado los 5 intentos. El acceso estará bloqueado por 1 minuto.")
            else:
                cache.set(attempts_key, attempts, timeout=LOGIN_LOCKOUT_SECONDS)

    return render(request, "inicioApp/inicioApp.html", {"form": form})


@login_required(login_url="inicio")
@require_http_methods(["GET", "POST"])
def operacion(request):
    if request.method == "POST":
        direction = request.POST.get("direction")
        if direction not in {"importacion", "exportacion"}:
            return render(
                request,
                "inicioApp/operacion.html",
                {"error": "Selecciona importar o exportar materiales."},
                status=400,
            )
        request.session["quote_direction"] = direction
        request.session.pop("quote_data", None)
        request.session["quote_step"] = 1
        return redirect("cotizador")

    return render(
        request,
        "inicioApp/operacion.html",
        {"is_admin": is_admin_user(request.user)},
    )


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