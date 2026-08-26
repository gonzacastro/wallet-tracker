"""Motor FIFO: reconstruye lotes, costo promedio y resultado realizado.

A partir de los eventos del ledger arma, para cada especie, la pila de compras
pendientes de venta (lotes). Con eso se sabe *desde cuando* tenes cada cosa,
a que precio la compraste y cuanta ganancia realizaste al vender.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import date
from typing import Iterable, Sequence

from .ledger import (
    ADJUSTMENT,
    AMORTIZATION,
    BUY,
    FEE,
    INCOME,
    NON_POSITION,
    OPENING,
    RATIO_CHANGE,
    SELL,
    TAX,
    Event,
)


@dataclass
class Lot:
    """Una compra pendiente (total o parcialmente) de venta."""

    ticker: str
    currency: str
    open_date: date
    quantity: float
    unit_cost: float
    synthetic: bool = False  # generado por una venta sin compra previa conocida

    @property
    def cost(self) -> float:
        return self.quantity * self.unit_cost


@dataclass
class ClosedTrade:
    """Una compra apareada con su venta."""

    ticker: str
    currency: str
    open_date: date
    close_date: date
    quantity: float
    unit_cost: float
    unit_proceeds: float
    synthetic_cost: bool = False

    @property
    def cost(self) -> float:
        return self.quantity * self.unit_cost

    @property
    def proceeds(self) -> float:
        return self.quantity * self.unit_proceeds

    @property
    def pnl(self) -> float:
        return self.proceeds - self.cost

    @property
    def pnl_pct(self) -> float:
        return (self.pnl / self.cost) if self.cost else 0.0

    @property
    def holding_days(self) -> int:
        return (self.close_date - self.open_date).days


@dataclass
class Holding:
    """Estado consolidado de una especie."""

    ticker: str
    currency: str
    quantity: float = 0.0
    cost_basis: float = 0.0
    first_buy: date | None = None
    last_buy: date | None = None
    last_activity: date | None = None
    realized_pnl: float = 0.0
    income: float = 0.0            # dividendos, rentas, amortizaciones cobradas
    fees: float = 0.0              # comisiones e impuestos atribuibles a la especie
    total_bought: float = 0.0      # plata puesta historicamente
    total_sold: float = 0.0        # plata recuperada historicamente
    buy_count: int = 0
    sell_count: int = 0
    lots: list[Lot] = field(default_factory=list)

    @property
    def avg_cost(self) -> float:
        return self.cost_basis / self.quantity if self.quantity else 0.0

    @property
    def is_open(self) -> bool:
        return self.quantity > 1e-9

    @property
    def holding_days(self) -> int | None:
        """Dias desde la primera compra que sigue viva en cartera."""
        if not self.lots:
            return None
        return (date.today() - min(lot.open_date for lot in self.lots)).days

    @property
    def weighted_holding_days(self) -> float | None:
        """Antiguedad promedio ponderada por plata invertida en cada lote."""
        if not self.lots:
            return None
        total_cost = sum(lot.cost for lot in self.lots)
        if not total_cost:
            return None
        today = date.today()
        return sum(lot.cost * (today - lot.open_date).days for lot in self.lots) / total_cost

    def market_value(self, price: float | None) -> float | None:
        return None if price is None else self.quantity * price

    def unrealized_pnl(self, price: float | None) -> float | None:
        value = self.market_value(price)
        return None if value is None else value - self.cost_basis

    def total_pnl(self, price: float | None) -> float | None:
        unreal = self.unrealized_pnl(price)
        if unreal is None:
            return None
        return unreal + self.realized_pnl + self.income - self.fees


@dataclass
class FifoResult:
    holdings: dict[tuple[str, str], Holding]
    closed: list[ClosedTrade]
    warnings: list[str]
    portfolio_fees: float = 0.0
    portfolio_income: float = 0.0

    def open_holdings(self) -> list[Holding]:
        return sorted(
            (h for h in self.holdings.values() if h.is_open),
            key=lambda h: -h.cost_basis,
        )

    def closed_holdings(self) -> list[Holding]:
        return sorted(
            (h for h in self.holdings.values() if not h.is_open),
            key=lambda h: (h.last_activity or date.min),
            reverse=True,
        )

    def by_ticker(self, ticker: str) -> list[Holding]:
        return [h for h in self.holdings.values() if h.ticker == ticker.upper()]


def _unit_price(event: Event) -> float:
    """Precio unitario efectivo: prioriza el importe real sobre el precio informado."""
    if event.quantity:
        if event.gross:
            return event.gross / event.quantity
        if event.price:
            return event.price
    return event.price


def run_fifo(events: Iterable[Event]) -> FifoResult:
    """Procesa los eventos en orden cronologico y devuelve el estado final."""
    holdings: dict[tuple[str, str], Holding] = {}
    queues: dict[tuple[str, str], deque[Lot]] = defaultdict(deque)
    closed: list[ClosedTrade] = []
    warnings: list[str] = []
    portfolio_fees = 0.0
    portfolio_income = 0.0

    def holding_for(ticker: str, currency: str) -> Holding:
        key = (ticker, currency)
        if key not in holdings:
            holdings[key] = Holding(ticker=ticker, currency=currency)
        return holdings[key]

    def income_holding(ticker: str, currency: str) -> Holding:
        """Tenencia a la que se le imputan dividendos, rentas y comisiones.

        Se busca por especie y no por bolsillo: un CEDEAR comprado en pesos
        cobra el dividendo en dolares, y son la misma tenencia. Separarlas
        dejaria una posicion fantasma con cantidad cero por cada especie que
        paga dividendos.
        """
        for key in holdings:
            if key[0] == ticker:
                return holdings[key]
        return holding_for(ticker, currency)

    def rescale(ticker: str, ratio: float, day: date) -> None:
        """Aplica un cambio de ratio: mas nominales, mismo costo total.

        Toca todos los bolsillos de la especie a la vez: el canje es del titulo,
        no de la moneda en la que se opero.
        """
        for key, holding in holdings.items():
            if key[0] != ticker or not holding.quantity:
                continue
            for lot in queues[key]:
                lot.quantity *= ratio
                lot.unit_cost /= ratio
            before = holding.quantity
            holding.quantity *= ratio
            holding.last_activity = day
            warnings.append(
                f"{day} {ticker}: cambio de ratio {ratio:g}:1 aplicado, "
                f"{before:,.2f} -> {holding.quantity:,.2f} nominales "
                f"(declarado en corporate_actions.json)."
            )

    for event in sorted(events, key=lambda e: (e.date, 0 if e.category == BUY else 1)):
        ticker = event.ticker
        currency = event.currency or "ARS"

        if event.category in NON_POSITION:
            # Las dos patas de una compra de dolar MEP/CCL: mueven efectivo pero
            # no son una inversion. Sin tenencia, sin costo y sin resultado.
            continue

        if event.category == RATIO_CHANGE:
            if ticker and event.ratio > 0:
                rescale(ticker, event.ratio, event.date)
            continue

        if event.category in (FEE, TAX):
            if ticker:
                h = income_holding(ticker, currency)
                h.fees += event.gross
                h.last_activity = event.date
            else:
                portfolio_fees += event.gross
            continue

        if event.category in INCOME:
            if ticker:
                h = income_holding(ticker, currency)
                h.income += event.cash_flow
                h.last_activity = event.date
                if event.category == AMORTIZATION:
                    warnings.append(
                        f"{event.date} {ticker}: amortizacion de {event.cash_flow:,.2f} "
                        f"{currency} contabilizada como ingreso (no ajusta el costo unitario)."
                    )
            else:
                portfolio_income += event.cash_flow
            continue

        if not ticker or not event.quantity:
            if event.category == ADJUSTMENT:
                warnings.append(f"{event.date}: ajuste sin especie -> {event.description}")
            continue

        key = (ticker, currency)
        h = holding_for(ticker, currency)
        queue = queues[key]

        if event.category in (BUY, OPENING):
            unit = _unit_price(event)
            lot = Lot(ticker, currency, event.date, event.quantity, unit)
            queue.append(lot)
            h.quantity += event.quantity
            h.cost_basis += lot.cost
            h.total_bought += event.gross or lot.cost
            h.buy_count += 1
            h.first_buy = h.first_buy or event.date
            h.last_buy = event.date
            h.last_activity = event.date

        elif event.category == SELL:
            unit_proceeds = _unit_price(event)
            remaining = event.quantity
            while remaining > 1e-9:
                if not queue:
                    # Venta sin compra en el historial descargado: creamos un lote
                    # sintetico al mismo precio para no inventar ganancia.
                    warnings.append(
                        f"{event.date} {ticker}: se vendieron {remaining:,.4f} sin compra "
                        f"previa en el historial (ampliar PPI_HISTORY_START)."
                    )
                    queue.append(Lot(ticker, currency, event.date, remaining, unit_proceeds, True))
                lot = queue[0]
                take = min(lot.quantity, remaining)
                closed.append(
                    ClosedTrade(
                        ticker=ticker,
                        currency=currency,
                        open_date=lot.open_date,
                        close_date=event.date,
                        quantity=take,
                        unit_cost=lot.unit_cost,
                        unit_proceeds=unit_proceeds,
                        synthetic_cost=lot.synthetic,
                    )
                )
                h.realized_pnl += take * (unit_proceeds - lot.unit_cost)
                h.quantity -= take
                h.cost_basis -= take * lot.unit_cost
                lot.quantity -= take
                remaining -= take
                if lot.quantity <= 1e-9:
                    queue.popleft()
            h.total_sold += event.gross
            h.sell_count += 1
            h.last_activity = event.date

        elif event.category == ADJUSTMENT:
            warnings.append(
                f"{event.date} {ticker}: ajuste/canje de {event.quantity:,.4f} sin tratamiento "
                f"automatico -> revisar manualmente ({event.description})"
            )

    for key, holding in holdings.items():
        holding.lots = list(queues.get(key, ()))
        if abs(holding.quantity) < 1e-9:
            holding.quantity = 0.0
            holding.cost_basis = 0.0

    return FifoResult(
        holdings=holdings,
        closed=closed,
        warnings=warnings,
        portfolio_fees=portfolio_fees,
        portfolio_income=portfolio_income,
    )


def positions_on(events: Sequence[Event], as_of: date) -> dict[tuple[str, str], float]:
    """Cantidad tenida de cada especie a una fecha dada (para reconstruir la serie)."""
    qty: dict[tuple[str, str], float] = defaultdict(float)
    for event in events:
        if event.date > as_of or not event.ticker:
            continue
        if not event.quantity and event.category != RATIO_CHANGE:
            continue
        if event.category == BUY:
            qty[(event.ticker, event.currency or "ARS")] += event.quantity
        elif event.category == SELL:
            qty[(event.ticker, event.currency or "ARS")] -= event.quantity
        elif event.category == RATIO_CHANGE and event.ratio > 0:
            for key in [k for k in qty if k[0] == event.ticker]:
                qty[key] *= event.ratio
    return {k: v for k, v in qty.items() if abs(v) > 1e-9}
