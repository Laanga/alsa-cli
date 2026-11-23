"""Servidor MCP: expone alsa-cli a asistentes sin terminal.

Una herramienta sobre alsa.core.search_bus — el mismo núcleo que el CLI.
"""

from __future__ import annotations

from fastmcp import FastMCP

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
        (Comfort, Supra…) y tarifas desglosadas. El bus más barato es el
        primero. Un bloqueo de Imperva o una estación desconocida devuelven
        error de herramienta, no un resultado vacío.
    """
    origin_id = origin if origin.isdigit() else get_station_id(origin)
    destination_id = destination if destination.isdigit() else get_station_id(destination)
    journeys = _search_bus_core(origin_id, destination_id, date, adults)

    return {
        "origin": origin,
        "destination": destination,
        "date": date,
        "adults": adults,
        "count": len(journeys),
        "buses": [j.to_dict() for j in journeys],
    }


def main() -> None:
    mcp.run()
