import tempfile
from datetime import timedelta
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
		sheet.append(
			[
				"Puerto Origen", "Pais Origen", "Puerto Destino", "Tipo de Ruta",
				"Tarifa 20 Min", "Tarifa 20 Max", "Tarifa 40 Min", "Tarifa 40 Max",
				"Transito Min", "Transito Max", "Fuente",
			]
		)
		sheet.append(["Shanghai", "China", "San Antonio", "Directo", 1000, 1200, 2000, 2400, 25, 30, "Fuente test"])
		workbook.save(self.workbook_path)

		user_model = get_user_model()
		self.common = user_model.objects.create_user(username="ClientePrueba", password="ClaveSegura2026!")
		UserAccess.objects.create(user=self.common)
		self.editor = user_model.objects.create_user(username="EditorPrueba", password="ClaveSegura2026!")
		UserAccess.objects.create(user=self.editor, can_edit_tariffs=True)
		self.admin = user_model.objects.create_user(username="AdminPrueba", password="ClaveSegura2026!")
		UserAccess.objects.create(user=self.admin, is_admin=True)

	def _row_post_data(self, **changes):
		workbook = load_workbook(self.workbook_path, data_only=True)
		values = list(workbook.active.iter_rows(min_row=2, max_row=2, values_only=True))[0]
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
		self.assertContains(response, 'src="/static/images/CotizacionAerea.jpg"')
		self.assertContains(response, 'src="/static/images/CotizacionMaritima.jpg"')
		self.assertNotContains(response, "Terrestre")
		self.assertNotContains(response, "Puerto Origen")
		self.assertNotContains(response, "Tarifa 20 Min")
		self.assertNotContains(response, "TarifasFijas")
		self.assertFalse(response.context["can_view_tariffs"])

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

	def test_quote_estimate_uses_selected_transport_route_and_weight(self):
		self.client.force_login(self.common)
		self.client.post(reverse("cotizador"), {"transport": "aereo"})
		self.client.post(
			reverse("cotizador"),
			{"origin": "Shanghai", "destination": "San Antonio"},
		)

		response = self.client.get(reverse("cotizador"))

		self.assertEqual(response.context["step"], 3)
		self.assertEqual(response.context["estimate"], {"minimum": 298, "maximum": 402})
		self.assertEqual(self.client.session["quote_data"]["delay_days"], 0)

	def test_route_step_accepts_nonnegative_estimated_delay_days(self):
		self.client.force_login(self.common)
		self.client.post(reverse("cotizador"), {"transport": "aereo"})
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

		self.assertEqual(response.context["estimate"], {"minimum": 1001, "maximum": 1001})

	def test_maritime_tariff_interpolates_weight_and_counts_containers(self):
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

		self.assertEqual(response.context["estimate"], {"minimum": 1100, "maximum": 1100})
		self.assertEqual(response.context["container_count"], 1)
		self.assertContains(response, "data-price-min=\"1000\"")

		session = self.client.session
		session["quote_data"]["weight"] = 30000
		session.save()
		response = self.client.get(reverse("cotizador"))

		self.assertEqual(response.context["estimate"], {"minimum": 2240, "maximum": 2240})
		self.assertEqual(response.context["container_count"], 2)
		self.assertContains(response, 'id="container-count">2</span>')
		self.assertContains(response, "máximo 25 toneladas por contenedor")

	def test_maritime_currency_conversion_uses_weighted_tariff(self):
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

		self.assertEqual(response.context["estimate"], {"minimum": "1.045.000", "maximum": "1.045.000"})
		self.assertContains(response, "$1.045.000 CLP")
		self.assertContains(response, "mindicador.cl")

	def test_quote_summary_formats_tons_and_shows_required_container_count(self):
		self.client.force_login(self.common)
		session = self.client.session
		session["quote_step"] = 4
		session["quote_data"] = {
			"transport": "maritimo",
			"origin": "Shanghai",
			"destination": "San Antonio",
			"cargo_type": "general",
			"weight": 25500,
			"weight_unit": "ton",
			"container_size": "20",
		}
		session.save()

		response = self.client.get(reverse("cotizador"))

		self.assertContains(response, "25,5 toneladas")
		self.assertEqual(response.context["container_count"], 2)
		self.assertContains(response, "Contenedores")
		self.assertContains(response, 'dd>2</dd>')

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
