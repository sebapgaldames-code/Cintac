import os
import tempfile
import json
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.cache import cache
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from openpyxl import load_workbook
from Admin.permissions import can_edit_tariffs, can_view_tariffs, is_admin_user


TRANSPORTS = (
	{"id": "maritimo", "label": "Marítimo", "image": "images/CotizacionMaritima.jpg", "starting_price": "Por ruta y contenedor"},
	{"id": "aereo", "label": "Aéreo", "image": "images/CotizacionAerea.jpg", "starting_price": "Desde US$150"},
)
CARGO_TYPES = (("general", "General", "📦"),)
WEIGHT_UNITS = {"kg", "ton"}
CURRENCIES = {"USD", "CLP"}
CONTAINER_CAPACITIES_KG = {"20": 23_000, "40": 25_000}
CONTAINER_EXTRA_COST_PER_TONNE = {"20": Decimal("18.50"), "40": Decimal("15.00")}
MAX_QUOTE_WEIGHT_KG = 1_000_000
USD_CLP_CACHE_KEY = "cotizador:usd-clp-rate"
USD_CLP_CACHE_SECONDS = 60 * 60


def _workbook_path():
	return Path(
		getattr(
			settings,
			"TARIFAS_FIJAS_PATH",
			settings.BASE_DIR / "Cotizador" / "TarifasFijas.xlsx",
		)
	)


def _available_routes(direction="importacion"):
	path = _workbook_path()
	if not path.exists():
		return []
	workbook = load_workbook(path, read_only=True, data_only=True)
	sheet_name = "Exportacion" if direction == "exportacion" else "Importacion"
	if sheet_name not in workbook.sheetnames:
		workbook.close()
		return []
	sheet = workbook[sheet_name]
	routes = []
	for row in sheet.iter_rows(min_row=2, values_only=True):
		if not row[0] or not row[2]:
			continue
		transit_min, transit_max = row[8], row[9]
		try:
			if Decimal(str(transit_min)) > Decimal(str(transit_max)):
				transit_min, transit_max = transit_max, transit_min
		except InvalidOperation:
			pass
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
				"transit_min": transit_min,
				"transit_max": transit_max,
			}
		)
	workbook.close()
	return routes


def _usd_clp_rate():
	cached_rate = cache.get(USD_CLP_CACHE_KEY)
	now = timezone.now()
	if cached_rate:
		fetched_at = datetime.fromisoformat(cached_rate["fetched_at"])
		if (now - fetched_at).total_seconds() < USD_CLP_CACHE_SECONDS:
			return {**cached_rate, "is_stale": False}

	url = getattr(settings, "USD_CLP_RATE_URL", "https://mindicador.cl/api/dolar")
	try:
		request = Request(url, headers={"User-Agent": "CintacCotizador/1.0"})
		with urlopen(request, timeout=5) as response:
			payload = json.load(response)
		latest = payload["serie"][0]
		rate = Decimal(str(latest["valor"]))
		rate_date = date.fromisoformat(latest["fecha"][:10]).strftime("%d/%m/%Y")
		if not rate.is_finite() or rate <= 0:
			raise ValueError("La fuente entregó una tasa de cambio no válida.")
		cached_rate = {
			"rate": str(rate),
			"date": rate_date,
			"fetched_at": now.isoformat(),
		}
		cache.set(USD_CLP_CACHE_KEY, cached_rate, timeout=None)
		return {**cached_rate, "is_stale": False}
	except (URLError, TimeoutError, OSError, ValueError, KeyError, TypeError, IndexError, InvalidOperation):
		if cached_rate:
			return {**cached_rate, "is_stale": True}
		return None


def _container_count(weight, container_size):
	capacity = CONTAINER_CAPACITIES_KG[container_size]
	return (int(weight) + capacity - 1) // capacity


def _estimate_container_quote(route, weight, container_size, container_count):
	weight_tonnes = Decimal(str(weight)) / Decimal("1000")
	minimum_tariff = Decimal(str(route[f"{container_size}_min"]))
	maximum_tariff = Decimal(str(route[f"{container_size}_max"]))
	weight_surcharge = (
		weight_tonnes * CONTAINER_EXTRA_COST_PER_TONNE[container_size]
	).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
	minimum = (
		container_count * minimum_tariff + weight_surcharge
	).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
	maximum = (
		container_count * maximum_tariff + weight_surcharge
	).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
	return minimum, maximum, weight_surcharge


def _estimate_quote(transport, route, weight, container_size, declared_container_count=None):
	if transport == "maritimo":
		container_count = declared_container_count
		if container_count is None:
			container_count = _container_count(weight, container_size)
		minimum, maximum, _ = _estimate_container_quote(
			route, weight, container_size, container_count,
		)
		return minimum, maximum
	weight = Decimal(str(weight))
	minimum = Decimal("148") + weight * Decimal("1.5")
	maximum = Decimal("150") + weight * Decimal("2.52")
	minimum = Decimal(str(minimum)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
	maximum = Decimal(str(maximum)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
	return int(minimum), int(maximum)


def _format_estimate(minimum, maximum, currency, exchange_rate):
	if currency == "CLP" and exchange_rate is not None:
		minimum = int((Decimal(minimum) * exchange_rate).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
		maximum = int((Decimal(maximum) * exchange_rate).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
		return {
			"minimum": f"{minimum:,}".replace(",", "."),
			"maximum": f"{maximum:,}".replace(",", "."),
		}
	return {"minimum": minimum, "maximum": maximum}


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
	currency_error = request.session.pop("quote_currency_error", None)
	direction = request.session.get("quote_direction", "importacion")
	if direction not in {"importacion", "exportacion"}:
		direction = "importacion"
	routes = _available_routes(direction)
	transport_ids = {item["id"] for item in TRANSPORTS}
	if quote.get("transport") and quote["transport"] not in transport_ids:
		quote = {}
		step = 1
		request.session.pop("quote_data", None)
		request.session["quote_step"] = step

	if request.method == "POST":
		action = request.POST.get("action")
		if action == "restart":
			request.session.pop("quote_data", None)
			request.session["quote_step"] = 1
			return redirect("cotizador")
		if action == "currency":
			currency = request.POST.get("currency")
			if currency == "CLP":
				if _usd_clp_rate() is not None:
					request.session["quote_currency"] = currency
					request.session.pop("quote_currency_error", None)
				else:
					request.session["quote_currency_error"] = (
						"No se pudo consultar el dólar observado. Mantuvimos la cotización en USD; "
						"intenta cambiar a CLP nuevamente cuando haya conexión."
					)
			elif currency in CURRENCIES:
				request.session["quote_currency"] = currency
				request.session.pop("quote_currency_error", None)
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
				if transport != "maritimo":
					quote.pop("container_count", None)
				quote["weight"] = quote.get("weight", "100")
		elif step == 2:
			origin = request.POST.get("origin", "")
			destination = request.POST.get("destination", "")
			delay_days_raw = request.POST.get("delay_days", "0")
			quote.update({"origin": origin, "destination": destination})
			route = next(
				(item for item in routes if item["origin"] == origin and item["destination"] == destination),
				None,
			)
			if route is None:
				errors.append("Selecciona una ruta disponible.")
			else:
				try:
					delay_days = int(delay_days_raw)
					if delay_days < 0:
						raise ValueError
				except (TypeError, ValueError):
					errors.append("Ingresa días de retraso como un número entero igual o mayor que cero.")
				if not errors:
					quote.update({
						"origin": origin,
						"destination": destination,
						"country": route["country"],
						"delay_days": delay_days,
					})
		elif step == 3:
			cargo_type = request.POST.get("cargo_type", "general")
			weight = request.POST.get("weight", "")
			weight_unit = request.POST.get("weight_unit", "kg")
			container_count_raw = request.POST.get("container_count", "").strip()
			quote.pop("container_count", None)
			if quote.get("transport") == "maritimo" and container_count_raw:
				try:
					if not container_count_raw.isdecimal():
						raise ValueError
					container_count = int(container_count_raw)
					if container_count < 1:
						raise ValueError
					quote["container_count"] = container_count
				except (TypeError, ValueError):
					errors.append("La cantidad de contenedores debe ser un número entero mayor que cero.")
			try:
				weight_value = Decimal(weight)
				if weight_unit == "ton":
					weight_value *= Decimal("1000")
				if not weight_value.is_finite() or weight_value != weight_value.to_integral_value():
					weight_value = Decimal("0")
				weight_value = int(weight_value)
			except (InvalidOperation, TypeError, ValueError):
				weight_value = 0
			description = request.POST.get("description", "").strip()
			quote.update({"cargo_type": cargo_type, "description": description})
			quote.pop("container_size", None)
			if cargo_type not in {item[0] for item in CARGO_TYPES}:
				errors.append("Selecciona el tipo de carga.")
			if weight_unit not in WEIGHT_UNITS:
				errors.append("Selecciona una unidad de peso válida.")
			if not 1 <= weight_value <= MAX_QUOTE_WEIGHT_KG:
				errors.append("El peso debe equivaler a un valor entre 1 kg y 1000 toneladas.")
			elif (
				quote.get("transport") == "maritimo"
				and quote.get("container_count")
				and weight_value > quote["container_count"] * CONTAINER_CAPACITIES_KG["40"]
			):
				errors.append(
					"La cantidad indicada no alcanza para el peso total; "
					"cada contenedor de 40 pies soporta hasta 25 toneladas."
				)
			if len(description) > 500:
				errors.append("La descripción no puede superar los 500 caracteres.")
			if weight_unit in WEIGHT_UNITS:
				quote["weight_unit"] = weight_unit
			if 1 <= weight_value <= MAX_QUOTE_WEIGHT_KG:
				quote["weight"] = weight_value
		elif step == 4:
			if action != "complete":
				errors.append("Confirma el resumen para completar la cotización.")
			elif not all(quote.get(field) for field in ("transport", "origin", "destination", "cargo_type", "weight")):
				errors.append("Completa los pasos anteriores antes de confirmar la cotización.")
			else:
				quote["completed"] = True
		else:
			request.session["quote_step"] = 1
			return redirect("cotizador")

		if not errors:
			request.session["quote_data"] = quote
			request.session["quote_step"] = min(4, step + 1)
			return redirect("cotizador")

	step = request.session.get("quote_step", 1)
	if not (request.method == "POST" and errors and step == 3):
		quote = request.session.get("quote_data", quote)
	currency = request.session.get("quote_currency", "USD")
	if currency not in CURRENCIES:
		currency = "USD"
	exchange_rate_data = _usd_clp_rate() if currency == "CLP" else None
	if currency == "CLP" and exchange_rate_data is None:
		currency = "USD"
		request.session["quote_currency"] = currency
		currency_error = (
			"No se pudo consultar el dólar observado. La cotización se muestra en USD "
			"para evitar usar una tasa desactualizada."
		)
	usd_to_clp_rate = Decimal(exchange_rate_data["rate"]) if exchange_rate_data else None
	usd_to_clp_rate_display = (
		format(usd_to_clp_rate, ",.2f").replace(",", "X").replace(".", ",").replace("X", ".")
		if usd_to_clp_rate
		else ""
	)
	weight_unit = quote.get("weight_unit", "kg")
	if weight_unit not in WEIGHT_UNITS:
		weight_unit = "kg"
	weight_display = Decimal(str(quote.get("weight", 100)))
	if weight_unit == "ton":
		weight_display /= Decimal("1000")
	weight_display = format(weight_display.normalize(), "f")
	selected_route = next(
		(item for item in routes if item["origin"] == quote.get("origin") and item["destination"] == quote.get("destination")),
		None,
	)
	estimate = None
	container_estimates = []
	container_tariffs = None
	if selected_route and quote.get("transport"):
		if quote["transport"] == "maritimo":
			declared_container_count = quote.get("container_count")
			container_tariffs = {
				size: {
					"minimum": str(selected_route[f"{size}_min"]),
					"maximum": str(selected_route[f"{size}_max"]),
				}
				for size in ("20", "40")
			}
			weight = int(quote.get("weight", 100))
			for size in ("20", "40"):
				container_count = declared_container_count or _container_count(weight, size)
				is_available = weight <= container_count * CONTAINER_CAPACITIES_KG[size]
				if is_available:
					minimum, maximum, weight_surcharge = _estimate_container_quote(
						selected_route, weight, size, container_count,
					)
					formatted = _format_estimate(minimum, maximum, currency, usd_to_clp_rate)
					formatted_surcharge = _format_estimate(
						weight_surcharge, weight_surcharge, currency, usd_to_clp_rate,
					)["minimum"]
				else:
					formatted = {"minimum": None, "maximum": None}
					formatted_surcharge = None
				container_estimates.append({
					"size": size,
					"container_count": container_count,
					"is_available": is_available,
					"capacity_tonnes": format(
						Decimal(CONTAINER_CAPACITIES_KG[size]) / Decimal("1000"), "f",
					).replace(".", ","),
					"minimum": formatted["minimum"],
					"maximum": formatted["maximum"],
					"weight_surcharge": formatted_surcharge,
				})
			available_estimate = next(
				(item for item in container_estimates if item["is_available"]),
				None,
			)
			if available_estimate:
				estimate = {
					"minimum": available_estimate["minimum"],
					"maximum": available_estimate["maximum"],
				}
		else:
			minimum, maximum = _estimate_quote(
				quote["transport"], selected_route, quote.get("weight", 100), "20",
			)
			estimate = _format_estimate(minimum, maximum, currency, usd_to_clp_rate)
	summary_fields = []
	if quote:
		direction_label = "Importación" if direction == "importacion" else "Exportación"
		summary_fields.append({"label": "Operación", "value": direction_label})
		transport_label = next((item["label"] for item in TRANSPORTS if item["id"] == quote.get("transport")), "")
		cargo_label = next((item[1] for item in CARGO_TYPES if item[0] == quote.get("cargo_type")), "")
		if transport_label:
			summary_fields.append({"label": "Transporte", "value": transport_label})
		for label, field in (("Origen", "origin"), ("Destino", "destination")):
			if quote.get(field):
				summary_fields.append({"label": label, "value": quote[field]})
		if selected_route:
			try:
				transit_min = int(Decimal(str(selected_route["transit_min"])))
				transit_max = int(Decimal(str(selected_route["transit_max"])))
				delay_days = int(quote.get("delay_days", 0))
			except (InvalidOperation, TypeError, ValueError):
				transit_min = transit_max = delay_days = None
			if transit_min is not None and transit_max is not None:
				summary_fields.append({
					"label": "Tiempo total de espera estimado (sin retrasos)",
					"value": f"{transit_min}–{transit_max} días",
				})
				summary_fields.append({
					"label": "Tiempo total de espera + días estimados de retraso",
					"value": f"{transit_min + delay_days}–{transit_max + delay_days} días",
				})
		if cargo_label:
			summary_fields.append({"label": "Tipo de carga", "value": cargo_label})
		if quote.get("container_count"):
			summary_fields.append({
				"label": "Contenedores informados",
				"value": quote["container_count"],
			})
		if quote.get("weight"):
			if weight_unit == "ton":
				weight_summary = f'{(Decimal(quote["weight"]) / Decimal("1000")).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP):.1f}'.replace(".", ",")
				weight_summary = f"{weight_summary} toneladas"
			else:
				weight_summary = f'{quote["weight"]} kg'
			summary_fields.append({"label": "Peso", "value": weight_summary})
		if quote.get("description"):
			summary_fields.append({"label": "Descripción", "value": quote["description"]})

	delay_days_display = (
		request.POST.get("delay_days", quote.get("delay_days", 0))
		if request.method == "POST" and step == 2
		else quote.get("delay_days", 0)
	)
	container_count_display = (
		request.POST.get("container_count", "")
		if request.method == "POST" and step == 3
		else quote.get("container_count", "")
	)
	context = {
		"step": step,
		"progress": round((step - 1) * 100 / 3),
		"quote": quote,
		"summary_fields": summary_fields,
		"delay_days": delay_days_display,
		"container_count_display": container_count_display,
		"container_estimate_basis": (
			"cantidad informada y peso transportado"
			if quote.get("container_count")
			else "peso transportado"
		),
		"currency": currency,
		"usd_to_clp_rate": usd_to_clp_rate_display,
		"usd_to_clp_rate_value": str(usd_to_clp_rate) if usd_to_clp_rate else "",
		"usd_to_clp_rate_date": exchange_rate_data["date"] if exchange_rate_data else "",
		"usd_to_clp_rate_stale": exchange_rate_data["is_stale"] if exchange_rate_data else False,
		"currency_error": currency_error,
		"weight_unit": weight_unit,
		"weight_display": weight_display,
		"max_weight_kg": MAX_QUOTE_WEIGHT_KG,
		"max_weight_ton": format(Decimal(MAX_QUOTE_WEIGHT_KG) / Decimal("1000"), "f"),
		"container_capacity_20_kg": CONTAINER_CAPACITIES_KG["20"],
		"container_capacity_40_kg": CONTAINER_CAPACITIES_KG["40"],
		"container_tariff_20": container_tariffs["20"] if container_tariffs else None,
		"container_tariff_40": container_tariffs["40"] if container_tariffs else None,
		"container_extra_per_tonne_20": str(CONTAINER_EXTRA_COST_PER_TONNE["20"]),
		"container_extra_per_tonne_40": str(CONTAINER_EXTRA_COST_PER_TONNE["40"]),
		"estimate_is_range": quote.get("transport") != "maritimo",
		"estimate": estimate,
		"container_estimates": container_estimates,
		"container_tariffs": container_tariffs,
		"direction": direction,
		"direction_label": "Importación" if direction == "importacion" else "Exportación",
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
