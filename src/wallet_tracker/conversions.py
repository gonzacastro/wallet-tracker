"""Deteccion de compras de dolar MEP/CCL disfrazadas de compraventa de titulos.

Para comprar dolares en Argentina se compra un bono en pesos y se vende el
*mismo* bono en dolares uno o dos dias despues. PPI lo informa como dos
movimientos comunes:

    2024-10-01  COMPRA AL30  2.379 nominales   -$1.697.264,71   (Pesos)
    2024-10-02  VENTA  AL30  2.379 nominales       +US$1.378,77 (Dolar MEP)

No es una inversion en AL30: es una conversion de moneda. El bono es el vehiculo
y nunca se tuvo en cartera de verdad. Tratarlo como operacion deja una posicion
fantasma (la pata en pesos nunca se vende) y una venta huerfana (la pata en
dolares nunca se compro), y eso vicia costo, resultado, TIR, TWR y drawdown.

Este modulo aparea las dos patas y las marca como `FX_CONVERSION`. El tipo de
cambio implicito de cada operacion queda guardado en un `FxConversion`: es
informacion valiosa por si misma, es el MEP real que pagaste ese dia.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from typing import Iterable, Sequence

from .ledger import BUY, FX_CONVERSION, SELL, Event
from .money import USD, currency_class, is_ars, normalize_currency

#: Dias corridos maximos entre las dos patas. Las 31 operaciones reales que
#: motivaron esto liquidan entre 1 y 5 dias.
MAX_GAP_DAYS = 7

#: Tolerancia relativa para considerar que dos cantidades son la misma.
QTY_TOLERANCE = 1e-6

#: Para el apareo por ratio (canje entre bolsillos de dolar, ver mas abajo):
#: cuanto pueden diferir los importes de las dos patas.
AMOUNT_TOLERANCE = 0.15

#: Ratio maximo aceptable entre nominales de una pata y la otra.
MAX_RATIO = 1000


@dataclass(frozen=True)
class FxConversion:
    """Una conversion de moneda hecha con un titulo como vehiculo."""

    ticker: str
    from_date: date
    from_currency: str
    from_amount: float          # positivo: lo que salio del bolsillo de origen
    from_quantity: float
    to_date: date
    to_currency: str
    to_amount: float            # positivo: lo que entro al bolsillo de destino
    to_quantity: float
    matched_by: str = "cantidad"

    @property
    def rate(self) -> float:
        """Tipo de cambio implicito: unidades de origen por unidad de destino."""
        return self.from_amount / self.to_amount if self.to_amount else 0.0

    @property
    def ratio(self) -> float:
        """Nominales de la pata de destino por cada nominal de la de origen."""
        return self.to_quantity / self.from_quantity if self.from_quantity else 0.0

    @property
    def days(self) -> int:
        return abs((self.to_date - self.from_date).days)

    @property
    def is_fx_purchase(self) -> bool:
        """True si se compraron dolares con pesos (lo que la gente llama 'MEP')."""
        return is_ars(self.from_currency) and currency_class(self.to_currency) == USD

    @property
    def label(self) -> str:
        return f"{normalize_currency(self.from_currency)} -> {normalize_currency(self.to_currency)}"


def _same_quantity(a: float, b: float) -> bool:
    return abs(a - b) <= QTY_TOLERANCE * max(abs(a), abs(b), 1.0)


def _integer_ratio(a: float, b: float) -> float | None:
    """Ratio entero entre dos cantidades (20 nominales del CEDEAR por 1 del ETF)."""
    if a <= 0 or b <= 0:
        return None
    ratio = max(a, b) / min(a, b)
    rounded = round(ratio)
    if rounded < 2 or rounded > MAX_RATIO:
        return None
    return ratio if abs(ratio - rounded) <= 1e-6 * rounded else None


def _close_amounts(a: float, b: float) -> bool:
    top = max(abs(a), abs(b))
    return bool(top) and abs(a - b) / top <= AMOUNT_TOLERANCE


def _is_candidate(event: Event) -> bool:
    return bool(
        event.category in (BUY, SELL)
        and event.ticker
        and event.quantity > 0
        and event.gross > 0
    )


def _pairs_by_quantity(buy: Event, sell: Event) -> bool:
    """Nivel 1: mismo ticker, misma cantidad, bolsillos distintos.

    Es el caso puro de dolar MEP/CCL: entran y salen exactamente los mismos
    nominales, solo cambia la moneda en la que se liquida.
    """
    return _same_quantity(buy.quantity, sell.quantity)


def _pairs_by_ratio(buy: Event, sell: Event) -> bool:
    """Nivel 2: canje entre bolsillos de dolar con cambio de nominales.

    Pasar dolar cable a dolar MEP se hace comprando el papel afuera y vendiendo
    su CEDEAR aca, y ahi los nominales cambian por el ratio de conversion (7
    unidades de SPY son 140 CEDEARs). Se exige ratio entero exacto y que los dos
    importes esten en dolares y cerca entre si, que es lo que descarta que sean
    dos decisiones de inversion independientes.
    """
    if currency_class(buy.currency) != USD or currency_class(sell.currency) != USD:
        return False
    if _integer_ratio(buy.quantity, sell.quantity) is None:
        return False
    return _close_amounts(buy.gross, sell.gross)


def _match(
    buys: Sequence[tuple[int, Event]],
    sells: Sequence[tuple[int, Event]],
    predicate,
    taken: set[int],
    max_gap_days: int,
) -> list[tuple[int, int]]:
    """Aparea patas de a pares, priorizando las mas cercanas en el tiempo."""
    candidates: list[tuple[int, int, int, date]] = []
    for bi, buy in buys:
        for si, sell in sells:
            gap = abs((sell.date - buy.date).days)
            if gap > max_gap_days:
                continue
            if normalize_currency(buy.currency) == normalize_currency(sell.currency):
                continue
            if not predicate(buy, sell):
                continue
            candidates.append((gap, bi, si, buy.date))

    matched: list[tuple[int, int]] = []
    for _, bi, si, _ in sorted(candidates, key=lambda c: (c[0], c[3], c[1], c[2])):
        if bi in taken or si in taken:
            continue
        taken.add(bi)
        taken.add(si)
        matched.append((bi, si))
    return matched


def pair_fx_conversions(
    events: Iterable[Event],
    *,
    max_gap_days: int = MAX_GAP_DAYS,
    allow_ratio: bool = True,
) -> tuple[list[Event], list[FxConversion]]:
    """Marca como `FX_CONVERSION` las patas que forman una compra de moneda.

    Devuelve la lista completa de eventos (con las patas apareadas reescritas) y
    las conversiones detectadas. Las patas conservan ticker, cantidad e importe:
    el efectivo *si* se movio y la caja tiene que seguir viendolo. Lo que cambia
    es que dejan de generar posicion, costo y resultado.
    """
    events = list(events)
    by_ticker: dict[str, list[int]] = {}
    for index, event in enumerate(events):
        if _is_candidate(event):
            by_ticker.setdefault(event.ticker or "", []).append(index)

    taken: set[int] = set()
    pairs: list[tuple[int, int]] = []
    predicates = [_pairs_by_quantity] + ([_pairs_by_ratio] if allow_ratio else [])

    # Primero el apareo exacto sobre todos los tickers: si una pata calza por
    # cantidad, esa lectura gana sobre cualquier apareo por ratio.
    for predicate in predicates:
        for indices in by_ticker.values():
            buys = [(i, events[i]) for i in indices if events[i].category == BUY and i not in taken]
            sells = [(i, events[i]) for i in indices if events[i].category == SELL and i not in taken]
            if not buys or not sells:
                continue
            pairs += _match(buys, sells, predicate, taken, max_gap_days)

    conversions: list[FxConversion] = []
    for bi, si in pairs:
        buy, sell = events[bi], events[si]
        conversions.append(
            FxConversion(
                ticker=buy.ticker or "",
                from_date=buy.date,
                from_currency=buy.currency,
                from_amount=buy.gross,
                from_quantity=buy.quantity,
                to_date=sell.date,
                to_currency=sell.currency,
                to_amount=sell.gross,
                to_quantity=sell.quantity,
                matched_by="cantidad" if _same_quantity(buy.quantity, sell.quantity) else "ratio",
            )
        )
        events[bi] = replace(buy, category=FX_CONVERSION, matched_rule="conversion de moneda")
        events[si] = replace(sell, category=FX_CONVERSION, matched_rule="conversion de moneda")

    conversions.sort(key=lambda c: c.from_date)
    return events, conversions


def fx_purchases(conversions: Iterable[FxConversion]) -> list[FxConversion]:
    """Solo las que compraron moneda dura con pesos."""
    return [c for c in conversions if c.is_fx_purchase]


def weighted_rate(conversions: Sequence[FxConversion]) -> float | None:
    """Tipo de cambio promedio ponderado por monto (no promedio simple)."""
    spent = sum(c.from_amount for c in conversions)
    bought = sum(c.to_amount for c in conversions)
    return spent / bought if bought else None
