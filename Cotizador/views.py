import os
import re
import tempfile
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.validators import validate_email
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods
from openpyxl import load_workbook
from Admin.permissions import can_edit_tariffs, can_view_tariffs, is_admin_user


TRANSPORTS = (
	{"id": "aereo", "label": "Aéreo", "icon": "✈", "starting_price": "Desde US$150"},
	{"id": "maritimo", "label": "Marítimo", "icon": "▰", "starting_price": "Por ruta y contenedor"},
	{"id": "terrestre", "label": "Terrestre", "icon": "▰", "starting_price": "Desde US$100"},
)
CARGO_TYPES = (
	("general", "General", "📦"),
	("perecedera", "Perecedera", "🥬"),
	("fragil", "Frágil", "⚠"),
	("peligrosa", "Peligrosa", "☢"),
	("alto_valor", "Alto valor", "◆"),
	("otro", "Otro", "▤"),
)


def _workbook_path():
	return Path(
		getattr(
			settings,
			"TARIFAS_FIJAS_PATH",
			settings.BASE_DIR / "Cotizador" / "TarifasFijas.xlsx",
		)
	)


def _available_routes():
	path = _workbook_path()
	if not path.exists():
		return []
	workbook = load_workbook(path, read_only=True, data_only=True)
	sheet = workbook.active
	routes = []
	for row in sheet.iter_rows(min_row=2, values_only=True):
		if not row[0] or not row[2]:
			continue
		routes.append(
			{
				"origin": str(row[0]),
				"country": str(row[1] or ""),
				"destination": str(row[2]),
				"route_type": str(row[3] or ""),
				"20_min": row[4],
				"20_max": row[5],
				"40_min": row[6],
				"40_max": row[7],
				"transit_min": row[8],
				"transit_max": row[9],
			}
		)
	workbook.close()
	return routes


def _estimate_quote(transport, route, weight, container_size):
	if transport == "maritimo":
		minimum = route[f"{container_size}_min"]
		maximum = route[f"{container_size}_max"]
	else:
		weight = Decimal(str(weight))
		if transport == "aereo":
			minimum = Decimal("148") + weight * Decimal("1.5")
			maximum = Decimal("150") + weight * Decimal("2.52")
		else:
			minimum = Decimal("40") + weight * Decimal("0.6")
			maximum = Decimal("100") + weight * Decimal("1.0")
	minimum = Decimal(str(minimum)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
	maximum = Decimal(str(maximum)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
	return int(minimum), int(maximum)


def _parse_cell_value(value, column):
	if column in range(5, 11):
		try:
			number = Decimal(value)
		except (InvalidOperation, TypeError):
			raise ValueError("Ingresa un valor numérico válido para tarifas y días.")
		if not number.is_finite() or number < 0:
			raise ValueError("Las tarifas y los días deben ser números iguales o mayores que cero.")
		if column in (9, 10) and number != number.to_integral_value():
			raise ValueError("Los días de tránsito deben ser números enteros.")
		return int(number) if number == number.to_integral_value() else float(number)

	text = value.strip()
	if not text or len(text) > 200:
		raise ValueError("Completa los campos de texto con un máximo de 200 caracteres.")
	if text.startswith(("=", "+", "-", "@")):
		raise ValueError("Los campos de texto no pueden contener fórmulas.")
	return text


def _save_workbook(path, post_data):
	workbook = load_workbook(path)
	sheet = workbook.active
	for row_number in range(2, sheet.max_row + 1):
		values = []
		for column in range(1, sheet.max_column + 1):
			field_name = f"cell_{row_number}_{column}"
			value = _parse_cell_value(post_data.get(field_name, ""), column)
			sheet.cell(row=row_number, column=column).value = value
			values.append(value)

		for minimum_column, maximum_column in ((5, 6), (7, 8), (9, 10)):
			if values[minimum_column - 1] > values[maximum_column - 1]:
				raise ValueError(f"En la fila {row_number}, el valor mínimo no puede superar el máximo.")

	temporary_path = None
	try:
		with tempfile.NamedTemporaryFile(
			dir=path.parent,
			suffix=path.suffix,
			delete=False,
		) as temporary_file:
			temporary_path = Path(temporary_file.name)
		workbook.save(temporary_path)
		os.replace(temporary_path, path)
	finally:
		if temporary_path is not None:
			temporary_path.unlink(missing_ok=True)


@login_required(login_url="inicio")
@require_http_methods(["GET", "POST"])
def cotizador(request):
	quote = request.session.get("quote_data", {})
	step = request.session.get("quote_step", 1)
	errors = []
	routes = _available_routes()

	if request.method == "POST":
		action = request.POST.get("action")
		if action == "restart":
			request.session.pop("quote_data", None)
			request.session["quote_step"] = 1
			return redirect("cotizador")
		if action == "back":
			request.session["quote_step"] = max(1, step - 1)
			return redirect("cotizador")

		quote = dict(quote)
		if step == 1:
			transport = request.POST.get("transport")
			if transport not in {item["id"] for item in TRANSPORTS}:
				errors.append("Selecciona un tipo de transporte.")
			else:
				quote["transport"] = transport
				quote["weight"] = quote.get("weight", "100")
		elif step == 2:
			origin = request.POST.get("origin", "")
			destination = request.POST.get("destination", "")
			quote.update({"origin": origin, "destination": destination})
			route = next(
				(item for item in routes if item["origin"] == origin and item["destination"] == destination),
				None,
			)
			if route is None:
				errors.append("Selecciona una ruta disponible.")
			else:
				quote.update({"origin": origin, "destination": destination, "country": route["country"]})
		elif step == 3:
			cargo_type = request.POST.get("cargo_type")
			weight = request.POST.get("weight", "")
			container_size = request.POST.get("container_size", "20")
			try:
				weight_value = int(weight)
			except (TypeError, ValueError):
				weight_value = 0
			description = request.POST.get("description", "").strip()
			quote.update(
				{
					"cargo_type": cargo_type,
					"weight": weight,
					"container_size": container_size,
					"description": description,
				}
			)
			if cargo_type not in {item[0] for item in CARGO_TYPES}:
				errors.append("Selecciona el tipo de carga.")
			if not 1 <= weight_value <= 5000:
				errors.append("El peso debe estar entre 1 y 5.000 kg.")
			if container_size not in {"20", "40"}:
				errors.append("Selecciona un contenedor válido.")
			if len(description) > 500:
				errors.append("La descripción no puede superar los 500 caracteres.")
			if not errors:
				quote["weight"] = weight_value
		elif step == 4:
			name = request.POST.get("name", "").strip()
			email = request.POST.get("email", "").strip()
			phone = request.POST.get("phone", "").strip()
			quote.update({"name": name, "email": email, "phone": phone})
			if not name or len(name) > 120:
				errors.append("Ingresa tu nombre (máximo 120 caracteres).")
			try:
				validate_email(email)
			except ValidationError:
				errors.append("Ingresa un correo electrónico válido.")
			if not 8 <= len(re.sub(r"\D", "", phone)) <= 15:
				errors.append("Ingresa un teléfono válido con código de país.")
			if not errors:
				quote.update({"name": name, "email": email, "phone": phone, "completed": True})
		else:
			request.session["quote_step"] = 1
			return redirect("cotizador")

		if not errors:
			request.session["quote_data"] = quote
			request.session["quote_step"] = min(4, step + 1)
			return redirect("cotizador")

	step = request.session.get("quote_step", 1)
	quote = request.session.get("quote_data", quote)
	selected_route = next(
		(item for item in routes if item["origin"] == quote.get("origin") and item["destination"] == quote.get("destination")),
		None,
	)
	estimate = None
	if selected_route and quote.get("transport"):
		minimum, maximum = _estimate_quote(
			quote["transport"],
			selected_route,
			quote.get("weight", 100),
			quote.get("container_size", "20"),
		)
		estimate = {"minimum": minimum, "maximum": maximum}

	context = {
		"step": step,
		"progress": round((step - 1) * 100 / 3),
		"quote": quote,
		"estimate": estimate,
		"errors": errors,
		"routes": routes,
		"origins": list(dict.fromkeys(route["origin"] for route in routes)),
		"destinations": list(dict.fromkeys(route["destination"] for route in routes)),
		"transports": TRANSPORTS,
		"cargo_types": CARGO_TYPES,
		"is_admin": is_admin_user(request.user),
		"can_view_tariffs": can_view_tariffs(request.user),
	}
	return render(request, "cotizadorApp/cotizadorApp.html", context)


@login_required(login_url="inicio")
@require_http_methods(["GET", "POST"])
def tarifas(request):
	if not can_view_tariffs(request.user):
		raise PermissionDenied
	can_edit = can_edit_tariffs(request.user)
	if request.method == "POST" and not can_edit:
		raise PermissionDenied

	path = _workbook_path()
	error = None
	if not path.exists():
		error = "No se encontró el archivo de tarifas."
		workbook = None
	else:
		try:
			workbook = load_workbook(path, read_only=request.method == "GET", data_only=True)
		except (OSError, ValueError):
			workbook = None
			error = "No se pudo abrir el archivo de tarifas."

	if request.method == "POST" and workbook is not None:
		lock_path = path.with_name(f"~${path.name}")
		if lock_path.exists():
			error = "Cierra TarifasFijas.xlsx en Excel antes de guardar los cambios."
		else:
			try:
				workbook.close()
				_save_workbook(path, request.POST)
			except (OSError, PermissionError, ValueError) as exc:
				error = str(exc) or "No se pudieron guardar los cambios."
			else:
				return redirect("tarifas")

	headers = []
	rows = []
	if workbook is not None:
		sheet = workbook.active
		headers = [cell.value or "" for cell in sheet[1]]
		for row_number in range(2, sheet.max_row + 1):
			values = [sheet.cell(row=row_number, column=column).value for column in range(1, sheet.max_column + 1)]
			if request.method == "POST":
				values = [request.POST.get(f"cell_{row_number}_{column}", value) for column, value in enumerate(values, 1)]
			rows.append({"number": row_number, "values": values})
		workbook.close()

	return render(
		request,
		"cotizadorApp/tarifasFijas.html",
		{"headers": headers, "rows": rows, "error": error, "can_edit": can_edit},
	)
