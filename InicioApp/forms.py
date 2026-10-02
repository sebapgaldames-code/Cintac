from django import forms
from django.contrib.auth.forms import AuthenticationForm
from django.core.exceptions import ValidationError


class LoginForm(AuthenticationForm):
	def __init__(self, *args, **kwargs):
		super().__init__(*args, **kwargs)
		self.fields["username"].widget.attrs.update(
			{
				"class": "form-input",
				"autocomplete": "username",
				"autofocus": True,
				"placeholder": "Tu nombre de usuario",
				"pattern": r"(?=.*\p{Lu})\p{L}+",
				"title": "Usa solo letras, sin espacios, e incluye al menos una mayuscula.",
			}
		)
		self.fields["password"].widget.attrs.update(
			{
				"class": "form-input",
				"autocomplete": "current-password",
				"placeholder": "Tu contrasena",
				"minlength": 8,
				"pattern": r"(?=.*\p{Lu})(?=.*\p{Ll})(?=.*\p{N})(?=.*[^\p{L}\p{N}\s]).{8,}",
				"title": "Minimo 8 caracteres, con mayuscula, minuscula, numero y caracter especial.",
			}
		)

	def clean_username(self):
		username = self.cleaned_data["username"]
		if not username.isalpha():
			raise ValidationError("El nombre solo puede contener letras y no puede tener espacios.")
		if not any(character.isupper() for character in username):
			raise ValidationError("El nombre debe incluir al menos una letra mayuscula.")
		return username

	def clean_password(self):
		password = self.cleaned_data["password"]
		errors = []
		if len(password) < 8:
			errors.append("La contrasena debe tener al menos 8 caracteres.")
		if not any(character.isupper() for character in password):
			errors.append("La contrasena debe incluir una letra mayuscula.")
		if not any(character.islower() for character in password):
			errors.append("La contrasena debe incluir una letra minuscula.")
		if not any(character.isdigit() for character in password):
			errors.append("La contrasena debe incluir un número.")
		if not any(not character.isalnum() and not character.isspace() for character in password):
			errors.append("La contrasena debe incluir un caracter especial, por ejemplo @.")
		if errors:
			raise ValidationError(errors)
		return password