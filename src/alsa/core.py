"""Cliente HTTP para alsa.es: pasa Imperva con curl_cffi y ejecuta la búsqueda de dos pasos.

Flujo (descubierto con ingeniería inversa sobre el HAR real de alsa.es):

1. GET a la home → recoge cookies de Imperva y el token CSRF `p_auth` del HTML.
2. GET a `SearchJourneysAction` con origen, destino y fecha → el portlet Liferay
   guarda la búsqueda en sesión del servidor.
3. GET a `GetJourneyListResource` con `journeyDirection=I` → devuelve el JSON
   con journeys, horarios y precios de la búsqueda en sesión.

Los IDs de estación que usa la búsqueda son los "station IDs" internos de Alsa
(no los "stop IDs" del autocomplete de la home).
"""

from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo

from curl_cffi import requests as curl_requests

from alsa.exceptions import (
    AlsaAuthError,
    AlsaBlockedError,
    AlsaConnectionError,
    AlsaParseError,
)
from alsa.models import Fare, Journey

MADRID_TZ = ZoneInfo("Europe/Madrid")

BASE_URL = "https://www.alsa.es"
PORTLET_ID = "com_babel_alsa_espania_purchase_web_PurchasePortlet"
PORTLET_NS = f"_{PORTLET_ID}"
REQUEST_TIMEOUT = 60  # segundos


def search_bus(
    origin_station_id: str,
    destination_station_id: str,
    travel_date: str,
    adults: int = 1,
) -> list[Journey]:
    """Busca buses entre dos estaciones de Alsa en una fecha.

    Args:
        origin_station_id: ID de la estación de salida (p. ej. "5555" = Barajas T4).
        destination_station_id: ID de la estación de llegada.
        travel_date: fecha en formato "DD/MM/YYYY".
        adults: número de pasajeros adultos.

    Returns:
        Lista de journeys ordenados por precio (más barato primero).

    Raises:
        AlsaBlockedError: Imperva bloqueó la petición.
        AlsaConnectionError: no se pudo conectar.
        AlsaParseError: la respuesta no se pudo interpretar.
        AlsaAuthError: el token CSRF no funcionó.
    """
    session = curl_requests.Session()
    session.impersonate = "chrome"

    # --- paso 0: cookies de Imperva + p_auth de cualquier página -------------
    try:
        home = session.get(BASE_URL, timeout=REQUEST_TIMEOUT)
    except Exception as e:
        raise AlsaConnectionError(str(e)) from e

    if home.status_code in (403, 429):
        raise AlsaBlockedError()

    auth_match = re.search(r'p_auth=([A-Za-z0-9_-]+)', home.text)
    if not auth_match:
        raise AlsaParseError("no se encontró p_auth en la respuesta de la home")
    p_auth = auth_match.group(1)

    # --- paso 1: SearchJourneysAction — establece la búsqueda en sesión -------
    search_url = (
        f"{BASE_URL}/checkout"
        f"?p_p_id={PORTLET_ID}"
        f"&p_p_lifecycle=1&p_p_state=normal&p_p_mode=view"
        f"&{PORTLET_NS}_javax.portlet.action=SearchJourneysAction"
        f"&p_auth={p_auth}"
        f"&alsaspam="
        f"&originStationId={origin_station_id}"
        f"&destinationStationId={destination_station_id}"
        f"&departureDate={travel_date}"
        f"&travelType=OUTWARD"
        f"&passengerType-1={adults}"
        f"&seats={adults}"
        f"&employee=false"
    )

    try:
        search_resp = session.get(search_url, timeout=REQUEST_TIMEOUT)
    except Exception as e:
        raise AlsaConnectionError(str(e)) from e

    if search_resp.status_code in (403, 429):
        raise AlsaBlockedError()

    # verificar que la búsqueda se registró: la página de checkout debe contener
    # el ID de la estación de origen en algún params del portlet
    if origin_station_id not in search_resp.text:
        raise AlsaAuthError(f"p_auth={p_auth[:8]}… posible sesión caducada")

    # --- paso 2: GetJourneyListResource — devuelve el JSON de resultados -----
    list_url = (
        f"{BASE_URL}/checkout"
        f"?p_p_id={PORTLET_ID}"
        f"&p_p_lifecycle=2&p_p_state=normal&p_p_mode=view"
        f"&p_p_resource_id=GetJourneyListResource"
        f"&p_p_cacheability=cacheLevelPage"
        f"&journeyDirection=I"
    )

    try:
        list_resp = session.get(list_url, timeout=REQUEST_TIMEOUT)
    except Exception as e:
        raise AlsaConnectionError(str(e)) from e

    if list_resp.status_code in (403, 429):
        raise AlsaBlockedError()

    if not list_resp.text.strip():
        return []  # sin resultados, no es error

    import json

    try:
        data = json.loads(list_resp.text)
    except json.JSONDecodeError as e:
        raise AlsaParseError(f"JSON inválido en GetJourneyListResource: {e}") from e

    journeys = _parse_journeys(data)
    journeys.sort(key=lambda j: j.cheapest_price)
    return journeys


def _parse_journeys(data: dict) -> list[Journey]:
    """Convierte el JSON crudo de Alsa en una lista de Journey."""
    raw = data.get("journeys") or []
    journeys: list[Journey] = []

    for rj in raw:
        try:
            dep_ms = rj.get("departureDate")
            arr_ms = rj.get("arrivalDate")
            if not dep_ms or not arr_ms:
                continue

            departure = datetime.fromtimestamp(dep_ms / 1000, tz=MADRID_TZ)
            arrival = datetime.fromtimestamp(arr_ms / 1000, tz=MADRID_TZ)
            duration = int((arrival - departure).total_seconds() / 60)

            fares = []
            for rf in rj.get("fares") or []:
                price = rf.get("price")
                if price is None or price <= 0:
                    continue
                fares.append(
                    Fare(
                        price=float(price),
                        fare_name=rf.get("fareName", ""),
                        fare_type=rf.get("fareType", ""),
                        fare_conditions=rf.get("fareConditions", ""),
                    )
                )

            service_types = rj.get("serviceTypeDescList") or [""]
            journeys.append(
                Journey(
                    origin_id=str(rj.get("originId", "")),
                    origin_name=rj.get("originName", ""),
                    destination_id=str(rj.get("destinationId", "")),
                    destination_name=rj.get("destinationName", ""),
                    departure=departure,
                    arrival=arrival,
                    duration_minutes=duration,
                    transfers=int(rj.get("transfersNumber", 0)),
                    service_type=service_types[0],
                    fares=fares,
                    line_name=rj.get("lineName", ""),
                )
            )
        except (KeyError, ValueError, TypeError):
            continue  # un journey malformado no tira la lista entera

    return journeys


def get_stations(query: str = "") -> list[dict]:
    """Busca estaciones de Alsa por nombre (autocomplete del endpoint público).

    Args:
        query: texto parcial del nombre de la estación (p. ej. "mad", "tole").

    Returns:
        Lista de dicts con id, name, province, simplified_name.
    """
    session = curl_requests.Session()
    session.impersonate = "chrome"

    origins_url = (
        f"{BASE_URL}/home"
        f"?p_p_id=com_babel_alsa_espania_journeysearch_web_JourneySearchPortlet_INSTANCE_21651890"
        f"&p_p_lifecycle=2&p_p_state=normal&p_p_mode=view"
        f"&p_p_resource_id=GetOriginsResourceCommand"
        f"&p_p_cacheability=cacheLevelPage"
    )

    try:
        resp = session.post(origins_url, data={"text": query}, timeout=REQUEST_TIMEOUT)
    except Exception as e:
        raise AlsaConnectionError(str(e)) from e

    if resp.status_code in (403, 429):
        raise AlsaBlockedError()

    import json

    try:
        stations = json.loads(resp.text)
    except json.JSONDecodeError:
        return []

    # filtrar solo los campos útiles
    result = []
    for st in stations:
        result.append(
            {
                "id": st.get("id", ""),
                "name": st.get("name", ""),
                "province": st.get("provincecountryName", ""),
                "simplified_name": st.get("simplifiedName", ""),
                "icon": st.get("icon", ""),
            }
        )
    return result


def get_station_id(name: str) -> str:
    """Encuentra el ID de una estación por su nombre.

    Returns:
        El ID de la primera coincidencia.

    Raises:
        AlsaStationError: si no se encuentra ninguna estación.
    """
    stations = get_stations(name.lower()[:20])
    if not stations:
        from alsa.exceptions import AlsaStationError

        raise AlsaStationError(
            f"No se encontró ninguna estación que encaje con {name!r}. "
            "Prueba con otro nombre o usa `alsa stations <texto>` para buscar."
        )
    return stations[0]["id"]
