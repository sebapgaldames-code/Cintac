from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse


class LoginTests(TestCase):
	def test_login_page_displays_cintac_logo(self):
		response = self.client.get(reverse("inicio"))

		self.assertContains(response, "images/LogoCintac.jpg")

	def test_requires_username_and_password(self):
		response = self.client.post(reverse("inicio"), {})

		self.assertEqual(response.status_code, 200)
		self.assertIn("username", response.context["form"].errors)
		self.assertIn("password", response.context["form"].errors)

	def test_username_must_have_only_letters_and_an_uppercase(self):
		for username in ("usuario", "Nom bre", "Nombre1"):
			with self.subTest(username=username):
				response = self.client.post(
					reverse("inicio"),
					{"username": username, "password": "ClaveSegura1!"},
				)
				self.assertIn("username", response.context["form"].errors)

	def test_password_must_meet_all_complexity_rules(self):
		for password in (
			"Corta1!",
			"sinmayuscula1!",
			"SINMINUSCULA1!",
			"SinNumero!",
			"SinEspecial1",
			"Clave123 ",
		):
			with self.subTest(password=password):
				response = self.client.post(
					reverse("inicio"),
					{"username": "Usuario", "password": password},
				)
				self.assertIn("password", response.context["form"].errors)

	def test_rejects_invalid_credentials(self):
		response = self.client.post(
			reverse("inicio"),
			{"username": "Usuario", "password": "Incorrecta1!"},
		)

		self.assertEqual(response.status_code, 200)
		self.assertTrue(response.context["form"].non_field_errors())
		self.assertNotIn("_auth_user_id", self.client.session)

	def test_logs_in_with_valid_credentials(self):
		user = get_user_model().objects.create_user(
			username="Operador",
			password="ClaveSegura2026!",
		)

		response = self.client.post(
			reverse("inicio"),
			{"username": "Operador", "password": "ClaveSegura2026!"},
		)

		self.assertRedirects(response, reverse("cotizador"))
		self.assertEqual(self.client.session["_auth_user_id"], str(user.pk))
		self.assertRedirects(self.client.get(reverse("inicio")), reverse("cotizador"))


class RegistrationTests(TestCase):
	def test_registration_page_displays_account_fields(self):
		response = self.client.get(reverse("registro"))

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, "Crear cuenta")
		self.assertContains(response, 'name="username"')
		self.assertContains(response, 'name="email"')

	def test_registration_saves_user_and_allows_login(self):
		response = self.client.post(
			reverse("registro"),
			{
				"username": "NuevaCuenta",
				"email": "nueva@example.com",
				"password1": "ClaveSegura2026!",
				"password2": "ClaveSegura2026!",
			},
		)

		user = get_user_model().objects.get(username="NuevaCuenta")
		self.assertRedirects(response, reverse("inicio"))
		self.assertEqual(user.email, "nueva@example.com")
		self.assertTrue(user.check_password("ClaveSegura2026!"))
		self.assertNotIn("_auth_user_id", self.client.session)

		login_response = self.client.post(
			reverse("inicio"),
			{"username": "NuevaCuenta", "password": "ClaveSegura2026!"},
		)
		self.assertRedirects(login_response, reverse("cotizador"))
		self.assertEqual(self.client.session["_auth_user_id"], str(user.pk))

	def test_empty_registration_shows_required_fields(self):
		response = self.client.post(reverse("registro"), {})

		self.assertEqual(response.status_code, 200)
		self.assertIn("username", response.context["form"].errors)
		self.assertIn("password1", response.context["form"].errors)
