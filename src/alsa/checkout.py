"""Lleva la compra hasta la página de datos de alsa.es en un Chrome controlado por CDP.

La web de Alsa guarda búsqueda y selección en la sesión del servidor, así que no hay un
enlace directo a un autobús: hay que hacer los pasos en el propio navegador. Se abre
Chrome con un perfil propio y el puerto de depuración (CDP), se lanza la búsqueda, se
elige el autobús y la tarifa con las mismas peticiones que hace la web y se navega
hasta «Datos». La ventana queda abierta para que el usuario rellene y pague.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from curl_cffi import requests as curl_requests
from curl_cffi.requests import WebSocket

from alsa.core import BASE_URL, PORTLET_ID, results_url
from alsa.exceptions import AlsaError

PROFILE_DIR = Path.home() / ".cache" / "alsa-cli" / "chrome"
PAGE_TIMEOUT = 60  # segundos por página (incluye la cola de queue-it)

CHROME_PATHS = {
    "darwin": [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    ],
    "win32": [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    ],
}
CHROME_COMMANDS = ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "brave-browser", "microsoft-edge"]


class CheckoutError(AlsaError):
    """No se pudo preparar la compra en el navegador."""


@dataclass(frozen=True)
class Selection:
    """Qué comprar: la búsqueda y el autobús (por hora de salida y parada) con su tarifa."""

    origin_id: str
    destination_id: str
    date: str  # DD/MM/YYYY
    adults: int
    departure_ms: int
    origin_stop_id: str
    package_id: str | None = None


def _step_url(step: int) -> str:
    return (
        f"{BASE_URL}/checkout?p_p_id={PORTLET_ID}&p_p_lifecycle=1&p_p_state=normal&p_p_mode=view"
        f"&_{PORTLET_ID}_javax.portlet.action=Step{step}Action"
    )


def find_chrome() -> str | None:
    for path in CHROME_PATHS.get(sys.platform, []):
        if Path(path).exists():
            return path
    for command in CHROME_COMMANDS:
        if found := shutil.which(command):
            return found
    return None


class _Tab:
    """Cliente CDP mínimo sobre una pestaña: comandos síncronos, eventos ignorados."""

    def __init__(self, ws_url: str) -> None:
        self._ws = WebSocket()
        self._ws.connect(ws_url)
        self._next_id = 0

    def call(self, method: str, params: dict | None = None, timeout: float = PAGE_TIMEOUT) -> dict:
        self._next_id += 1
        msg_id = self._next_id
        self._ws.send_str(json.dumps({"id": msg_id, "method": method, "params": params or {}}))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            data = json.loads(self._ws.recv()[0])
            if data.get("id") == msg_id:
                if "error" in data:
                    raise CheckoutError(f"Chrome rechazó {method}: {data['error'].get('message')}")
                return data.get("result", {})
        raise CheckoutError(f"Chrome no respondió a {method}")

    def evaluate(self, expression: str, timeout: float = PAGE_TIMEOUT) -> object:
        result = self.call(
            "Runtime.evaluate",
            {"expression": expression, "awaitPromise": True, "returnByValue": True},
            timeout=timeout,
        )
        if "exceptionDetails" in result:
            raise CheckoutError(f"error en la página de Alsa: {result['exceptionDetails'].get('text')}")
        return result.get("result", {}).get("value")

    def wait_for_checkout(self) -> None:
        """Espera a que la pestaña esté en /checkout de alsa.es y cargada (tras la cola de queue-it)."""
        deadline = time.monotonic() + PAGE_TIMEOUT
        while time.monotonic() < deadline:
            try:
                state = self.evaluate("location.host + location.pathname + '|' + document.readyState", timeout=10)
            except CheckoutError:
                state = None  # la página está navegando: el contexto de JS aún no existe
            if state == "www.alsa.es/checkout|complete":
                return
            time.sleep(0.5)
        raise CheckoutError("alsa.es tardó demasiado en cargar")

    def navigate(self, url: str) -> None:
        self.call("Page.navigate", {"url": url})
        time.sleep(1)
        self.wait_for_checkout()

    def close(self) -> None:
        self._ws.close()


def _devtools_port() -> int | None:
    port_file = PROFILE_DIR / "DevToolsActivePort"
    if not port_file.exists():
        return None
    try:
        port = int(port_file.read_text().split()[0])
        curl_requests.get(f"http://127.0.0.1:{port}/json/version", timeout=2)
    except (ValueError, IndexError, curl_requests.RequestsError):
        return None
    return port


def _start_chrome(chrome: str) -> int:
    """Reutiliza el Chrome de alsa-cli si ya está abierto; si no, lo arranca y espera al puerto CDP."""
    if port := _devtools_port():
        return port
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    (PROFILE_DIR / "DevToolsActivePort").unlink(missing_ok=True)
    subprocess.Popen(
        [
            chrome,
            f"--user-data-dir={PROFILE_DIR}",
            "--remote-debugging-port=0",
            "--no-first-run",
            "--no-default-browser-check",
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if port := _devtools_port():
            return port
        time.sleep(0.3)
    raise CheckoutError("Chrome no arrancó con el puerto de depuración")


_SELECT_JS = """
(async () => {
  const base = location.origin + "/checkout?p_p_id=%(portlet)s&p_p_lifecycle=2&p_p_state=normal"
    + "&p_p_mode=view&p_p_cacheability=cacheLevelPage&p_p_resource_id=";
  const get = async (q) => {
    const r = await fetch(base + q, {credentials: "include"});
    if (!r.ok) throw new Error(q.split("&")[0] + " " + r.status);
    return r.json();
  };
  const list = await get("GetJourneyListResource&journeyDirection=I");
  const journey = (list.journeys || []).find(
    (j) => j.departureDate === %(departure)d && String(j.originId) === "%(origin)s");
  if (!journey) return {error: "journey_not_found"};
  const fares = await get("GetPackageFaresResource&journeyIndex=" + journey.index + "&journeyDirection=I");
  const packages = (fares.packageFareList || []).filter((p) => p.enabled);
  const wanted = "%(package)s";
  const chosen = packages.find((p) => p.farePackageId === wanted)
    || packages.slice().sort((a, b) => a.totalPassengerPackagePrice - b.totalPassengerPackagePrice)[0];
  const pkg = chosen ? chosen.farePackageId : "";
  await get("UpdateJourneySelectionResource&outwardJourney=" + journey.index + "&outwardFare=0"
    + "&outwardPackageFare=" + pkg + "&returnJourney=-1&returnFare=-1&returnPackageFare=");
  return {index: journey.index, package: chosen ? chosen.packageName : null};
})()
"""


def open_checkout(selection: Selection, on_step: Callable[[int], None] | None = None) -> str:
    """Prepara la compra en Chrome y deja la pestaña en «Datos». Devuelve el nombre de la tarifa elegida.

    `on_step` recibe 0 (Chrome), 1 (búsqueda), 2 (bus y tarifa) y 3 (datos) al empezar cada paso.
    """
    step = on_step or (lambda n: None)
    chrome = find_chrome()
    if chrome is None:
        raise CheckoutError("no se encontró Chrome, Chromium, Brave ni Edge")
    step(0)
    port = _start_chrome(chrome)
    url = results_url(selection.origin_id, selection.destination_id, selection.date, selection.adults)
    try:
        target = curl_requests.request("PUT", f"http://127.0.0.1:{port}/json/new?{quote(url, safe='')}", timeout=10).json()
    except (curl_requests.RequestsError, ValueError) as e:
        raise CheckoutError(f"no se pudo abrir una pestaña en Chrome: {e}") from e

    tab = _Tab(target["webSocketDebuggerUrl"])
    try:
        tab.call("Page.enable")
        tab.call("Page.bringToFront")
        step(1)
        tab.wait_for_checkout()
        step(2)
        script = _SELECT_JS % {
            "portlet": PORTLET_ID,
            "departure": selection.departure_ms,
            "origin": selection.origin_stop_id,
            "package": selection.package_id or "",
        }
        result = tab.evaluate(script)
        if not isinstance(result, dict) or "error" in result:
            raise CheckoutError("el autobús ya no aparece en alsa.es; puede que se haya agotado")
        step(3)
        tab.navigate(_step_url(2))
        tab.navigate(_step_url(3))
        tab.call("Page.bringToFront")
        return str(result.get("package") or "")
    finally:
        tab.close()
