import os
import secrets
import string
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import urlopen

BASE_URL = os.environ.get("CINTAC_BASE_URL", "http://127.0.0.1:8000")
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Cintac.settings")

import django

django.setup()

from django.contrib.auth import get_user_model
from openpyxl import load_workbook
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select, WebDriverWait

from Admin.models import UserAccess


def first_import_route():
    workbook_path = Path(__file__).with_name("TarifasFijas.xlsx")
    workbook = load_workbook(workbook_path, read_only=True, data_only=True)
    try:
        sheet = workbook["Importacion"]
        for row in sheet.iter_rows(min_row=2, values_only=True):
            if row[0] and row[2]:
                return str(row[0]), str(row[2])
    finally:
        workbook.close()
    raise RuntimeError("No hay rutas de importacion disponibles en TarifasFijas.xlsx.")


def server_is_available():
    try:
        with urlopen(BASE_URL, timeout=1):
            return True
    except (URLError, TimeoutError, OSError):
        return False


def ensure_server():
    global server_process
    if server_is_available():
        return

    parsed_url = urlparse(BASE_URL)
    if parsed_url.hostname not in {"localhost", "127.0.0.1"}:
        raise RuntimeError(f"No se pudo conectar a {BASE_URL}.")

    host = parsed_url.hostname
    port = parsed_url.port or 8000
    server_process = subprocess.Popen(
        [sys.executable, "manage.py", "runserver", f"{host}:{port}", "--noreload"],
        cwd=PROJECT_ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
    )
    for _ in range(40):
        if server_process.poll() is not None:
            raise RuntimeError("Django no pudo iniciar; revisa las migraciones y errores del servidor.")
        if server_is_available():
            return
        time.sleep(0.5)
    raise RuntimeError(f"Django no respondió en {BASE_URL}.")


def create_test_account():
    username = os.environ.get("CINTAC_TEST_USERNAME")
    password = os.environ.get("CINTAC_TEST_PASSWORD")
    if username or password:
        if not username or not password:
            raise RuntimeError("Configura ambas variables CINTAC_TEST_USERNAME y CINTAC_TEST_PASSWORD.")
        return username, password, None

    if urlparse(BASE_URL).hostname not in {"localhost", "127.0.0.1"}:
        raise RuntimeError("Para probar una URL externa, configura credenciales de prueba en variables de entorno.")

    user_model = get_user_model()
    username = "SeleniumTest" + "".join(secrets.choice(string.ascii_uppercase) for _ in range(8))
    while user_model.objects.filter(username=username).exists():
        username = "SeleniumTest" + "".join(secrets.choice(string.ascii_uppercase) for _ in range(8))
    password = f"Selenium!2026{secrets.token_hex(8)}A1"
    user = user_model.objects.create_user(username=username, password=password)
    UserAccess.objects.create(user=user)
    return username, password, user


def submit_current_step(driver, wait):
    button = wait.until(
        EC.element_to_be_clickable(
            (By.CSS_SELECTOR, "section.quote-section button.quote-primary[type='submit']")
        )
    )
    button.click()


driver = None
test_user = None
server_process = None
try:
    ensure_server()
    username, password, test_user = create_test_account()
    origin, destination = first_import_route()
    driver = webdriver.Chrome()
    wait = WebDriverWait(driver, 15)

    driver.get(BASE_URL)

    wait.until(EC.visibility_of_element_located((By.NAME, "username"))).send_keys(username)
    driver.find_element(By.NAME, "password").send_keys(password)
    driver.find_element(By.CSS_SELECTOR, "button.submit-button[type='submit']").click()
    wait.until(EC.url_contains("/operacion/"))

    wait.until(
        EC.element_to_be_clickable(
            (By.XPATH, "//button[contains(@class,'operation-card')][.//strong[normalize-space()='Importar materiales']]")
        )
    ).click()

    wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "input[name='transport'][value='maritimo']"))).click()
    submit_current_step(driver, wait)

    wait.until(EC.presence_of_element_located((By.NAME, "origin")))
    Select(driver.find_element(By.NAME, "origin")).select_by_value(origin)
    Select(driver.find_element(By.NAME, "destination")).select_by_value(destination)
    submit_current_step(driver, wait)

    weight_input = wait.until(EC.visibility_of_element_located((By.NAME, "weight")))
    weight_input.clear()
    weight_input.send_keys("20")

    total_20 = wait.until(EC.visibility_of_element_located((By.ID, "total-estimate-20")))
    previous_total = total_20.text
    cargo_value_input = driver.find_element(By.NAME, "cargo_value")
    cargo_value_input.clear()
    cargo_value_input.send_keys("15000")
    wait.until(
        lambda current_driver: current_driver.find_element(By.ID, "total-estimate-20").text
        != previous_total
    )

    assert not driver.find_elements(By.NAME, "container_count"), (
        "El formulario no deberia permitir ingresar manualmente la cantidad de contenedores."
    )
    submit_current_step(driver, wait)

    summary = wait.until(
        EC.visibility_of_element_located((By.CSS_SELECTOR, "section.quote-section .printable-report"))
    )
    assert "Valor de la carga" in summary.text, "El informe deberia incluir el valor de carga."
    driver.find_element(By.CSS_SELECTOR, "button[name='action'][value='complete']").click()

    complete_title = wait.until(EC.visibility_of_element_located((By.ID, "complete-title")))
    print(complete_title.text)
    print(driver.find_element(By.CSS_SELECTOR, ".printable-report").text)
    print("Total cotizacion 20 pies:", driver.find_element(By.ID, "total-estimate-20").text)
finally:
    if driver is not None:
        driver.quit()
    if test_user is not None:
        test_user.delete()
    if server_process is not None and server_process.poll() is None:
        server_process.terminate()
        server_process.wait(timeout=10)
