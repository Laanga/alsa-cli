"""Formato en español para fechas, horas, precios y duraciones."""

from __future__ import annotations

from datetime import date, datetime

WEEKDAYS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MONTHS = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]


def long_date(d: date) -> str:
    return f"{WEEKDAYS[d.weekday()]} {d.day} de {MONTHS[d.month - 1]}"


def short_date(d: date) -> str:
    return f"{WEEKDAYS[d.weekday()][:3]} {d.day} {MONTHS[d.month - 1][:3]}"


def price(value: float) -> str:
    return f"{value:,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")


def duration(minutes: int) -> str:
    hours, mins = divmod(minutes, 60)
    if not hours:
        return f"{mins} min"
    return f"{hours}h {mins:02d}"


def hm(dt: datetime) -> str:
    return dt.strftime("%H:%M")


def arrival(departure: datetime, arrival_dt: datetime) -> str:
    """Hora de llegada con «+1» si llega otro día."""
    days = (arrival_dt.date() - departure.date()).days
    return hm(arrival_dt) + (f" +{days}" if days else "")


def transfers(count: int) -> str:
    if count == 0:
        return "directo"
    return f"{count} transbordo" + ("s" if count > 1 else "")


def travellers(count: int) -> str:
    return f"{count} viajero" + ("s" if count > 1 else "")
