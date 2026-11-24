"""Tests offline del servidor MCP: misma respuesta que `alsa bus --format json` y errores de herramienta."""

import json
from datetime import timedelta

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError
from test_cli import DAY, JOURNEY, RETRIES, TODAY
from typer.testing import CliRunner

from alsa import cli, mcp

ARGS = {"origin": "Madrid", "destination": "Oviedo", "date": DAY, "adults": 2}


@pytest.fixture
def fake_search(monkeypatch):
    calls: list[tuple] = []

    def search(*args):
        calls.append(args)
        return [JOURNEY], RETRIES, "https://alsa.test/shop"

    station_id = {"Madrid": "90155", "Oviedo": "90186"}.__getitem__
    for module, search_name in ((cli, "search_bus"), (mcp, "_search_bus_core")):
        monkeypatch.setattr(module, "get_station_id", station_id)
        monkeypatch.setattr(module, search_name, search)
    return calls


async def _call(args: dict) -> dict:
    async with Client(mcp.mcp) as client:
        result = await client.call_tool("search_bus", args)
    return result.structured_content


async def test_mcp_responde_lo_mismo_que_el_cli(fake_search):
    from_mcp = await _call(ARGS)
    cli_result = CliRunner().invoke(cli.app, ["bus", "Madrid", "Oviedo", "-d", DAY, "-a", "2", "--format", "json"])

    assert from_mcp == json.loads(cli_result.stdout)
    assert fake_search[0] == ("90155", "90186", DAY, 2)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"date": "2026-12-21"}, "DD/MM/YYYY"),
        ({"date": (TODAY - timedelta(days=1)).strftime("%d/%m/%Y")}, "ya ha pasado"),
        ({"adults": 0}, "de 1 a 20"),
        ({"adults": 21}, "de 1 a 20"),
    ],
)
async def test_mcp_rechaza_fecha_o_adultos_invalidos(fake_search, changes, message):
    with pytest.raises(ToolError, match=message):
        await _call({**ARGS, **changes})
    assert fake_search == []


async def test_mcp_error_de_alsa_es_error_de_herramienta(fake_search, monkeypatch):
    def unknown(name):
        raise cli.AlsaError("No se encontró ninguna estación que encaje con 'Atlantis'.")

    monkeypatch.setattr(mcp, "get_station_id", unknown)
    with pytest.raises(ToolError, match="Atlantis"):
        await _call({**ARGS, "origin": "Atlantis"})
