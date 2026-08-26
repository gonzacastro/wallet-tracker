"""A donde mandar el proximo aporte para no desarmar tu plan.

Esto NO predice que instrumento va a subir. Es aritmetica contra un objetivo que
definis vos: si queres que tu cartera se parezca a cierto reparto y tenes plata
nueva para poner, calcula cuanto va a cada especie para acercarte a ese reparto
*comprando solamente*, sin vender nada.

Es la forma barata de rebalancear cuando aportas todos los meses: en vez de
vender lo que subio para comprar lo que bajo (con comisiones e impuestos de por
medio), dirigis la plata nueva a lo que quedo corto.

El objetivo va en `objetivo.json` en la raiz del proyecto:

    [
      { "ticker": "SPY",  "objetivo": 25, "grupo": "base" },
      { "ticker": "JPM",  "objetivo": 20, "grupo": "apuestas" }
    ]

Los pesos se normalizan solos, asi que podes escribirlos en porcentaje, en
partes o como quieras. `grupo` es opcional y solo sirve para agrupar en la
salida (por ejemplo: la parte amplia de la cartera y las apuestas puntuales).
Sin archivo, el objetivo es partes iguales entre lo que ya tenes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

#: Diferencia por debajo de la cual no vale la pena mover plata.
MIN_ALLOCATION = 1.0


@dataclass(frozen=True)
class Target:
    """Cuanto queres que pese cada especie en tu cartera."""

    ticker: str
    weight: float             # ya normalizado: suma 1 entre todos
    group: str = ""


@dataclass
class Allocation:
    """Que hacer con una especie al repartir un aporte."""

    ticker: str
    group: str
    current: float            # plata que tenes hoy en esa especie
    target_weight: float
    amount: float             # cuanto ponerle de este aporte

    @property
    def final(self) -> float:
        return self.current + self.amount

    def current_weight(self, total: float) -> float:
        return self.current / total if total else 0.0

    def final_weight(self, total: float) -> float:
        return self.final / total if total else 0.0

    def units(self, price: float | None) -> float | None:
        """Cuantos nominales compra el monto asignado al precio de hoy.

        Se compran papeles enteros, asi que el monto exacto es orientativo: lo
        accionable es la cantidad.
        """
        return (self.amount / price) if price else None


def load_targets(path: str | Path | None) -> list[Target]:
    """Lee `objetivo.json`. Devuelve vacio si no existe."""
    if not path:
        return []
    file = Path(path)
    if not file.exists():
        return []
    data = json.loads(file.read_text(encoding="utf-8"))
    raw = [
        (str(item["ticker"]).upper(), float(item.get("objetivo") or 0), str(item.get("grupo") or ""))
        for item in data
        if item.get("ticker") and float(item.get("objetivo") or 0) > 0
    ]
    total = sum(w for _, w, _ in raw)
    if not total:
        return []
    return [Target(ticker, weight / total, group) for ticker, weight, group in raw]


def equal_weights(tickers: Iterable[str]) -> list[Target]:
    """Objetivo por defecto: partes iguales entre lo que ya tenes."""
    tickers = sorted(set(tickers))
    if not tickers:
        return []
    share = 1.0 / len(tickers)
    return [Target(ticker, share) for ticker in tickers]


def allocate(
    holdings: dict[str, float], targets: Sequence[Target], amount: float
) -> list[Allocation]:
    """Reparte `amount` para acercar la cartera al objetivo, sin vender nada.

    Primero se cubre lo que le falta a cada especie para llegar a su peso
    objetivo sobre la cartera *ya ampliada* por el aporte. Si el aporte no
    alcanza para cubrir todo, se reparte a prorrata de lo que le falta a cada
    una: la mas atrasada recibe mas. Si sobra despues de emparejar a todas, el
    resto se reparte segun los pesos objetivo.
    """
    if not targets or amount <= 0:
        return []

    final_total = sum(holdings.values()) + amount
    rows = [
        Allocation(
            ticker=t.ticker,
            group=t.group,
            current=holdings.get(t.ticker, 0.0),
            target_weight=t.weight,
            amount=0.0,
        )
        for t in targets
    ]

    shortfalls = {r.ticker: max(0.0, r.target_weight * final_total - r.current) for r in rows}
    needed = sum(shortfalls.values())

    if needed <= amount:
        # Alcanza para emparejar a todas: se cubre el faltante y el sobrante se
        # reparte segun el objetivo, que deja la cartera exactamente en el plan.
        leftover = amount - needed
        for row in rows:
            row.amount = shortfalls[row.ticker] + leftover * row.target_weight
    elif needed:
        # No alcanza: cada una recibe en proporcion a lo atrasada que esta.
        for row in rows:
            row.amount = amount * shortfalls[row.ticker] / needed

    for row in rows:
        if row.amount < MIN_ALLOCATION:
            row.amount = 0.0

    rows.sort(key=lambda r: -r.amount)
    return rows


def drift(holdings: dict[str, float], targets: Sequence[Target]) -> list[tuple[str, float, float]]:
    """Cuanto se corrio cada especie de su objetivo: (ticker, actual, objetivo)."""
    total = sum(holdings.values())
    out = [
        (t.ticker, (holdings.get(t.ticker, 0.0) / total if total else 0.0), t.weight)
        for t in targets
    ]
    out.sort(key=lambda item: item[1] - item[2])
    return out
