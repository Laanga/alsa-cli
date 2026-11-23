# alsa-cli

Busca horarios y tarifas de autobuses Alsa desde el terminal. JSON-first, sin cuenta, sin API key.

```bash
# instalar
uv tool install git+https://github.com/ahorrandocon20/alsa-cli

# buscar buses
alsa bus Madrid Toledo --date 21/12/2026

# con formato JSON (para scripts y agentes)
alsa bus Madrid Toledo -d 21/12/2026 --format json

# buscar estaciones
alsa stations "mad"

# servidor MCP para asistentes
pipx install "alsa-cli[mcp]"
alsa-mcp
```

> **Unofficial.** No afiliado, respaldado ni soportado por Alsa. El CLI habla
> directamente con la API web de alsa.es, de forma anónima, igual que lo hace
> el navegador antes de que nadie inicie sesión. Esa API no está documentada
> y puede cambiar sin previo aviso. Alsa es una marca de sus respectivos
> propietarios.

## Cómo funciona

1. Obtiene cookies de Imperva (bot protection) y el token CSRF `p_auth` del HTML de alsa.es, con `curl_cffi` impersonando Chrome.
2. Envía la búsqueda al `SearchJourneysAction` del portlet Liferay (un GET con `originStationId`, `destinationStationId`, `departureDate` y pasajeros).
3. Recupera los resultados del `GetJourneyListResource` — un JSON con cada bus: horarios, duración, transbordos, tipo de servicio (Comfort/Supra) y todas las tarifas con precio.

## Qué devuelve

```json
{
  "origin": "Madrid",
  "destination": "Toledo",
  "date": "21/12/2026",
  "count": 2,
  "buses": [
    {
      "origin_id": "5555",
      "origin_name": "Aeropuerto Madrid - Barajas T4",
      "destination_name": "Toledo",
      "departure": "2026-12-21T01:15:00+01:00",
      "arrival": "2026-12-21T06:33:00+01:00",
      "duration_minutes": 318,
      "transfers": 1,
      "service_type": "Comfort",
      "cheapest_price_eur": 26.5,
      "fares": [
        {"price": 26.5, "fare_name": "Tarifa Flex", "fare_type": "FLX"}
      ]
    }
  ]
}
```

## Aviso

Los precios son los que alsa.es publicaba en el momento de la búsqueda.
Confirma el precio, equipaje y condiciones en la web de Alsa antes de comprar.

## Desarrollo

```bash
uv sync
uv run pytest -q
uvx ruff check .
```
