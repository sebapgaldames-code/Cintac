from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods
from .models import UserAccess
from .permissions import is_admin_user


@login_required(login_url="inicio")
@require_http_methods(["GET", "POST"])
def panel(request):
	if not is_admin_user(request.user):
		raise PermissionDenied

	if request.method == "POST":
		user_model = get_user_model()
		try:
			target = user_model.objects.get(pk=request.POST.get("user_id"))
		except (user_model.DoesNotExist, ValueError, TypeError):
			messages.error(request, "No se encontró ese usuario.")
			return redirect("access:admin_dashboard")

		if target.is_superuser or target.pk == request.user.pk:
			messages.error(request, "No puedes modificar tu propia cuenta ni una cuenta raíz.")
			return redirect("access:admin_dashboard")

		access, _ = UserAccess.objects.get_or_create(user=target)
		access.is_admin = request.POST.get("is_admin") == "on"
		access.can_edit_tariffs = request.POST.get("can_edit_tariffs") == "on"
		access.save(update_fields=["is_admin", "can_edit_tariffs"])
		messages.success(request, f"Permisos actualizados para {target.get_username()}.")
		return redirect("access:admin_dashboard")

	user_model = get_user_model()
	users = user_model.objects.order_by("username")
	for user in users:
		user.access_settings, _ = UserAccess.objects.get_or_create(user=user)

	return render(request, "Admin/Admin.html", {"users": users})
