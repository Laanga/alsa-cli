"""Servidor MCP: expone alsa-cli a asistentes sin terminal.

Una herramienta sobre alsa.core.search_bus — el mismo núcleo que el CLI.
"""

from __future__ import annotations

import sys

try:
    from fastmcp import FastMCP
except ImportError:  # instalado sin el extra mcp
    sys.exit(
        "alsa-mcp necesita el extra mcp. Reinstala con:\n"
        '  uv tool install --force "alsa-cli[mcp] @ git+https://github.com/ahorrandocon20/alsa-cli"'
    )

from alsa.cli import _check_date
from alsa.core import get_station_id
from alsa.core import search_bus as _search_bus_core

mcp = FastMCP("alsa-cli")


@mcp.tool
def search_bus(
    origin: str,
    destination: str,
    date: str,
    adults: int = 1,
) -> dict:
    """Busca buses de Alsa entre dos estaciones en una fecha, con horarios y tarifas.

    Args:
        origin: nombre o ID de la estación de salida (p. ej. "Madrid" o "5555").
        destination: nombre o ID de la estación de llegada (p. ej. "Toledo" o "1690").
        date: fecha del viaje en formato DD/MM/YYYY.
        adults: número de pasajeros adultos (default 1).

    Returns:
        Payload JSON con la lista de buses ordenados por precio,
        cada uno con horarios, duración, transbordos, tipo de servicio
        (Comfort, Supra…) y tarifas desglosadas. El campo shop.url abre
        en alsa.es la lista de buses de esta búsqueda para comprar. Un bloqueo de
        Imperva, una estación desconocida o una fecha mal escrita devuelven
        error de herramienta, no un resultado vacío.
    """
    if error := _check_date(date):
        raise ValueError(error)
    origin_id = origin if origin.isdigit() else get_station_id(origin)
    destination_id = destination if destination.isdigit() else get_station_id(destination)
    journeys, retries, shop_url = _search_bus_core(origin_id, destination_id, date, adults)

    return {
        "origin": origin,
        "destination": destination,
        "date": date,
        "adults": adults,
        "count": len(journeys),
        "buses": [j.to_dict() for j in journeys],
        "shop": {
            "url": shop_url,
            "prefilled": ["origin", "destination", "date", "adults"],
            "next": "La URL abre en alsa.es la lista de buses de esta búsqueda; elige bus y tarifa allí para comprar.",
        },
        "retries": retries,
    }


def main() -> None:
    mcp.run()
