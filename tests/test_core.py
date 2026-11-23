"""Tests offline del parser de journeys: precios, horarios y tipos."""

from zoneinfo import ZoneInfo

from alsa.core import _parse_journeys

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
    assert journeys[0].cheapest_price == float("inf")


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
