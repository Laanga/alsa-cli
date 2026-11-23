"""Búsqueda de estaciones para el autocompletado, filtrando la lista completa en local."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

GROUP_SUFFIX = "(todas las paradas)"


def norm(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text.casefold())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


@dataclass(frozen=True)
class StationOption:
    id: str
    name: str
    province: str
    is_group: bool
    is_airport: bool

    @property
    def label(self) -> str:
        """Nombre corto para el campo: «Madrid (todas)»."""
        if self.is_group:
            return f"{self.name[: -len(GROUP_SUFFIX)].strip()} (todas)"
        return self.name


def _province(raw: str) -> str:
    province = raw.removesuffix(", España").strip()
    return "" if province == "España" else province


class StationIndex:
    def __init__(self, stations: list[dict]):
        self._items: list[tuple[StationOption, str, list[str]]] = []
        for st in stations:
            name = st["name"]
            n = norm(name)
            option = StationOption(
                id=str(st["id"]),
                name=name,
                province=_province(st.get("province", "")),
                is_group=n.endswith(GROUP_SUFFIX),
                is_airport=st.get("icon") == "ui-airport-city",
            )
            base = n.removesuffix(GROUP_SUFFIX).strip()
            self._items.append((option, base, re.split(r"[^a-z0-9]+", base)))

    def __len__(self) -> int:
        return len(self._items)

    def search(self, query: str, limit: int = 8) -> list[StationOption]:
        q = norm(query).strip()
        if not q:
            return []
        tokens = [t for t in re.split(r"[^a-z0-9]+", q) if t]
        # Las paradas de una ciudad con grupo («Madrid-Chamartin») van justo detrás del grupo.
        cities = [base for option, base, _ in self._items if option.is_group and base.startswith(q)]
        scored: list[tuple[int, str, StationOption]] = []
        for option, base, words in self._items:
            if base == q:
                score = 0 if option.is_group else 1
            elif option.is_group and base.startswith(q):
                score = 2
            elif any(base.startswith(city) for city in cities):
                score = 3
            elif base.startswith(q):
                score = 4
            elif tokens and all(any(w.startswith(t) for w in words) for t in tokens):
                score = 5
            elif q in base:
                score = 6
            else:
                continue
            scored.append((score, base, option))
        scored.sort(key=lambda item: item[:2])
        return [option for *_, option in scored[:limit]]
