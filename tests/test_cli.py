"""Tests offline del comando `alsa bus`: validación, JSON de salida y errores por stderr."""

import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from typer.testing import CliRunner

from alsa import cli
from alsa.exceptions import AlsaBlockedError
from alsa.models import Fare, Journey

TZ = ZoneInfo("Europe/Madrid")

JOURNEY = Journey(
    origin_id="155",
    origin_name="Madrid Estación Sur",
    destination_id="186",
    destination_name="Oviedo / Uviéu",
    departure=datetime(2026, 12, 21, 0, 30, tzinfo=TZ),
    arrival=datetime(2026, 12, 21, 6, 15, tzinfo=TZ),
    duration_minutes=345,
    transfers=0,
    service_type="Comfort",
    fares=[Fare(42.15, "SUPER FLEX", "BASE", "Puedes anular y cambiar")],
)
RETRIES = [{"request": "home", "tries": 2, "errors": ["server error (503)"]}]


@pytest.fixture
def fake_search(monkeypatch):
    calls: dict[str, list] = {"station": [], "search": []}

    def station_id(name: str) -> str:
        calls["station"].append(name)
        return {"Madrid": "90155", "Oviedo": "90186"}[name]

    def search(*args):
        calls["search"].append(args)
        return [JOURNEY], RETRIES, "https://alsa.test/shop"

    monkeypatch.setattr(cli, "get_station_id", station_id)
    monkeypatch.setattr(cli, "search_bus", search)
    return calls


def _run(*args: str):
    return CliRunner().invoke(cli.app, list(args))


def test_bus_json_tiene_buses_enlace_y_reintentos(fake_search):
    result = _run("bus", "Madrid", "Oviedo", "-d", "21/12/2026", "--adults", "2", "--format", "json")

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert fake_search["search"] == [("90155", "90186", "21/12/2026", 2)]
    assert (payload["origin"], payload["destination"], payload["date"], payload["adults"]) == (
        "Madrid",
        "Oviedo",
        "21/12/2026",
        2,
    )
    assert payload["count"] == 1
    assert payload["buses"][0]["cheapest_price_eur"] == 42.15
    assert payload["buses"][0]["departure"] == "2026-12-21T00:30:00+01:00"
    assert payload["shop"]["url"] == "https://alsa.test/shop"
    assert payload["retries"] == RETRIES


def test_bus_con_id_numerico_no_busca_la_estacion(fake_search):
    result = _run("bus", "5555", "Oviedo", "-d", "21/12/2026", "--format", "json")

    assert result.exit_code == 0
    assert fake_search["station"] == ["Oviedo"]
    assert fake_search["search"][0][:2] == ("5555", "90186")


def test_bus_texto_por_defecto(fake_search):
    result = _run("bus", "Madrid", "Oviedo", "-d", "21/12/2026")

    assert result.exit_code == 0
    assert "00:30 → 06:15" in result.stdout and "comprar: https://alsa.test/shop" in result.stdout


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["-d", "2026-12-21"], "DD/MM/YYYY"),
        (["-d", "21/12/2026", "--format", "xml"], "text o json"),
    ],
)
def test_bus_argumentos_invalidos_son_error_por_stderr(fake_search, args, message):
    result = _run("bus", "Madrid", "Oviedo", *args)

    assert result.exit_code == 1
    assert result.stdout == ""
    assert message in json.loads(result.stderr)["error"]["message"]
    assert fake_search["search"] == []


def test_bus_error_de_alsa_sale_por_stderr(fake_search, monkeypatch):
    def blocked(*args):
        raise AlsaBlockedError()

    monkeypatch.setattr(cli, "search_bus", blocked)
    result = _run("bus", "Madrid", "Oviedo", "-d", "21/12/2026", "--format", "json")

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "Imperva" in json.loads(result.stderr)["error"]["message"]
