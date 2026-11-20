import tempfile
from datetime import timedelta
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from django.core.cache import cache
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from openpyxl import Workbook, load_workbook
from Admin.models import UserAccess
from .views import USD_CLP_CACHE_KEY


class TariffEditorTests(TestCase):
	def setUp(self):
		cache.delete(USD_CLP_CACHE_KEY)
		self.addCleanup(cache.delete, USD_CLP_CACHE_KEY)
		temporary_directory = tempfile.TemporaryDirectory()
		self.addCleanup(temporary_directory.cleanup)
		self.workbook_path = Path(temporary_directory.name) / "TarifasFijas.xlsx"
		settings_override = override_settings(TARIFAS_FIJAS_PATH=self.workbook_path)
		settings_override.enable()
		self.addCleanup(settings_override.disable)

		workbook = Workbook()
		sheet = workbook.active
		sheet.title = "Importacion"
		sheet.append(
			[
				"Puerto Origen", "Pais Origen", "Puerto Destino", "Tipo de Ruta",
				"Tarifa 20 Min", "Tarifa 20 Max", "Tarifa 40 Min", "Tarifa 40 Max",
				"Transito Min", "Transito Max", "Fuente",
			]
		)
		sheet.append(["Shanghai", "China", "San Antonio", "Directo", 1000, 1200, 2000, 2400, 25, 30, "Fuente test"])
		export_sheet = workbook.create_sheet("Exportacion")
		export_sheet.append(
			[
				"Puerto Origen", "Pais Origen", "Puerto Destino", "Tipo de Ruta",
				"Tarifa 20 Min", "Tarifa 20 Max", "Tarifa 40 Min", "Tarifa 40 Max",
				"Transito Min", "Transito Max",
			]
		)
		export_sheet.append(["San Antonio", "Chile", "Callao", "Directo", 900, 900, 970, 970, 3, 5])
		workbook.save(self.workbook_path)

		user_model = get_user_model()
		self.common = user_model.objects.create_user(username="ClientePrueba", password="ClaveSegura2026!")
		UserAccess.objects.create(user=self.common)
		self.editor = user_model.objects.create_user(username="EditorPrueba", password="ClaveSegura2026!")
		UserAccess.objects.create(user=self.editor, can_edit_tariffs=True)
		self.admin = user_model.objects.create_user(username="AdminPrueba", password="ClaveSegura2026!")
		UserAccess.objects.create(user=self.admin, is_admin=True)

	def _row_post_data(self, sheet_name="Importacion", **changes):
		workbook = load_workbook(self.workbook_path, data_only=True)
		values = list(workbook[sheet_name].iter_rows(min_row=2, max_row=2, values_only=True))[0]
		workbook.close()
		data = {f"cell_2_{column}": str(value) for column, value in enumerate(values, 1)}
		data.update(changes)
		return data

	def test_common_user_sees_quote_flow_without_excel_contents(self):
		self.client.force_login(self.common)

		response = self.client.get(reverse("cotizador"))

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, "Tipo de transporte")
		self.assertContains(response, "Aéreo")
		self.assertContains(response, "Marítimo")
		self.assertLess(response.content.index(b'value="maritimo"'), response.content.index(b'value="aereo"'))
		step_start = response.content.index(b'<section class="quote-section" aria-labelledby="step-title">')
		step_end = response.content.index(b"</section>", step_start)
		self.assertIn(b'<a class="quote-secondary" href="/operacion/">', response.content[step_start:step_end])
		self.assertContains(response, 'src="/static/images/CotizacionAerea.jpg"')
		self.assertContains(response, 'src="/static/images/CotizacionMaritima.jpg"')
		self.assertNotContains(response, "Terrestre")
		self.assertNotContains(response, "Puerto Origen")
		self.assertNotContains(response, "Tarifa 20 Min")
		self.assertNotContains(response, "TarifasFijas")
		self.assertFalse(response.context["can_view_tariffs"])

	def test_operation_choice_loads_export_routes_from_export_sheet(self):
		self.client.force_login(self.common)

		response = self.client.get(reverse("operacion"))
		self.assertContains(response, "Importar materiales")
		self.assertContains(response, "Exportar materiales")
		self.assertContains(response, 'class="operation-icon operation-icon-import"')
		self.assertContains(response, 'type="checkbox" role="switch"')
		response = self.client.post(reverse("operacion"), {"direction": "exportacion"})

		self.assertRedirects(response, reverse("cotizador"))
		self.assertEqual(self.client.session["quote_direction"], "exportacion")
		self.client.post(reverse("cotizador"), {"transport": "maritimo"})
		response = self.client.get(reverse("cotizador"))
		self.assertContains(response, "Callao")
		self.assertNotContains(response, "Shanghai")
		self.assertEqual(response.context["direction"], "exportacion")
		self.assertNotContains(response, 'name="container_count"')
		self.client.post(
			reverse("cotizador"),
			{"origin": "San Antonio", "destination": "Callao", "delay_days": "0"},
		)
		response = self.client.get(reverse("cotizador"))
		self.assertNotContains(response, 'name="container_count"')

	def test_air_transport_shows_unavailable_notice_for_import_and_export(self):
		self.client.force_login(self.common)

		for direction in ("importacion", "exportacion"):
			with self.subTest(direction=direction):
				session = self.client.session
				session["quote_direction"] = direction
				session["quote_data"] = {
					"transport": "maritimo",
					"origin": "Shanghai",
					"destination": "San Antonio",
					"weight": 100,
				}
				session["quote_step"] = 1
				session.save()

				response = self.client.post(reverse("cotizador"), {"transport": "aereo"})

				self.assertRedirects(response, reverse("cotizador"))
				self.assertEqual(self.client.session["quote_step"], 5)
				response = self.client.get(reverse("cotizador"))
				self.assertContains(response, "La cotización aérea no está disponible por el momento")
				self.assertContains(response, "Volver a elegir transporte")
				self.assertNotContains(response, "Elige tu ruta")
				self.assertNotContains(response, "Estimación referencial")

				response = self.client.post(reverse("cotizador"), {"action": "back"})
				self.assertRedirects(response, reverse("cotizador"))
				self.assertEqual(self.client.session["quote_step"], 1)
				self.assertNotIn("transport", self.client.session["quote_data"])

	def test_maritime_quote_compares_both_container_sizes(self):
		self.client.force_login(self.common)
		session = self.client.session
		session["quote_step"] = 3
		session["quote_data"] = {
			"transport": "maritimo",
			"origin": "Shanghai",
			"destination": "San Antonio",
			"weight": 12500,
		}
		session.save()

		response = self.client.get(reverse("cotizador"))

		self.assertEqual(
			response.context["container_estimates"],
			[
				{
					"size": "20",
					"container_count": 1,
					"capacity_tonnes": "23",
					"is_available": True,
					"minimum": Decimal("1231.25"),
					"maximum": Decimal("1431.25"),
					"weight_surcharge": Decimal("231.25"),
				},
				{
					"size": "40",
					"container_count": 1,
					"capacity_tonnes": "25",
					"is_available": True,
					"minimum": Decimal("2187.50"),
					"maximum": Decimal("2587.50"),
					"weight_surcharge": Decimal("187.50"),
				},
			],
		)
		self.assertContains(response, "Contenedor 20 pies")
		self.assertContains(response, "Contenedor 40 pies")
		self.assertNotContains(response, 'name="container_size"')

	def test_common_user_cannot_view_excel(self):
		self.client.force_login(self.common)

		response = self.client.get(reverse("tarifas"))

		self.assertEqual(response.status_code, 403)

	def test_admin_can_view_excel_without_edit_permission(self):
		self.client.force_login(self.admin)

		response = self.client.get(reverse("tarifas"))

		self.assertContains(response, "Puerto Origen")
		self.assertNotContains(response, 'name="cell_2_5"')
		self.assertContains(self.client.get(reverse("cotizador")), "TarifasFijas")

		response = self.client.post(reverse("tarifas"), {"cell_2_5": "1100"})
		self.assertEqual(response.status_code, 403)

	def test_air_selection_cannot_continue_to_route_or_estimate(self):
		self.client.force_login(self.common)
		self.client.post(reverse("cotizador"), {"transport": "aereo"})
		response = self.client.post(
			reverse("cotizador"),
			{"origin": "Shanghai", "destination": "San Antonio"},
		)

		self.assertRedirects(response, reverse("cotizador"))
		response = self.client.get(reverse("cotizador"))
		self.assertEqual(response.context["step"], 5)
		self.assertContains(response, "La cotización aérea no está disponible por el momento")
		self.assertNotContains(response, 'name="delay_days"')

	def test_optional_cargo_value_is_added_to_air_freight_range(self):
		self.client.force_login(self.common)
		session = self.client.session
		session["quote_step"] = 3
		session["quote_data"] = {
			"transport": "aereo",
			"origin": "Shanghai",
			"destination": "San Antonio",
		}
		session.save()

		response = self.client.post(
			reverse("cotizador"),
			{"weight": "100", "weight_unit": "kg", "cargo_value": "1000"},
		)

		self.assertRedirects(response, reverse("cotizador"))
		self.assertEqual(self.client.session["quote_data"]["cargo_value"], "1000")
		self.assertEqual(self.client.session["quote_data"]["cargo_value_currency"], "USD")
		response = self.client.get(reverse("cotizador"))
		self.assertEqual(
			response.context["total_estimate"],
			{"minimum": Decimal("1298.00"), "maximum": Decimal("1402.00")},
		)
		self.assertContains(response, "Total cotización (flete + carga)")

	def test_cargo_value_is_optional_and_invalid_values_are_rejected(self):
		self.client.force_login(self.common)
		session = self.client.session
		session["quote_step"] = 3
		session["quote_data"] = {"transport": "aereo"}
		session.save()

		response = self.client.post(
			reverse("cotizador"),
			{"weight": "100", "weight_unit": "kg", "cargo_value": ""},
		)
		self.assertRedirects(response, reverse("cotizador"))
		self.assertNotIn("cargo_value", self.client.session["quote_data"])

		session = self.client.session
		session["quote_step"] = 3
		session["quote_data"] = {"transport": "aereo"}
		session.save()
		for invalid_value in ("-1", "1.001", "1000000000000", "NaN"):
			response = self.client.post(
				reverse("cotizador"),
				{"weight": "100", "weight_unit": "kg", "cargo_value": invalid_value},
			)
			self.assertContains(response, "El valor de la carga debe ser un monto")
			self.assertEqual(self.client.session["quote_step"], 3)

	def test_cargo_value_in_clp_is_combined_with_freight_in_clp(self):
		self.client.force_login(self.common)
		session = self.client.session
		session["quote_step"] = 3
		session["quote_currency"] = "CLP"
		session["quote_data"] = {"transport": "aereo"}
		session.save()

		rate = {"rate": "950", "date": "13/10/2026", "is_stale": False}
		with patch("Cotizador.views._usd_clp_rate", return_value=rate):
			response = self.client.post(
				reverse("cotizador"),
				{"weight": "100", "weight_unit": "kg", "cargo_value": "95000"},
			)
			self.assertRedirects(response, reverse("cotizador"))
			response = self.client.get(reverse("cotizador"))

		self.assertEqual(
			response.context["total_estimate"],
			{"minimum": "378.100", "maximum": "476.900"},
		)

	def test_maritime_total_adds_cargo_value_to_each_container_option(self):
		self.client.force_login(self.common)
		session = self.client.session
		session["quote_step"] = 4
		session["quote_data"] = {
			"transport": "maritimo",
			"origin": "Shanghai",
			"destination": "San Antonio",
			"cargo_type": "general",
			"weight": 12500,
			"cargo_value": "100",
			"cargo_value_currency": "USD",
		}
		session.save()

		response = self.client.get(reverse("cotizador"))

		self.assertEqual(
			[(item["total_minimum"], item["total_maximum"])
				for item in response.context["container_estimates"]],
			[
				(Decimal("1331.25"), Decimal("1531.25")),
				(Decimal("2287.50"), Decimal("2687.50")),
			],
		)
		self.assertContains(response, "Total cotización (flete + carga)")

	def test_route_step_accepts_nonnegative_estimated_delay_days(self):
		self.client.force_login(self.common)
		self.client.post(reverse("cotizador"), {"transport": "maritimo"})
		response = self.client.get(reverse("cotizador"))
		self.assertContains(response, 'name="delay_days"')
		self.assertContains(response, "Días estimados de posibles retrasos")

		response = self.client.post(
			reverse("cotizador"),
			{"origin": "Shanghai", "destination": "San Antonio", "delay_days": "7"},
		)

		self.assertRedirects(response, reverse("cotizador"))
		self.assertEqual(self.client.session["quote_data"]["delay_days"], 7)

	def test_route_step_rejects_negative_or_fractional_delay_days(self):
		self.client.force_login(self.common)
		session = self.client.session
		session["quote_step"] = 2
		session["quote_data"] = {"transport": "aereo"}
		session.save()

		for invalid_delay in ("-1", "1.5", "not-a-number"):
			response = self.client.post(
				reverse("cotizador"),
				{"origin": "Shanghai", "destination": "San Antonio", "delay_days": invalid_delay},
			)
			self.assertContains(response, "número entero igual o mayor que cero")
			self.assertEqual(self.client.session["quote_step"], 2)

	def test_maritime_estimate_uses_excel_and_container_size(self):
		self.client.force_login(self.common)
		self.client.post(reverse("cotizador"), {"transport": "maritimo"})
		self.client.post(
			reverse("cotizador"),
			{"origin": "Shanghai", "destination": "San Antonio"},
		)

		response = self.client.get(reverse("cotizador"))

		self.assertEqual(
			response.context["estimate"],
			{"minimum": Decimal("1001.85"), "maximum": Decimal("1201.85")},
		)

	def test_container_count_is_automatically_estimated_from_weight(self):
		self.client.force_login(self.common)
		session = self.client.session
		session["quote_step"] = 3
		session["quote_data"] = {
			"transport": "maritimo",
			"origin": "Shanghai",
			"destination": "San Antonio",
		}
		session.save()

		response = self.client.get(reverse("cotizador"))
		self.assertNotContains(response, 'name="container_count"')
		self.assertContains(response, 'name="cargo_value"')
		response = self.client.post(
			reverse("cotizador"),
			{
				"weight": "12500",
				"weight_unit": "kg",
				"container_count": "3",
			},
		)

		self.assertRedirects(response, reverse("cotizador"))
		self.assertNotIn("container_count", self.client.session["quote_data"])
		response = self.client.get(reverse("cotizador"))
		self.assertEqual(
			[(item["container_count"], item["minimum"], item["maximum"])
				for item in response.context["container_estimates"]],
			[
				(1, Decimal("1231.25"), Decimal("1431.25")),
				(1, Decimal("2187.50"), Decimal("2587.50")),
			],
		)
		self.assertNotContains(response, "Contenedores informados")
		self.assertContains(response, "Total cotización (flete + carga)")

	def test_container_capacity_is_checked_for_each_size(self):
		self.client.force_login(self.common)
		session = self.client.session
		session["quote_step"] = 3
		session["quote_data"] = {
			"transport": "maritimo",
			"origin": "Shanghai",
			"destination": "San Antonio",
		}
		session.save()

		response = self.client.post(
			reverse("cotizador"),
			{"weight": "24000", "weight_unit": "kg", "container_count": "1"},
		)
		self.assertRedirects(response, reverse("cotizador"))
		response = self.client.get(reverse("cotizador"))
		self.assertEqual(
			[(item["container_count"], item["is_available"]) for item in response.context["container_estimates"]],
			[(2, True), (1, True)],
		)
		self.assertNotContains(response, "No disponible: excede la capacidad del tipo de contenedor")

		session = self.client.session
		session["quote_step"] = 3
		session["quote_data"] = {
			"transport": "maritimo",
			"origin": "Shanghai",
			"destination": "San Antonio",
		}
		session.save()
		response = self.client.post(
			reverse("cotizador"),
			{"weight": "26000", "weight_unit": "kg", "container_count": "1"},
		)

		self.assertRedirects(response, reverse("cotizador"))
		response = self.client.get(reverse("cotizador"))
		self.assertEqual(
			[(item["container_count"], item["is_available"]) for item in response.context["container_estimates"]],
			[(2, True), (2, True)],
		)

	def test_maritime_tariff_uses_excel_rates_and_weight_surcharge(self):
		self.client.force_login(self.common)
		session = self.client.session
		session["quote_step"] = 3
		session["quote_data"] = {
			"transport": "maritimo",
			"origin": "Shanghai",
			"destination": "San Antonio",
			"cargo_type": "general",
			"weight": 12500,
			"weight_unit": "kg",
			"container_size": "20",
		}
		session.save()

		response = self.client.get(reverse("cotizador"))

		self.assertEqual(
			response.context["estimate"],
			{"minimum": Decimal("1231.25"), "maximum": Decimal("1431.25")},
		)
		self.assertEqual(
			[item["container_count"] for item in response.context["container_estimates"]],
			[1, 1],
		)
		self.assertContains(response, "data-price-20-min=\"1000\"")
		self.assertContains(response, "data-price-40-min=\"2000\"")

		session = self.client.session
		session["quote_data"]["weight"] = 30000
		session.save()
		response = self.client.get(reverse("cotizador"))

		self.assertEqual(
			response.context["estimate"],
			{"minimum": Decimal("2555.00"), "maximum": Decimal("2955.00")},
		)
		self.assertEqual(
			[item["container_count"] for item in response.context["container_estimates"]],
			[2, 2],
		)
		self.assertContains(response, "capacidad 23 t")
		self.assertContains(response, "capacidad 25 t")

	def test_maritime_currency_conversion_includes_weight_surcharge(self):
		self.client.force_login(self.common)
		session = self.client.session
		session["quote_step"] = 3
		session["quote_data"] = {
			"transport": "maritimo",
			"origin": "Shanghai",
			"destination": "San Antonio",
			"weight": 12500,
			"container_size": "20",
		}
		session.save()
		with patch("Cotizador.views._usd_clp_rate", return_value={
			"rate": "950",
			"date": "13/10/2026",
			"is_stale": False,
		}):
			self.client.post(reverse("cotizador"), {"action": "currency", "currency": "CLP"})
			response = self.client.get(reverse("cotizador"))

		self.assertEqual(
			response.context["estimate"],
			{"minimum": "1.169.688", "maximum": "1.359.688"},
		)
		self.assertContains(response, 'id="estimate-20">$1.169.688 - $1.359.688 CLP')
		self.assertContains(response, '$2.078.125 - $2.458.125 CLP')
		self.assertContains(response, "mindicador.cl")

	def test_quote_summary_compares_separate_container_quotes_and_counts(self):
		self.client.force_login(self.common)
		session = self.client.session
		session["quote_step"] = 4
		session["quote_data"] = {
			"transport": "maritimo",
			"origin": "Shanghai",
			"destination": "San Antonio",
			"cargo_type": "general",
			"weight": 288000,
			"weight_unit": "ton",
		}
		session.save()

		response = self.client.get(reverse("cotizador"))

		self.assertContains(response, "288,0 toneladas")
		self.assertEqual(
			[item["container_count"] for item in response.context["container_estimates"]],
			[13, 12],
		)
		self.assertContains(response, "Opción contenedor 20 pies")
		self.assertContains(response, "Opción contenedor 40 pies")
		self.assertContains(response, "<dd>13</dd>", html=False)
		self.assertContains(response, "<dd>12</dd>", html=False)
		self.assertNotIn("Contenedores", [field["label"] for field in response.context["summary_fields"]])

	def test_quote_summary_shows_transit_range_and_range_with_added_delay(self):
		self.client.force_login(self.common)
		session = self.client.session
		session["quote_step"] = 4
		session["quote_data"] = {
			"transport": "maritimo",
			"origin": "Shanghai",
			"destination": "San Antonio",
			"cargo_type": "general",
			"weight": 100,
			"container_size": "20",
			"delay_days": 7,
		}
		session.save()

		response = self.client.get(reverse("cotizador"))

		self.assertContains(response, "Tiempo total de espera estimado (sin retrasos)")
		self.assertContains(response, "25–30 días")
		self.assertContains(response, "Tiempo total de espera + días estimados de retraso")
		self.assertContains(response, "32–37 días")
		self.assertContains(response, "Generar informe")
		self.assertNotContains(response, "Tipo de cambio")

	def test_legacy_terrestrial_session_is_reset_and_not_offered(self):
		self.client.force_login(self.common)
		session = self.client.session
		session["quote_step"] = 4
		session["quote_data"] = {
			"transport": "terrestre",
			"origin": "Qingdao",
			"destination": "Valparaiso",
			"cargo_type": "general",
			"weight": 116400,
			"weight_unit": "ton",
	}
		session.save()

		response = self.client.get(reverse("cotizador"))

		self.assertEqual(response.context["step"], 1)
		self.assertEqual(response.context["quote"], {})
		self.assertNotContains(response, "Terrestre")
		self.assertNotContains(response, "data-transport=\"terrestre\"")

	def test_currency_toggle_converts_displayed_estimate_to_clp(self):
		self.client.force_login(self.common)
		session = self.client.session
		session["quote_step"] = 3
		session["quote_data"] = {
			"transport": "aereo",
			"origin": "Shanghai",
			"destination": "San Antonio",
			"weight": 100,
		}
		session.save()

		with patch("Cotizador.views._usd_clp_rate", return_value={
			"rate": "950",
			"date": "13/10/2026",
			"is_stale": False,
		}):
			response = self.client.post(reverse("cotizador"), {"action": "currency", "currency": "CLP"})
			self.assertRedirects(response, reverse("cotizador"))
			response = self.client.get(reverse("cotizador"))
		self.assertContains(response, 'id="estimate-minimum">$283.100</span>')
		self.assertContains(response, 'id="estimate-maximum">$381.900</span>')
		self.assertContains(response, "CLP")
		self.assertContains(response, "1 USD = $950,00 CLP")

	def test_currency_conversion_fails_visibly_without_a_live_or_cached_rate(self):
		self.client.force_login(self.common)
		with patch("Cotizador.views._usd_clp_rate", return_value=None):
			response = self.client.post(reverse("cotizador"), {"action": "currency", "currency": "CLP"})
			self.assertEqual(response.status_code, 302)
			response = self.client.get(reverse("cotizador"))

		self.assertContains(response, "No se pudo consultar el dólar observado")
		self.assertEqual(self.client.session.get("quote_currency", "USD"), "USD")

	def test_exchange_rate_is_fetched_and_cached_for_one_hour(self):
		from .views import _usd_clp_rate

		response = BytesIO(
			b'{"serie":[{"fecha":"2026-10-13T00:00:00.000Z","valor":923.45}]}'
		)
		with patch("Cotizador.views.urlopen", return_value=response) as fetch:
			rate = _usd_clp_rate()
			cached_rate = _usd_clp_rate()

		self.assertEqual(rate["rate"], "923.45")
		self.assertEqual(rate["date"], "13/10/2026")
		self.assertFalse(rate["is_stale"])
		self.assertEqual(cached_rate, rate)
		fetch.assert_called_once()

	def test_exchange_rate_uses_last_successful_value_when_service_is_unavailable(self):
		from .views import _usd_clp_rate
		from urllib.error import URLError

		cache.set(
			USD_CLP_CACHE_KEY,
			{
				"rate": "920.5",
				"date": "12/10/2026",
				"fetched_at": (timezone.now() - timedelta(hours=2)).isoformat(),
			},
			timeout=None,
		)
		with patch("Cotizador.views.urlopen", side_effect=URLError("offline")):
			rate = _usd_clp_rate()

		self.assertEqual(rate["rate"], "920.5")
		self.assertTrue(rate["is_stale"])

	def test_tonne_input_is_stored_as_kilograms_for_estimation(self):
		self.client.force_login(self.common)
		session = self.client.session
		session["quote_step"] = 3
		session["quote_data"] = {
			"transport": "aereo",
			"origin": "Shanghai",
			"destination": "San Antonio",
		}
		session.save()

		response = self.client.post(
			reverse("cotizador"),
			{"cargo_type": "general", "weight": "0.5", "weight_unit": "ton", "container_size": "20"},
		)

		self.assertRedirects(response, reverse("cotizador"))
		self.assertEqual(self.client.session["quote_data"]["weight"], 500)
		self.assertEqual(self.client.session["quote_data"]["weight_unit"], "ton")
		self.assertContains(self.client.get(reverse("cotizador")), "0,5 toneladas")

	def test_maximum_weight_of_1000_tonnes_is_available_and_accepted(self):
		self.client.force_login(self.common)
		session = self.client.session
		session["quote_step"] = 3
		session["quote_data"] = {
			"transport": "aereo",
			"origin": "Shanghai",
			"destination": "San Antonio",
			"weight_unit": "ton",
		}
		session.save()

		response = self.client.get(reverse("cotizador"))
		self.assertContains(response, 'max="1000"')

		response = self.client.post(
			reverse("cotizador"),
			{"cargo_type": "general", "weight": "1000", "weight_unit": "ton"},
		)

		self.assertRedirects(response, reverse("cotizador"))
		self.assertEqual(self.client.session["quote_data"]["weight"], 1_000_000)

	def test_weight_above_1000_tonnes_is_rejected(self):
		self.client.force_login(self.common)
		session = self.client.session
		session["quote_step"] = 3
		session["quote_data"] = {
			"transport": "aereo",
			"origin": "Shanghai",
			"destination": "San Antonio",
		}
		session.save()

		response = self.client.post(
			reverse("cotizador"),
			{"cargo_type": "general", "weight": "1000.001", "weight_unit": "ton"},
		)

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, "El peso debe equivaler a un valor entre 1 kg y 1000 toneladas.")
		self.assertEqual(self.client.session["quote_step"], 3)
		self.assertNotIn("weight", self.client.session["quote_data"])

	def test_quote_summary_can_be_confirmed_without_contact_data(self):
		self.client.force_login(self.common)
		session = self.client.session
		session["quote_step"] = 4
		session["quote_data"] = {
			"transport": "aereo",
			"origin": "Shanghai",
			"destination": "San Antonio",
			"cargo_type": "general",
			"weight": 100,
			"description": "Muestras",
		}
		session.save()
		self.client.post(reverse("cotizador"), {"action": "complete"})

		response = self.client.get(reverse("cotizador"))

		self.assertContains(response, "ESTIMACIÓN PREPARADA")
		self.assertContains(response, "Shanghai")
		self.assertContains(response, "San Antonio")
		self.assertContains(response, "Muestras")
		self.assertNotContains(response, "Datos de contacto")
		self.assertContains(response, "Generar informe")
		self.assertContains(response, "Flete estimado")
		self.assertContains(response, 'src="/static/images/LogoCintac.jpg"')

	def test_summary_step_has_a_back_button(self):
		self.client.force_login(self.common)
		session = self.client.session
		session["quote_step"] = 4
		session["quote_data"] = {"transport": "aereo", "origin": "Shanghai", "destination": "San Antonio"}
		session.save()

		response = self.client.get(reverse("cotizador"))

		self.assertContains(response, 'name="action" value="back" formnovalidate')
		self.assertContains(response, "Generar informe")

	def test_general_cargo_is_a_static_image_and_selected_automatically(self):
		self.client.force_login(self.common)
		session = self.client.session
		session["quote_step"] = 3
		session["quote_data"] = {"transport": "aereo"}
		session.save()

		response = self.client.get(reverse("cotizador"))

		self.assertContains(response, 'src="/static/images/carga-general.png"')
		self.assertContains(response, "Carga general")
		self.assertNotContains(response, 'name="cargo_type"')
		response = self.client.post(
			reverse("cotizador"),
			{"weight": "100", "weight_unit": "kg", "container_size": "20"},
		)
		self.assertRedirects(response, reverse("cotizador"))
		self.assertEqual(self.client.session["quote_data"]["cargo_type"], "general")

		session = self.client.session
		session["quote_step"] = 3
		session["quote_data"] = {"transport": "aereo"}
		session.save()
		response = self.client.post(
			reverse("cotizador"),
			{"cargo_type": "perecedera", "weight": "100", "container_size": "20"},
		)
		self.assertContains(response, "Selecciona el tipo de carga.")

	def test_authorized_editor_saves_tariff_changes(self):
		self.client.force_login(self.editor)

		response = self.client.post(
			reverse("tarifas"),
			self._row_post_data(cell_2_5="1100"),
		)

		self.assertRedirects(response, reverse("tarifas"))
		workbook = load_workbook(self.workbook_path, data_only=True)
		self.assertEqual(workbook.active["E2"].value, 1100)
		workbook.close()

	def test_invalid_minimum_does_not_change_workbook(self):
		self.client.force_login(self.editor)

		response = self.client.post(
			reverse("tarifas"),
			self._row_post_data(cell_2_5="1300"),
		)

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, "valor mínimo no puede superar el máximo")
		workbook = load_workbook(self.workbook_path, data_only=True)
		self.assertEqual(workbook.active["E2"].value, 1000)
		workbook.close()

	def test_tariff_editor_displays_the_excel_table_on_dedicated_page(self):
		self.client.force_login(self.editor)

		response = self.client.get(reverse("tarifas"))

		self.assertContains(response, "Puerto Origen")
		self.assertContains(response, "Shanghai")
		self.assertNotContains(self.client.get(reverse("cotizador")), "Puerto Origen")

	def test_tariff_editor_can_switch_and_save_export_sheet(self):
		self.client.force_login(self.editor)

		response = self.client.get(reverse("tarifas"), {"direction": "exportacion"})

		self.assertContains(response, "Callao")
		self.assertNotContains(response, "Shanghai")
		self.assertEqual(response.context["direction"], "exportacion")
		self.assertContains(response, 'href="/cotizador/tarifas/?direction=importacion"')
		self.assertContains(response, 'href="/cotizador/tarifas/?direction=exportacion"')

		response = self.client.post(
			reverse("tarifas"),
			self._row_post_data(
				"Exportacion",
				cell_2_5="910",
				cell_2_6="920",
				direction="exportacion",
			),
		)

		self.assertRedirects(response, f"{reverse('tarifas')}?direction=exportacion")
		workbook = load_workbook(self.workbook_path, data_only=True)
		self.assertEqual(workbook["Exportacion"]["E2"].value, 910)
		self.assertEqual(workbook["Exportacion"]["F2"].value, 920)
		self.assertEqual(workbook["Importacion"]["E2"].value, 1000)
		workbook.close()
