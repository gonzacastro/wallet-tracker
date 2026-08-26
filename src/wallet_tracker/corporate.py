"""Acciones societarias: cambios de ratio, splits y canjes declarados a mano.

Un CEDEAR puede cambiar su ratio de conversion de un dia para el otro: los
nominales se multiplican y el precio se divide, sin que haya ninguna operacion
en la cuenta. Para el FIFO eso es invisible -- sigue creyendo que tenes los
nominales viejos contra el precio nuevo -- y la especie aparece con una perdida
enorme que nunca ocurrio.

No hay forma confiable de inferirlo desde la descripcion de un movimiento, asi
que el flujo es: el codigo *detecta* la discrepancia contra la foto de tenencias
que informa PPI y avisa; vos confirmas el ratio en `corporate_actions.json`.

    [
      {"ticker": "SPY", "date": "2026-05-29", "ratio": 3,
       "note": "cambio de ratio del CEDEAR 1:3"}
    ]

`ratio` son los nominales nuevos por cada nominal viejo: 3 = te dan 3 por 1.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Iterable, Sequence

from .ledger import RATIO_CHANGE, Event
from .lots import FifoResult

#: Diferencia relativa a partir de la cual se avisa que algo no concilia.
RECONCILE_TOLERANCE = 0.005


@dataclass(frozen=True)
class CorporateAction:
    ticker: str
    date: date
    ratio: float
    note: str = ""

    def as_event(self) -> Event:
        return Event(
            uid=f"ca:{self.ticker}:{self.date.isoformat()}",
            date=self.date,
            category=RATIO_CHANGE,
            description=self.note or f"Cambio de ratio {self.ratio:g}:1",
            currency="",
            cash_flow=0.0,
            ticker=self.ticker,
            ratio=self.ratio,
            matched_rule="corporate_actions.json",
        )


def load_corporate_actions(path: str | Path | None) -> list[CorporateAction]:
    if not path:
        return []
    file = Path(path)
    if not file.exists():
        return []
    data = json.loads(file.read_text(encoding="utf-8"))
    actions = [
        CorporateAction(
            ticker=str(item["ticker"]).upper(),
            date=date.fromisoformat(str(item["date"])),
            ratio=float(item["ratio"]),
            note=str(item.get("note") or ""),
        )
        for item in data
        if item.get("ticker") and item.get("date") and float(item.get("ratio") or 0) > 0
    ]
    actions.sort(key=lambda a: (a.date, a.ticker))
    return actions


def apply_corporate_actions(
    events: Sequence[Event], actions: Iterable[CorporateAction]
) -> list[Event]:
    """Inyecta las acciones declaradas como eventos en la linea de tiempo.

    Se inyectan en vez de reescalar el historial entero para que la serie de
    valuacion siga siendo correcta *a cada fecha*: antes del canje, los
    nominales viejos contra el precio viejo; despues, los nuevos contra el nuevo.
    """
    injected = [a.as_event() for a in actions]
    if not injected:
        return list(events)
    return sorted([*events, *injected], key=lambda e: (e.date, e.ordinal))


#: Ultima foto *de cada cuenta*. Con mas de un broker en la misma base, el
#: maximo global se quedaria con la del que sincronizo ultimo y daria por
#: vacias todas las tenencias del otro.
LATEST_PER_ACCOUNT = (
    "ts = (SELECT MAX(ts) FROM snapshots s2 WHERE s2.account_number = s.account_number)"
)


def snapshot_quantities(conn: sqlite3.Connection) -> dict[str, float]:
    """Nominales de cada especie segun la ultima foto de tenencias de cada broker."""
    return {
        row["ticker"]: float(row["quantity"] or 0.0)
        for row in conn.execute(
            "SELECT ticker, SUM(quantity) AS quantity FROM snapshots s "
            f"WHERE kind='instrument' AND ticker IS NOT NULL AND {LATEST_PER_ACCOUNT} "
            "GROUP BY ticker"
        )
    }


def reconcile(fifo: FifoResult, broker: dict[str, float]) -> list[str]:
    """Compara lo que calcula el FIFO contra lo que informa el broker.

    Es la red de seguridad para todo lo que el historial no alcanza a explicar:
    canjes, cambios de ratio o compras anteriores al inicio del historial. Si el
    desvio es un multiplo limpio sugiere directamente la linea de configuracion.
    """
    computed: dict[str, float] = {}
    for holding in fifo.holdings.values():
        computed[holding.ticker] = computed.get(holding.ticker, 0.0) + holding.quantity

    messages: list[str] = []
    for ticker in sorted(set(broker) | set(computed)):
        theirs = broker.get(ticker, 0.0)
        ours = computed.get(ticker, 0.0)
        if abs(theirs - ours) <= RECONCILE_TOLERANCE * max(abs(theirs), abs(ours), 1.0):
            continue
        detail = f"{ticker}: el broker informa {theirs:,.2f} nominales y el historial da {ours:,.2f}"
        if ours > 0 and theirs > 0:
            ratio = theirs / ours
            if abs(ratio - round(ratio)) <= 0.01 and round(ratio) > 1:
                detail += (
                    f" (ratio {round(ratio)}:1 -> si fue un canje o split, declaralo en "
                    f'corporate_actions.json: {{"ticker": "{ticker}", "date": "AAAA-MM-DD", '
                    f'"ratio": {round(ratio)}}})'
                )
            else:
                detail += f" (factor {ratio:,.4f})"
        messages.append(detail)
    return messages
