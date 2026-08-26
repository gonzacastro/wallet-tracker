"""Monedas: normalizacion de las etiquetas de PPI y paso a pesos.

PPI no usa codigos ISO. Cada movimiento viene etiquetado con el *bolsillo* al
que entro o del que salio la plata: "Pesos", "Dolar MEP", "Dolar Cable",
"DolarCV7000 Ext.". Dos bolsillos distintos pueden ser la misma moneda (MEP y
cable son ambos dolares), y esa diferencia es justamente la que distingue una
inversion de una compra de dolares.

Aca vive la unica traduccion a pesos del proyecto. Cualquier agregado que sume
importes de varias monedas tiene que pasar por `Converter`: si no, termina
sumando dolares y pesos como si valieran lo mismo.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date
from typing import Protocol

ARS = "ARS"
USD = "USD"

#: Forma canonica del bolsillo en pesos. Todas las variantes colapsan aca.
PESOS = "PESOS"

#: Como llama PPI (y el resto del proyecto) al peso argentino.
ARS_ALIASES = frozenset({"ARS", "$", "PESO", "PESOS", "PESOS ARGENTINOS"})


def _fold(text: str | None) -> str:
    """Mayusculas, sin acentos y con espacios colapsados."""
    if not text:
        return ""
    stripped = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in stripped if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", stripped).strip().upper()


def normalize_currency(label: str | None) -> str:
    """Nombre canonico del bolsillo, comparable entre movimientos.

    Colapsa las variantes del peso a `PESOS` y unifica los sufijos de
    liquidacion ("Dolar Cable - Rescate" y "Dolar Cable" son el mismo bolsillo,
    no dos monedas distintas).
    """
    folded = _fold(label)
    if not folded:
        return PESOS
    folded = re.sub(r"\s*-\s*RESCATE$", "", folded)
    if folded in ARS_ALIASES:
        return PESOS
    return folded


def currency_class(label: str | None) -> str:
    """`ARS` o `USD`. Todo lo que no es peso se trata como dolar."""
    return ARS if normalize_currency(label) == PESOS else USD


def is_ars(label: str | None) -> bool:
    return currency_class(label) == ARS


class RateLookup(Protocol):
    """Lo unico que `Converter` necesita de una serie de tipo de cambio."""

    def get(self, day: date) -> float | None: ...


class Converter:
    """Pasa importes de cualquier bolsillo a pesos con el dolar del dia.

    Los importes en pesos salen intactos. Si falta la cotizacion de un dia se
    devuelve el importe sin convertir y se anota la fecha en `missing`, para
    que el reporte pueda avisar en vez de mentir en silencio.
    """

    def __init__(self, rates: RateLookup | None = None) -> None:
        self._rates = rates
        self.missing: set[date] = set()

    def __bool__(self) -> bool:
        return bool(self._rates)

    def rate(self, day: date) -> float | None:
        return self._rates.get(day) if self._rates else None

    def to_ars(self, amount: float, currency: str | None, day: date) -> float:
        if not amount or is_ars(currency):
            return amount
        rate = self.rate(day)
        if not rate:
            self.missing.add(day)
            return amount
        return amount * rate

    def to_usd(self, amount: float, currency: str | None, day: date) -> float | None:
        """Importe en dolares. `None` si hace falta convertir y no hay cotizacion."""
        if not is_ars(currency):
            return amount
        rate = self.rate(day)
        if not rate:
            self.missing.add(day)
            return None
        return amount / rate
