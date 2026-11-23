"""Modelos de datos: estaciones, journeys y fares de Alsa."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Station:
    """Una parada o estación de Alsa."""

    id: str
    name: str
    province: str
    simplified_name: str = ""
    icon: str = ""
    is_movelia: bool = False


@dataclass(frozen=True)
class Fare:
    """Una tarifa de un journey."""

    price: float
    fare_name: str
    fare_type: str  # "FLX", "STD", etc.
    fare_conditions: str


@dataclass(frozen=True)
class Journey:
    """Un bus de Alsa entre dos puntos, con sus tarifas."""

    origin_id: str
    origin_name: str
    destination_id: str
    destination_name: str
    departure: datetime
    arrival: datetime
    duration_minutes: int
    transfers: int
    service_type: str  # "Comfort", "Supra", etc.
    fares: list[Fare]
    line_name: str = ""

    @property
    def cheapest_price(self) -> float:
        return min(f.price for f in self.fares) if self.fares else float("inf")

    def to_dict(self) -> dict:
        return {
            "origin_id": self.origin_id,
            "origin_name": self.origin_name,
            "destination_id": self.destination_id,
            "destination_name": self.destination_name,
            "departure": self.departure.isoformat(),
            "arrival": self.arrival.isoformat(),
            "duration_minutes": self.duration_minutes,
            "transfers": self.transfers,
            "service_type": self.service_type,
            "line_name": self.line_name,
            "cheapest_price_eur": round(self.cheapest_price, 2),
            "fares": [
                {
                    "price": f.price,
                    "fare_name": f.fare_name,
                    "fare_type": f.fare_type,
                    "fare_conditions": f.fare_conditions,
                }
                for f in self.fares
            ],
        }

    @classmethod
    def from_dict(cls, d: dict) -> Journey:

        return cls(
            origin_id=d["origin_id"],
            origin_name=d["origin_name"],
            destination_id=d["destination_id"],
            destination_name=d["destination_name"],
            departure=datetime.fromisoformat(d["departure"]),
            arrival=datetime.fromisoformat(d["arrival"]),
            duration_minutes=d["duration_minutes"],
            transfers=d["transfers"],
            service_type=d["service_type"],
            fares=[Fare(**f) for f in d.get("fares", [])],
            line_name=d.get("line_name", ""),
        )
