from .models import UserAccess


def is_admin_user(user):
	if not user.is_authenticated:
		return False
	if user.is_superuser:
		return True
	return UserAccess.objects.filter(user_id=user.pk, is_admin=True).exists()


def can_edit_tariffs(user):
	if not user.is_authenticated:
		return False
	if user.is_superuser:
		return True
	return UserAccess.objects.filter(user_id=user.pk, can_edit_tariffs=True).exists()


def can_view_tariffs(user):
	return is_admin_user(user) or can_edit_tariffs(user)