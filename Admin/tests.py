from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from .models import UserAccess


class AdminAccessTests(TestCase):
	def setUp(self):
		user_model = get_user_model()
		self.admin = user_model.objects.create_user(username="AdminPrueba", password="ClaveSegura2026!")
		UserAccess.objects.create(user=self.admin, is_admin=True)
		self.common = user_model.objects.create_user(username="UsuarioPrueba", password="ClaveSegura2026!")
		UserAccess.objects.create(user=self.common)

	def test_common_user_cannot_open_admin_panel(self):
		self.client.force_login(self.common)

		response = self.client.get(reverse("access:admin_dashboard"))

		self.assertEqual(response.status_code, 403)

	def test_admin_can_grant_admin_and_tariff_edit_access(self):
		self.client.force_login(self.admin)

		response = self.client.post(
			reverse("access:admin_dashboard"),
			{"user_id": self.common.pk, "is_admin": "on", "can_edit_tariffs": "on"},
		)

		self.assertRedirects(response, reverse("access:admin_dashboard"))
		access = UserAccess.objects.get(user=self.common)
		self.assertTrue(access.is_admin)
		self.assertTrue(access.can_edit_tariffs)

	def test_admin_cannot_modify_own_access(self):
		self.client.force_login(self.admin)

		response = self.client.post(
			reverse("access:admin_dashboard"),
			{"user_id": self.admin.pk, "can_edit_tariffs": "on"},
		)

		self.assertRedirects(response, reverse("access:admin_dashboard"))
		access = UserAccess.objects.get(user=self.admin)
		self.assertTrue(access.is_admin)
		self.assertFalse(access.can_edit_tariffs)

	def test_login_redirects_admin_to_management_panel(self):
		response = self.client.post(
			reverse("inicio"),
			{"username": "AdminPrueba", "password": "ClaveSegura2026!"},
		)

		self.assertRedirects(response, reverse("access:admin_dashboard"))
