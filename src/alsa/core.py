"""Cliente HTTP para alsa.es: pasa Imperva con curl_cffi y ejecuta la búsqueda de dos pasos.

Flujo (descubierto con ingeniería inversa sobre el HAR real de alsa.es):

1. GET a la home → recoge cookies de Imperva y el token CSRF `p_auth` del HTML.
2. GET a `SearchJourneysAction` con origen, destino y fecha → el portlet Liferay
   guarda la búsqueda en sesión del servidor.
3. GET a `GetJourneyListResource` con `journeyDirection=I` → devuelve el JSON
   con journeys, horarios y precios de la búsqueda en sesión.

Reintentos (mismo patrón que renfe-cli): hasta 3 intentos por petición,
1 s y 2 s de pausa, solo para errores 5xx y conexiones caídas. Nunca para
bloqueos (403/429), timeouts ni páginas HTML de Imperva. Cada reintento
queda en la lista `retries` de la respuesta.
"""

from __future__ import annotations

import json
import re
import time
import unicodedata
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
RETRY_Pauses = (1.0, 2.0)  # segundos antes del segundo y tercer intento
RETRY_STATUSES = (500, 502, 503, 504)


def _request_with_retries(
    session: curl_requests.Session,
    method: str,
    url: str,
    label: str,
    retries_log: list[dict],
    **kwargs,
) -> curl_requests.Response:
    """Hace una petición con hasta 3 intentos, igual que renfe-cli.

    Reintenta solo errores 5xx y conexiones caídas; bloqueos (403/429),
    timeouts y páginas de bot paran inmediatamente.
    """
    errors: list[str] = []

    for attempt in range(1, len(RETRY_Pauses) + 2):
        try:
            if method == "GET":
                resp = session.get(url, timeout=REQUEST_TIMEOUT, **kwargs)
            else:
                resp = session.post(url, timeout=REQUEST_TIMEOUT, **kwargs)

            if resp.status_code in (403, 429):
                raise AlsaBlockedError()
            if resp.status_code in RETRY_STATUSES and attempt <= len(RETRY_Pauses):
                errors.append(f"server error ({resp.status_code})")
                if attempt < len(RETRY_Pauses) + 1:
                    time.sleep(RETRY_Pauses[attempt - 1])
                continue
            return resp

        except AlsaBlockedError:
            raise  # un bloqueo nunca se reintenta
        except Exception as e:
            if attempt <= len(RETRY_Pauses):
                errors.append(f"connection dropped ({type(e).__name__})")
                if attempt < len(RETRY_Pauses) + 1:
                    time.sleep(RETRY_Pauses[attempt - 1])
                continue
            raise AlsaConnectionError(str(e)) from e

    # si llegamos aquí, agotamos los reintentos
    raise AlsaConnectionError(f"{label} falló tras {len(RETRY_Pauses) + 1} intentos")


def search_bus(
    origin_station_id: str,
    destination_station_id: str,
    travel_date: str,
    adults: int = 1,
) -> tuple[list[Journey], list[dict], str]:
    """Busca buses entre dos estaciones de Alsa en una fecha.

    Returns:
        Tupla de (journeys ordenados por precio, lista de reintentos, shop_url).

    Raises:
        AlsaBlockedError: Imperva bloqueó la petición.
        AlsaConnectionError: no se pudo conectar tras reintentos.
        AlsaParseError: la respuesta no se pudo interpretar.
        AlsaAuthError: el token CSRF no funcionó.
    """
    retries: list[dict] = []
    session = curl_requests.Session()
    session.impersonate = "chrome"

    # --- paso 0: cookies de Imperva + p_auth de cualquier página -------------
    home = _request_with_retries(session, "GET", BASE_URL, "home", retries)

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

    search_resp = _request_with_retries(session, "GET", search_url, "search", retries)

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

    list_resp = _request_with_retries(session, "GET", list_url, "journey list", retries)

    if not list_resp.text.strip():
        return [], retries, _shop_url(origin_station_id, destination_station_id, travel_date, adults)

    try:
        data = json.loads(list_resp.text)
    except json.JSONDecodeError as e:
        raise AlsaParseError(f"JSON inválido en GetJourneyListResource: {e}") from e

    journeys = _parse_journeys(data)
    journeys.sort(key=lambda j: j.cheapest_price)

    shop = _shop_url(origin_station_id, destination_station_id, travel_date, adults)
    return journeys, retries, shop


def _shop_url(origin_id: str, destination_id: str, date: str, adults: int) -> str:
    """URL que abre la web de Alsa con la ruta precargada para comprar."""
    return (
        f"{BASE_URL}/checkout"
        f"?originStationId={origin_id}"
        f"&destinationStationId={destination_id}"
        f"&departureDate={date}"
        f"&travelType=OUTWARD"
        f"&passengerType-1={adults}"
        f"&seats={adults}"
    )


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
    """Busca estaciones de Alsa por nombre (filtrado local, como su web)."""
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
        resp = session.post(origins_url, data={"origen": query}, timeout=REQUEST_TIMEOUT)
    except Exception as e:
        raise AlsaConnectionError(str(e)) from e

    if resp.status_code in (403, 429):
        raise AlsaBlockedError()

    try:
        all_stations = json.loads(resp.text)
    except json.JSONDecodeError:
        return []

    def _norm(text: str) -> str:
        decomposed = unicodedata.normalize("NFD", text.casefold())
        return "".join(ch for ch in decomposed if not unicodedata.combining(ch))

    query_norm = _norm(query)
    result = []
    for st in all_stations:
        if query_norm and query_norm not in _norm(st.get("name", "")):
            continue
        result.append(
            {
                "id": st.get("id", ""),
                "name": st.get("name", ""),
                "province": st.get("provincecountryName", ""),
                "simplified_name": st.get("simplifiedName", ""),
                "icon": st.get("icon", ""),
            }
        )
        if len(result) >= 20:
            break

    return result


def get_all_stations() -> list[dict]:
    """Descarga la lista completa de estaciones de Alsa, sin filtrar ni recortar."""
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
        resp = session.post(origins_url, data={"origen": ""}, timeout=REQUEST_TIMEOUT)
    except Exception as e:
        raise AlsaConnectionError(str(e)) from e

    if resp.status_code in (403, 429):
        raise AlsaBlockedError()

    try:
        all_stations = json.loads(resp.text)
    except json.JSONDecodeError:
        raise AlsaParseError("la lista de estaciones no se pudo leer") from None

    return [
        {
            "id": str(st.get("id", "")),
            "name": st.get("name", ""),
            "province": st.get("provincecountryName", ""),
            "icon": st.get("icon", ""),
        }
        for st in all_stations
        if st.get("id") and st.get("name")
    ]


def get_station_id(name: str) -> str:
    """Encuentra el ID de una estación de Alsa por su nombre.

    Descarga la lista completa (una petición) y filtra localmente.
    Prioriza match exacto, luego agrupación de ciudad, luego startsWith.
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
        resp = session.post(origins_url, data={"origen": ""}, timeout=REQUEST_TIMEOUT)
    except Exception as e:
        raise AlsaConnectionError(str(e)) from e

    if resp.status_code in (403, 429):
        raise AlsaBlockedError()

    try:
        all_stations = json.loads(resp.text)
    except json.JSONDecodeError:
        raise AlsaParseError("la lista de estaciones no se pudo leer") from None

    def _norm(text: str) -> str:
        decomposed = unicodedata.normalize("NFD", text.casefold())
        return "".join(ch for ch in decomposed if not unicodedata.combining(ch))

    name_norm = _norm(name)

    # 1: match exacto (ignorando acentos y mayúsculas)
    for st in all_stations:
        if _norm(st.get("name", "")) == name_norm:
            return st["id"]

    # 2: agrupación "(Todas las paradas)" que contenga el nombre
    for st in all_stations:
        st_name = _norm(st.get("name", ""))
        if "todas las paradas" in st_name.lower() and name_norm in st_name:
            return st["id"]

    # 3: el nombre EMPIEZA por la búsqueda
    for st in all_stations:
        if _norm(st.get("name", "")).startswith(name_norm):
            return st["id"]

    # 4: el nombre CONTIENE la búsqueda
    for st in all_stations:
        if name_norm in _norm(st.get("name", "")):
            return st["id"]

    from alsa.exceptions import AlsaStationError

    raise AlsaStationError(
        f"No se encontró ninguna estación que encaje con {name!r}. "
        "Prueba con otro nombre o usa `alsa stations <texto>` para buscar."
    )
