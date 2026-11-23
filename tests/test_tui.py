"""Tests offline de la TUI: autocompletado, filtros, formato y el flujo completo con datos falsos."""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from typer.testing import CliRunner

import alsa.tui.app as app_module
from alsa.cli import app as cli_app
from alsa.exceptions import AlsaBlockedError
from alsa.models import Fare, Journey
from alsa.tui import fmt, screens
from alsa.tui.screens import (
    BuyScreen,
    PurchaseScreen,
    ResultsScreen,
    SearchRequest,
    matches_time,
)
from alsa.tui.stations import StationIndex, StationOption

TZ = ZoneInfo("Europe/Madrid")

STATIONS = [
    {"id": "90155", "name": "Madrid (Todas las paradas)", "province": "Madrid, España", "icon": "ui-city"},
    {"id": "6302", "name": "Madrid-Chamartin", "province": "Madrid, España", "icon": "ui-city"},
    {"id": "50080", "name": "Madridejos", "province": "España", "icon": "ui-city"},
    {"id": "50081", "name": "Madroñera", "province": "Caceres, España", "icon": "ui-city"},
    {"id": "10578", "name": "Aeropuerto Madrid - Barajas T1", "province": "Madrid, España", "icon": "ui-airport-city"},
    {"id": "90186", "name": "Oviedo / Uviéu (Todas las paradas)", "province": "Asturias, España", "icon": "ui-city"},
    {"id": "1307", "name": "Facultades Oviedo", "province": "Asturias, España", "icon": "ui-city"},
]


def _journey(hhmm: str, minutes: int, price: float, transfers: int = 0) -> Journey:
    hour, minute = map(int, hhmm.split(":"))
    departure = datetime(2026, 12, 5, hour, minute, tzinfo=TZ)
    return Journey(
        origin_id="90155",
        origin_name="Madrid Estación Sur",
        destination_id="90186",
        destination_name="Oviedo / Uviéu",
        departure=departure,
        arrival=departure + timedelta(minutes=minutes),
        duration_minutes=minutes,
        transfers=transfers,
        service_type="Comfort",
        fares=[Fare(price, "FLEX", "FLX", "Puedes anular y cambiar")],
    )


JOURNEYS = [_journey("08:15", 345, 42.15), _journey("14:00", 330, 34.0), _journey("21:30", 315, 44.5, 1)]


def _req(**changes) -> SearchRequest:
    index = StationIndex(STATIONS)
    base = SearchRequest(
        origin=index.search("madrid")[0],
        destination=index.search("oviedo")[0],
        day=date.today(),
        adults=1,
        depart_after=None,
        arrive_before=None,
    )
    return SearchRequest(**{**base.__dict__, **changes})


# --- autocompletado -----------------------------------------------------------


def test_search_pone_primero_el_grupo_de_la_ciudad():
    results = StationIndex(STATIONS).search("mad")
    assert results[0].label == "Madrid (todas)"
    assert results[1].name == "Madrid-Chamartin"


def test_search_ignora_acentos_y_mayusculas():
    results = StationIndex(STATIONS).search("MADRONERA")
    assert [r.name for r in results] == ["Madroñera"]


def test_search_encuentra_por_palabra_interior():
    results = StationIndex(STATIONS).search("barajas")
    assert results[0].is_airport


def test_search_vacio_no_sugiere_nada():
    assert StationIndex(STATIONS).search("   ") == []


def test_label_de_grupo_es_corto():
    option = StationOption("1", "Gijón / Xixón (Todas las paradas)", "Asturias", True, False)
    assert option.label == "Gijón / Xixón (todas)"


# --- filtros y formato ----------------------------------------------------------


def test_filtro_salir_desde():
    req = _req(depart_after="09:00")
    assert [matches_time(j, req) for j in JOURNEYS] == [False, True, True]


def test_filtro_llegar_antes_descarta_los_que_llegan_otro_dia():
    req = _req(arrive_before="23:30")
    assert [matches_time(j, req) for j in JOURNEYS] == [True, True, False]


def test_formato_espanol():
    assert fmt.price(1234.5) == "1.234,50 €"
    assert fmt.duration(345) == "5h 45"
    assert fmt.duration(45) == "45 min"
    assert fmt.long_date(date(2026, 12, 5)) == "sábado 5 de diciembre"
    late = JOURNEYS[2]
    assert fmt.arrival(late.departure, late.arrival) == "02:45 +1"


# --- CLI ------------------------------------------------------------------------


def test_alsa_sin_terminal_muestra_la_ayuda_y_no_abre_la_tui():
    result = CliRunner().invoke(cli_app, [])
    assert result.exit_code == 0
    assert "bus" in result.output and "stations" in result.output


# --- flujo completo con Textual -------------------------------------------------


@pytest.fixture
def fake_alsa(monkeypatch):
    opened: list[str] = []
    monkeypatch.setattr(app_module, "get_all_stations", lambda: STATIONS)
    monkeypatch.setattr(screens, "search_bus", lambda o, d, day, adults: (list(JOURNEYS), [], f"https://alsa.test/{o}-{d}"))
    monkeypatch.setattr(screens.webbrowser, "open", lambda url, new=2: opened.append(url) or True)
    return opened


async def _wait_for(pilot, condition, timeout=5.0):
    for _ in range(int(timeout / 0.05)):
        if condition():
            return
        await pilot.pause(0.05)
    raise AssertionError("la condición no se cumplió a tiempo")


async def test_flujo_completo_hasta_abrir_alsa(fake_alsa):
    app = app_module.AlsaApp()
    async with app.run_test(size=(110, 40)) as pilot:
        await _wait_for(pilot, lambda: app.stations is not None)
        await pilot.press(*"mad", "enter", *"ovi", "enter")
        await pilot.press("enter")  # «Hoy» → foco en Buscar
        await pilot.press("enter")  # Buscar
        await _wait_for(pilot, lambda: isinstance(app.screen, ResultsScreen) and app.screen.phase == "list")
        results = app.screen
        assert results.req.origin.id == "90155"
        assert results.req.destination.id == "90186"
        assert [j.departure.strftime("%H:%M") for j in results.shown] == ["08:15", "14:00", "21:30"]

        await pilot.press("down", "enter")
        await _wait_for(pilot, lambda: isinstance(app.screen, BuyScreen))
        assert app.screen.journey.departure.strftime("%H:%M") == "14:00"
        await pilot.press("enter")
        await _wait_for(pilot, lambda: isinstance(app.screen, PurchaseScreen))
        assert fake_alsa == ["https://alsa.test/90155-90186"]

        await pilot.press("escape")
        await _wait_for(pilot, lambda: isinstance(app.screen, ResultsScreen))


async def test_ordenar_por_precio_mantiene_el_bus_seleccionado(fake_alsa):
    app = app_module.AlsaApp()
    async with app.run_test(size=(110, 40)) as pilot:
        await _wait_for(pilot, lambda: app.stations is not None)
        app.push_screen(ResultsScreen(_req()))
        await _wait_for(pilot, lambda: isinstance(app.screen, ResultsScreen) and app.screen.phase == "list")
        await pilot.press("down", "down", "o")
        results = app.screen
        assert [j.cheapest_price for j in results.shown] == [34.0, 42.15, 44.5]
        assert results._current().departure.strftime("%H:%M") == "21:30"


async def test_buscar_sin_paradas_avisa_en_el_formulario(fake_alsa):
    app = app_module.AlsaApp()
    async with app.run_test(size=(110, 40)) as pilot:
        app.screen.submit()
        await pilot.pause()
        assert "salida" in str(app.screen.query_one("#form-error").render())
        assert not isinstance(app.screen, ResultsScreen)


async def test_filtro_sin_resultados_explica_por_que(fake_alsa):
    app = app_module.AlsaApp()
    async with app.run_test(size=(110, 40)) as pilot:
        app.push_screen(ResultsScreen(_req(depart_after="23:00")))
        await _wait_for(pilot, lambda: isinstance(app.screen, ResultsScreen) and app.screen.phase == "message")
        await pilot.press("t")
        await _wait_for(pilot, lambda: app.screen.phase == "list")
        assert len(app.screen.shown) == 3


async def test_error_de_alsa_se_muestra_y_se_puede_reintentar(fake_alsa, monkeypatch):
    def blocked(*args):
        raise AlsaBlockedError()

    monkeypatch.setattr(screens, "search_bus", blocked)
    app = app_module.AlsaApp()
    async with app.run_test(size=(110, 40)) as pilot:
        app.push_screen(ResultsScreen(_req()))
        await _wait_for(pilot, lambda: isinstance(app.screen, ResultsScreen) and app.screen.phase == "message")
        assert "Imperva" in str(app.screen.query_one("#message-text").render())

        monkeypatch.setattr(screens, "search_bus", lambda *a: (list(JOURNEYS), [], "https://alsa.test"))
        await pilot.press("r")
        await _wait_for(pilot, lambda: app.screen.phase == "list")
