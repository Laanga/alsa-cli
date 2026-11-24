"""Tests offline del parser de journeys y de los reintentos de las peticiones a alsa.es."""

from zoneinfo import ZoneInfo

import pytest
from curl_cffi.requests.exceptions import ConnectionError as CurlConnectionError
from curl_cffi.requests.exceptions import Timeout

from alsa import core
from alsa.core import _parse_journeys, _request_with_retries
from alsa.exceptions import AlsaBlockedError, AlsaConnectionError, AlsaTimeoutError

TZ = ZoneInfo("Europe/Madrid")

# JSON capturado del endpoint real GetJourneyListResource de alsa.es
# (simplificado, estructura fiel a la respuesta del 2026-11-24)
SAMPLE = {
    "journeys": [
        {
            "index": 0,
            "departureDate": 1795479300000,
            "arrivalDate": 1795498380000,
            "departureTime": "01:15",
            "arrivalTime": "06:33",
            "originId": "5555",
            "originName": "Aeropuerto Madrid - Barajas T4",
            "destinationId": "3100",
            "destinationName": "Pamplona",
            "transfersNumber": 1,
            "serviceTypeDescList": ["Comfort", "Comfort"],
            "lineName": "",
            "fares": [
                {
                    "price": 26.5,
                    "grossPrice": 36.22,
                    "fareType": "FLX",
                    "fareName": "Tarifa Flex",
                    "fareConditions": "Puedes anular y cambiar",
                    "travellersData": [
                        {"passengerType": "ADULT", "amount": 1, "price": 26.5}
                    ],
                }
            ],
        },
        {
            "index": 1,
            "departureDate": 1795485300000,
            "arrivalDate": 1795502100000,
            "departureTime": "09:15",
            "arrivalTime": "15:35",
            "originId": "5555",
            "originName": "Aeropuerto Madrid - Barajas T4",
            "destinationId": "3100",
            "destinationName": "Pamplona",
            "transfersNumber": 1,
            "serviceTypeDescList": ["Supra"],
            "fares": [
                {"price": 43.63, "fareType": "SFL", "fareName": "SUPER FLEX", "fareConditions": "Cambio y anulacion"}
            ],
        },
    ],
    "isRoundTrip": False,
    "countCanBeSold": 2,
}


def test_parse_extrae_journeys_con_precio():
    journeys = _parse_journeys(SAMPLE)

    assert len(journeys) == 2
    assert journeys[0].origin_name == "Aeropuerto Madrid - Barajas T4"
    assert journeys[0].destination_name == "Pamplona"
    assert journeys[0].cheapest_price == 26.50
    assert journeys[0].service_type == "Comfort"


def test_parse_lee_horarios_y_duracion():
    journeys = _parse_journeys(SAMPLE)

    j = journeys[0]
    assert j.departure.hour == 1  # epoch 1795479300000 = 01:15 en Europe/Madrid
    assert j.arrival > j.departure
    assert j.duration_minutes > 0
    assert j.duration_minutes == (j.arrival - j.departure).total_seconds() / 60


def test_parse_ordena_por_precio():
    # el segundo journey es más caro → _parse_journeys no ordena, pero
    # search_bus sí: aquí verificamos que los datos están para ordenar
    journeys = _parse_journeys(SAMPLE)

    prices = [j.cheapest_price for j in journeys]
    assert 26.50 in prices
    assert 43.63 in prices


def test_parse_journey_sin_fares_no_revierta():
    data = {
        "journeys": [
            {
                "departureDate": 1795479300000,
                "arrivalDate": 1795498380000,
                "originId": "1",
                "originName": "A",
                "destinationId": "2",
                "destinationName": "B",
                "transfersNumber": 0,
                "serviceTypeDescList": ["Comfort"],
                "fares": [],
            }
        ]
    }
    journeys = _parse_journeys(data)

    assert len(journeys) == 1
    assert journeys[0].fares == []
    assert journeys[0].cheapest_price == float("inf")  # al final al ordenar por precio
    assert journeys[0].to_dict()["cheapest_price_eur"] is None  # JSON válido, no Infinity


def test_parse_vacio_devuelve_lista_vacia():
    assert _parse_journeys({}) == []
    assert _parse_journeys({"journeys": []}) == []


def test_journey_to_dict_y_from_dict_roundtrip():
    j = _parse_journeys(SAMPLE)[0]
    d = j.to_dict()
    j2 = type(j).from_dict(d)

    assert j2.origin_id == j.origin_id
    assert j2.cheapest_price == j.cheapest_price
    assert j2.duration_minutes == j.duration_minutes


def test_parse_marca_agotados_con_canbesold_f():
    import copy

    data = copy.deepcopy(SAMPLE)
    data["journeys"][0]["canBeSold"] = "F"
    data["journeys"][1]["canBeSold"] = "V"
    journeys = _parse_journeys(data)
    assert [j.available for j in journeys] == [False, True]
    assert journeys[0].to_dict()["available"] is False


class FakeResponse:
    def __init__(self, status_code: int, text: str = "ok"):
        self.status_code = status_code
        self.text = text


class FakeSession:
    """Devuelve (o lanza) las respuestas en orden y cuenta cuántas peticiones recibe."""

    def __init__(self, *outcomes: FakeResponse | Exception):
        self.outcomes = list(outcomes)
        self.calls = 0

    def get(self, *args, **kwargs) -> FakeResponse:
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.fixture(autouse=True)
def sin_pausas(monkeypatch):
    monkeypatch.setattr(core, "RETRY_Pauses", (0, 0))


def test_reintento_que_se_recupera_queda_en_retries():
    session = FakeSession(FakeResponse(503), CurlConnectionError("reset"), FakeResponse(200))
    log: list[dict] = []

    resp = _request_with_retries(session, "GET", "https://alsa.test", "home", log)

    assert resp.status_code == 200
    assert log == [
        {"request": "home", "tries": 3, "errors": ["server error (503)", "connection dropped (ConnectionError)"]}
    ]


def test_tres_errores_de_servidor_paran_con_error():
    session = FakeSession(FakeResponse(503), FakeResponse(502), FakeResponse(500))

    with pytest.raises(AlsaConnectionError, match="falló tras 3 intentos"):
        _request_with_retries(session, "GET", "https://alsa.test", "home", [])
    assert session.calls == 3


def test_timeout_no_se_reintenta():
    session = FakeSession(Timeout("lento"), FakeResponse(200))

    with pytest.raises(AlsaTimeoutError):
        _request_with_retries(session, "GET", "https://alsa.test", "home", [])
    assert session.calls == 1


def test_pagina_de_bloqueo_de_imperva_es_un_bloqueo():
    blocked = FakeResponse(200, "<html>Request unsuccessful. Incapsula incident ID: 123</html>")
    session = FakeSession(blocked, FakeResponse(200))

    with pytest.raises(AlsaBlockedError):
        _request_with_retries(session, "GET", "https://alsa.test", "home", [])
    assert session.calls == 1


def test_403_es_un_bloqueo_sin_reintento():
    session = FakeSession(FakeResponse(403), FakeResponse(200))

    with pytest.raises(AlsaBlockedError):
        _request_with_retries(session, "GET", "https://alsa.test", "home", [])
    assert session.calls == 1


def test_respuesta_a_la_primera_no_anota_reintentos():
    session = FakeSession(FakeResponse(200))
    log: list[dict] = []

    _request_with_retries(session, "GET", "https://alsa.test", "home", log)

    assert log == []
