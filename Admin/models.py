from django.conf import settings
from django.db import models


class UserAccess(models.Model):
	user = models.OneToOneField(
		settings.AUTH_USER_MODEL,
		on_delete=models.CASCADE,
		related_name="useraccess",
	)
	is_admin = models.BooleanField(default=False)
	can_edit_tariffs = models.BooleanField(default=False)

	def __str__(self):
		return self.user.get_username()
