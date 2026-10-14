from django import forms
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.core.exceptions import ValidationError
from django.contrib.auth import get_user_model

PASSWORD_PATTERN = r"(?=.*\p{Lu})(?=.*\p{Ll})(?=.*\p{N})(?=.*[^\p{L}\p{N}\s]).{8,}"
PASSWORD_TITLE = "Minimo 8 caracteres, con mayuscula, minuscula, numero y caracter especial."
EMAIL_INVALID_MESSAGE = "Ingresa una dirección de correo electrónico válida."


def validate_username(username):
	if not username.isalpha():
		raise ValidationError("El nombre solo puede contener letras y no puede tener espacios.")
	if not any(character.isupper() for character in username):
		raise ValidationError("El nombre debe incluir al menos una letra mayuscula.")
	return username


def validate_password_complexity(password):
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


class RegistroForm(UserCreationForm):
	email = forms.EmailField(
		required=False,
		error_messages={"invalid": EMAIL_INVALID_MESSAGE},
		widget=forms.EmailInput(
			attrs={
				"class": "form-input",
				"autocomplete": "email",
				"placeholder": "tu@correo.com",
				"data-invalid-message": EMAIL_INVALID_MESSAGE,
			}
		),
	)

	class Meta(UserCreationForm.Meta):
		model = get_user_model()
		fields = ("username", "email")

	def __init__(self, *args, **kwargs):
		super().__init__(*args, **kwargs)
		self.fields["username"].widget.attrs.update(
			{
				"class": "form-input",
				"autocomplete": "username",
				"autofocus": True,
				"placeholder": "Tu nombre de usuario",
			}
		)
		self.fields["username"].label = "Nombre de usuario"
		self.fields["email"].label = "Correo electrónico (opcional)"
		self.fields["password1"].widget.attrs.update(
			{
				"class": "form-input",
				"autocomplete": "new-password",
				"placeholder": "Crea una contrasena segura",
				"pattern": PASSWORD_PATTERN,
				"title": PASSWORD_TITLE,
			}
		)
		self.fields["password1"].label = "Contrasena"
		self.fields["password2"].widget.attrs.update(
			{
				"class": "form-input",
				"autocomplete": "new-password",
				"placeholder": "Repite tu contrasena",
				"pattern": PASSWORD_PATTERN,
				"title": PASSWORD_TITLE,
			}
		)
		self.fields["password2"].label = "Confirmar contrasena"

	def clean_username(self):
		return validate_username(self.cleaned_data["username"])

	def clean_password1(self):
		return validate_password_complexity(self.cleaned_data["password1"])


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
				"pattern": PASSWORD_PATTERN,
				"title": PASSWORD_TITLE,
			}
		)

	def clean_username(self):
		return validate_username(self.cleaned_data["username"])

	def clean_password(self):
		return validate_password_complexity(self.cleaned_data["password"])