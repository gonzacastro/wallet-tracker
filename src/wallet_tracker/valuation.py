"""Valuacion diaria de la cartera: serie de NAV en pesos y en dolares.

Cruza las tenencias reconstruidas del ledger con la serie de precios historicos
bajada de PPI y con el dolar implicito, para poder medir la evolucion dia a dia.
"""

from __future__ import annotations

import sqlite3
from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterable, Sequence

from .conversions import FxConversion
from .ledger import BUY, OPENING, RATIO_CHANGE, SELL, Event
from .money import Converter, is_ars


class PriceBook:
    """Precios por ticker con relleno hacia atras (ultimo precio conocido)."""

    def __init__(self, series: dict[str, list[tuple[date, float]]]) -> None:
        self._dates: dict[str, list[date]] = {}
        self._values: dict[str, list[float]] = {}
        for ticker, points in series.items():
            ordered = sorted((d, p) for d, p in points if p)
            if not ordered:
                continue
            self._dates[ticker] = [d for d, _ in ordered]
            self._values[ticker] = [p for _, p in ordered]

    @classmethod
    def from_db(cls, conn: sqlite3.Connection) -> "PriceBook":
        series: dict[str, list[tuple[date, float]]] = defaultdict(list)
        for row in conn.execute("SELECT ticker, date, price FROM prices WHERE price > 0"):
            series[row["ticker"]].append((date.fromisoformat(row["date"]), float(row["price"])))
        return cls(series)

    @property
    def tickers(self) -> set[str]:
        return set(self._dates)

    def get(self, ticker: str, day: date) -> float | None:
        dates = self._dates.get(ticker)
        if not dates:
            return None
        idx = bisect_right(dates, day)
        if idx == 0:
            return None
        return self._values[ticker][idx - 1]

    def last(self, ticker: str) -> tuple[date, float] | None:
        dates = self._dates.get(ticker)
        if not dates:
            return None
        return dates[-1], self._values[ticker][-1]

    def series(self, ticker: str, start: date | None = None, end: date | None = None) -> list[tuple[date, float]]:
        """Serie completa de un ticker, opcionalmente acotada."""
        dates = self._dates.get(ticker)
        if not dates:
            return []
        values = self._values[ticker]
        return [
            (d, p)
            for d, p in zip(dates, values)
            if (start is None or d >= start) and (end is None or d <= end)
        ]

    def apply_ratio_changes(self, actions: Iterable) -> None:
        """Divide los precios anteriores a un canje para empalmar la serie.

        Un cambio de ratio multiplica los nominales y divide el precio de un dia
        para el otro, sin que pase nada economico. La serie cruda queda con un
        escalon que no es una caida: sin corregirlo, el grafico muestra un
        precipicio y cualquier comparacion que cruce esa fecha da un disparate
        (SPY llego a medir -62% cuando en realidad habia subido).

        Se ajusta al leer y no al bajar los precios: la base guarda lo que
        informa PPI, y declarar un canje nuevo no obliga a re-sincronizar.

        OJO: esto es para graficar y comparar contra el precio de hoy, NO para
        valuar. La serie de valuacion cruza cantidad y precio *de cada dia*, y
        `quantities_by_day` ya escala los nominales en la fecha del canje:
        ajustar tambien los precios lo contaria dos veces y meteria un salto
        que nunca ocurrio.
        """
        for action in actions:
            dates = self._dates.get(action.ticker)
            if not dates or action.ratio <= 0:
                continue
            values = self._values[action.ticker]
            for i, day in enumerate(dates):
                if day < action.date:
                    values[i] /= action.ratio

    def first_date(self, ticker: str) -> date | None:
        dates = self._dates.get(ticker)
        return dates[0] if dates else None


class FxBook:
    """Serie diaria del dolar implicito (CCL o MEP)."""

    def __init__(self, points: Iterable[tuple[date, float]]) -> None:
        ordered = sorted((d, v) for d, v in points if v)
        self._dates = [d for d, _ in ordered]
        self._values = [v for _, v in ordered]

    @classmethod
    def from_db(cls, conn: sqlite3.Connection) -> "FxBook":
        rows = conn.execute("SELECT date, ccl FROM fx WHERE ccl > 0").fetchall()
        return cls((date.fromisoformat(r["date"]), float(r["ccl"])) for r in rows)

    def __bool__(self) -> bool:
        return bool(self._dates)

    def get(self, day: date) -> float | None:
        if not self._dates:
            return None
        idx = bisect_right(self._dates, day)
        if idx == 0:
            return self._values[0]
        return self._values[idx - 1]


@dataclass
class NavPoint:
    date: date
    instruments_ars: float
    cash_ars: float
    ccl: float | None
    in_transit_ars: float = 0.0   # plata en el medio de una conversion de moneda

    @property
    def nav_ars(self) -> float:
        return self.instruments_ars + self.cash_ars + self.in_transit_ars

    @property
    def nav_usd(self) -> float | None:
        if not self.ccl:
            return None
        return self.nav_ars / self.ccl


def _daterange(start: date, end: date) -> Iterable[date]:
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def cash_balances(events: Sequence[Event]) -> dict[str, list[tuple[date, float]]]:
    """Saldo de caja por moneda a lo largo del tiempo.

    El saldo que PPI informa en cada movimiento (`balance`) se usa una sola vez,
    en el primer movimiento de cada moneda, para saber con cuanta plata arranca
    el historial (puede haber fondos anteriores a `PPI_HISTORY_START`). De ahi
    en mas se acumulan los importes.

    Es a proposito que no se tome el `balance` de cada fila: dentro de un mismo
    dia PPI los informa por fecha de liquidacion, no en el orden en que ocurren,
    asi que la ultima fila del dia puede traer un saldo que no es el de cierre.
    Un dia con seis compras llego a informar $4.025.584 cuando el cierre real
    era $4.537. Acumular reproduce exactamente la foto de tenencias del broker.
    """
    running: dict[str, float] = {}
    series: dict[str, dict[date, float]] = defaultdict(dict)
    for event in sorted(events, key=lambda e: (e.date, e.ordinal)):
        # Se agrupa por la etiqueta cruda de PPI, no por moneda: "Dolar Cable" y
        # "Dolar Cable - Rescate" son dos cuentas con saldos independientes.
        currency = (event.currency or "ARS").upper()
        if currency not in running:
            running[currency] = (event.balance - event.cash_flow) if event.balance else 0.0
        running[currency] += event.cash_flow
        series[currency][event.date] = running[currency]
    return {cur: sorted(points.items()) for cur, points in series.items()}


def quantities_by_day(events: Sequence[Event]) -> dict[date, dict[str, float]]:
    """Tenencia acumulada de cada especie, solo en los dias en que cambia."""
    snapshots: dict[date, dict[str, float]] = {}
    running: dict[str, float] = defaultdict(float)
    for event in sorted(events, key=lambda e: (e.date, 0 if e.category in (BUY, OPENING) else 1)):
        if not event.ticker:
            continue
        if event.category == RATIO_CHANGE and event.ratio > 0:
            # Un canje o cambio de ratio no es una operacion: cambia la cantidad
            # de nominales sin que entre ni salga plata.
            if running.get(event.ticker):
                running[event.ticker] *= event.ratio
                snapshots[event.date] = {t: q for t, q in running.items() if abs(q) > 1e-9}
            continue
        if not event.quantity:
            continue
        if event.category in (BUY, OPENING):
            running[event.ticker] += event.quantity
        elif event.category == SELL:
            running[event.ticker] -= event.quantity
        else:
            continue
        snapshots[event.date] = {t: q for t, q in running.items() if abs(q) > 1e-9}
    return snapshots


def in_transit_by_day(
    conversions: Sequence[FxConversion], converter: Converter | None = None
) -> dict[date, float]:
    """Plata que esta viajando entre dos bolsillos, por dia y en pesos.

    Una compra de dolar MEP tarda uno a tres dias: los pesos ya salieron de la
    cuenta y los dolares todavia no llegaron. En el medio la plata existe -- es
    el bono, que esta comprado -- pero no aparece ni en la caja ni en las
    tenencias. Sin contarla, un aporte que entra y se convierte el mismo dia
    hace que la cartera parezca haberse evaporado, y el TWR se vuelve delirante.
    """
    converter = converter or Converter()
    out: dict[date, float] = defaultdict(float)
    for conversion in conversions:
        day = min(conversion.from_date, conversion.to_date)
        last = max(conversion.from_date, conversion.to_date)
        while day < last:
            out[day] += converter.to_ars(
                conversion.from_amount, conversion.from_currency, day
            )
            day += timedelta(days=1)
    return dict(out)


def external_flows_by_day(
    events: Sequence[Event], fx: FxBook | Converter | None = None
) -> dict[date, float]:
    """Aportes/retiros externos por dia, expresados en pesos.

    Los retiros en dolares son la mitad de esta cartera: sumarlos sin convertir
    haria que un retiro de US$4.000 pese lo mismo que uno de $4.000.
    """
    converter = fx if isinstance(fx, Converter) else Converter(fx)
    out: dict[date, float] = defaultdict(float)
    for event in events:
        if not event.is_external_flow:
            continue
        out[event.date] += converter.to_ars(event.cash_flow, event.currency, event.date)
    return dict(out)


def build_nav_series(
    events: Sequence[Event],
    prices: PriceBook,
    fx: FxBook | None = None,
    instrument_currency: dict[str, str] | None = None,
    *,
    conversions: Sequence[FxConversion] | None = None,
    start: date | None = None,
    end: date | None = None,
    step_days: int = 1,
) -> list[NavPoint]:
    """Serie de valuacion diaria de toda la cartera.

    Las tenencias vienen del ledger, los precios del historico de PPI y las
    posiciones nominadas en dolares se pasan a pesos con el CCL del dia.
    """
    if not events:
        return []
    instrument_currency = {k.upper(): (v or "ARS").upper() for k, v in (instrument_currency or {}).items()}
    start = start or min(e.date for e in events)
    end = end or date.today()

    qty_changes = quantities_by_day(events)
    change_dates = sorted(qty_changes)
    cash = cash_balances(events)
    cash_dates = {cur: [d for d, _ in points] for cur, points in cash.items()}
    in_transit = in_transit_by_day(conversions or [], Converter(fx))

    series: list[NavPoint] = []
    holdings: dict[str, float] = {}
    change_idx = 0

    for day in _daterange(start, end):
        while change_idx < len(change_dates) and change_dates[change_idx] <= day:
            holdings = qty_changes[change_dates[change_idx]]
            change_idx += 1

        rate = fx.get(day) if fx else None

        instruments_ars = 0.0
        for ticker, quantity in holdings.items():
            price = prices.get(ticker, day)
            if price is None:
                continue
            value = quantity * price
            if not is_ars(instrument_currency.get(ticker)) and rate:
                value *= rate
            instruments_ars += value

        cash_ars = 0.0
        for currency, points in cash.items():
            idx = bisect_right(cash_dates[currency], day)
            if idx == 0:
                continue
            balance = points[idx - 1][1]
            if not is_ars(currency):
                balance = balance * rate if rate else 0.0
            cash_ars += balance

        series.append(NavPoint(day, instruments_ars, cash_ars, rate, in_transit.get(day, 0.0)))

    if step_days > 1:
        series = series[::step_days] + series[-1:]
    return series
