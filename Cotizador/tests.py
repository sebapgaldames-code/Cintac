import tempfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from openpyxl import Workbook, load_workbook
from Admin.models import UserAccess


class TariffEditorTests(TestCase):
	def setUp(self):
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

	def test_maritime_estimate_uses_excel_and_container_size(self):
		self.client.force_login(self.common)
		self.client.post(reverse("cotizador"), {"transport": "maritimo"})
		self.client.post(
			reverse("cotizador"),
			{"origin": "Shanghai", "destination": "San Antonio"},
		)

		response = self.client.get(reverse("cotizador"))

		self.assertEqual(response.context["estimate"], {"minimum": 1000, "maximum": 1200})

	def test_quote_can_complete_all_four_steps(self):
		self.client.force_login(self.common)
		self.client.post(reverse("cotizador"), {"transport": "aereo"})
		self.client.post(
			reverse("cotizador"),
			{"origin": "Shanghai", "destination": "San Antonio"},
		)
		self.client.post(
			reverse("cotizador"),
			{"cargo_type": "general", "weight": "100", "container_size": "20", "description": "Muestras"},
		)
		self.client.post(
			reverse("cotizador"),
			{"name": "Cliente Prueba", "email": "cliente@example.com", "phone": "+56 9 1234 5678"},
		)

		response = self.client.get(reverse("cotizador"))

		self.assertContains(response, "ESTIMACIÓN PREPARADA")
		self.assertContains(response, "Cliente Prueba")

	def test_contact_step_back_button_skips_required_contact_fields(self):
		self.client.force_login(self.common)
		session = self.client.session
		session["quote_step"] = 4
		session["quote_data"] = {"transport": "aereo", "origin": "Shanghai", "destination": "San Antonio"}
		session.save()

		response = self.client.get(reverse("cotizador"))

		self.assertContains(response, 'name="action" value="back" formnovalidate')

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
