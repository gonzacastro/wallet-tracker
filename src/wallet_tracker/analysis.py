"""Ensambla todo: ledger -> FIFO -> precios -> metricas listas para mostrar."""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Sequence

from .conversions import FxConversion, fx_purchases, pair_fx_conversions, weighted_rate
from .corporate import (
    LATEST_PER_ACCOUNT,
    CorporateAction,
    apply_corporate_actions,
    load_corporate_actions,
    reconcile,
    snapshot_quantities,
)
from .ledger import FEE, INCOME, TAX, Event, Rules, build_ledger, unclassified
from .lots import ClosedTrade, FifoResult, Holding, run_fifo
from .money import Converter, is_ars
from .metrics import (
    CashFlow,
    annualize,
    daily_returns,
    max_drawdown,
    return_index,
    twr,
    volatility,
    xirr,
)
from .valuation import FxBook, NavPoint, PriceBook, build_nav_series, external_flows_by_day

#: Acciones societarias declaradas a mano (cambios de ratio, canjes).
CORPORATE_ACTIONS_FILE = "corporate_actions.json"


def _downsample(points: list[tuple[date, float]], max_points: int = 180) -> list[tuple[date, float]]:
    """Reduce la serie para que los graficos del reporte no pesen de mas."""
    if len(points) <= max_points:
        return points
    step = len(points) / max_points
    sampled = [points[int(i * step)] for i in range(max_points)]
    if sampled[-1] != points[-1]:
        sampled.append(points[-1])
    return sampled


@dataclass
class Position:
    """Una especie en cartera, con costo, precio actual y resultado."""

    ticker: str
    currency: str            # bolsillo en el que se opero ("Pesos", "Dolar MEP")
    description: str = ""
    instrument_type: str = ""
    quantity: float = 0.0
    avg_cost: float = 0.0
    cost_basis: float = 0.0
    price: float | None = None
    price_date: date | None = None
    market_value: float | None = None
    unrealized_pnl: float | None = None
    realized_pnl: float = 0.0
    income: float = 0.0
    fees: float = 0.0
    first_buy: date | None = None
    last_buy: date | None = None
    holding_days: int | None = None
    weighted_holding_days: float | None = None
    buy_count: int = 0
    sell_count: int = 0
    xirr: float | None = None
    xirr_usd: float | None = None
    total_pnl: float | None = None
    total_pnl_pct: float | None = None
    lots: list[dict[str, Any]] = field(default_factory=list)
    stale_price: bool = False
    price_series: list[tuple[date, float]] = field(default_factory=list)
    marks: list[tuple[date, float, str]] = field(default_factory=list)
    # Los mismos importes pasados a pesos con el dolar de la fecha de cada
    # operacion. Para una posicion en pesos son identicos a los de arriba; para
    # una en dolares son los unicos que se pueden sumar con el resto.
    market_value_ars: float | None = None
    cost_basis_ars: float = 0.0
    #: El broker informa esta tenencia pero el historial no la explica: se sabe
    #: cuanto tenes y cuanto vale, no cuanto pagaste. Mejor decirlo que inventar
    #: un costo y devolver un porcentaje que parece preciso y no lo es.
    cost_unknown: bool = False
    unrealized_pnl_ars: float | None = None
    realized_pnl_ars: float = 0.0
    income_ars: float = 0.0
    fees_ars: float = 0.0

    @property
    def is_open(self) -> bool:
        return self.quantity > 1e-9

    @property
    def in_ars(self) -> bool:
        return is_ars(self.currency)

    @property
    def total_return_ars(self) -> float | None:
        """Todo lo que dio la especie: precio, ventas, dividendos y costos."""
        if self.unrealized_pnl_ars is None:
            return None
        return (
            self.unrealized_pnl_ars
            + self.realized_pnl_ars
            + self.income_ars
            - self.fees_ars
        )

    @property
    def total_return_pct(self) -> float | None:
        total = self.total_return_ars
        if self.cost_unknown or total is None or not self.cost_basis_ars:
            return None
        return total / self.cost_basis_ars


@dataclass
class PortfolioReport:
    as_of: date
    positions: list[Position]
    closed_positions: list[Position]
    closed_trades: list[ClosedTrade]
    nav_series: list[NavPoint]
    warnings: list[str]
    unclassified_events: list[Event]
    events: list[Event]
    fifo: FifoResult
    fx_conversions: list[FxConversion] = field(default_factory=list)
    corporate_actions: list[CorporateAction] = field(default_factory=list)
    #: Metricas de la etapa en que hubo cartera de verdad. Es el encabezado del
    #: dashboard: las de historial completo quedan diluidas si antes la cuenta
    #: se uso para otra cosa (comprar dolar MEP, por ejemplo).
    investing: PeriodMetrics | None = None
    #: Aportes y retiros externos por dia, ya pasados a pesos.
    flows_by_day: dict[date, float] = field(default_factory=dict)
    #: Rendimiento de cada especie mes a mes (mapa de calor del reporte).
    asset_months: list[AssetMonths] = field(default_factory=list)
    ccl: float | None = None
    deposits: float = 0.0
    withdrawals: float = 0.0
    portfolio_fees: float = 0.0
    portfolio_income: float = 0.0
    xirr: float | None = None
    xirr_usd: float | None = None
    twr: float | None = None
    twr_annualized: float | None = None
    #: El mismo rendimiento medido en dolares: descuenta la devaluacion.
    return_usd: float | None = None
    benchmark: Benchmark | None = None
    volatility: float | None = None
    max_drawdown: float = 0.0
    max_drawdown_dates: tuple[date | None, date | None] = (None, None)
    first_activity: date | None = None

    # Todos los agregados de cartera suman posiciones que pueden estar en
    # monedas distintas, asi que usan siempre la variante pasada a pesos.
    @property
    def market_value(self) -> float:
        return sum(p.market_value_ars or 0.0 for p in self.positions)

    @property
    def cost_basis(self) -> float:
        return sum(p.cost_basis_ars for p in self.positions)

    @property
    def unrealized_pnl(self) -> float:
        return sum(p.unrealized_pnl_ars or 0.0 for p in self.positions)

    @property
    def realized_pnl(self) -> float:
        return sum(p.realized_pnl_ars for p in self.all_positions)

    @property
    def income(self) -> float:
        return sum(p.income_ars for p in self.all_positions) + self.portfolio_income

    @property
    def fees(self) -> float:
        return sum(p.fees_ars for p in self.all_positions) + self.portfolio_fees

    @property
    def all_positions(self) -> list[Position]:
        return self.positions + self.closed_positions

    @property
    def untracked(self) -> list[Position]:
        """Tenencias que el broker informa y el historial no explica."""
        return [p for p in self.positions if p.cost_unknown]

    @property
    def untracked_value(self) -> float:
        return sum(p.market_value_ars or 0.0 for p in self.untracked)

    @property
    def total_value(self) -> float:
        """Todo lo que tenes, incluyendo lo que no tiene historial de costo.

        `nav_ars` sale de la serie diaria, que se reconstruye de los movimientos
        y por eso no puede ver una tenencia sin operaciones. Para "cuanto tengo"
        hay que sumarlas aparte.
        """
        return self.nav_ars + self.untracked_value

    def by_performance(self) -> list[Position]:
        """Posiciones abiertas de mejor a peor rendimiento porcentual."""
        return sorted(
            (p for p in self.positions if p.cost_basis),
            key=lambda p: -(p.unrealized_pnl / p.cost_basis if p.unrealized_pnl is not None else 0.0),
        )

    @property
    def since(self) -> date | None:
        """Desde cuando tiene sentido medir: la primera inversion de verdad."""
        return self.investing.since if self.investing else self.first_activity

    @property
    def fx_purchases(self) -> list[FxConversion]:
        """Las conversiones que compraron moneda dura con pesos."""
        return fx_purchases(self.fx_conversions)

    @property
    def fx_spent_ars(self) -> float:
        return sum(c.from_amount for c in self.fx_purchases)

    @property
    def fx_bought_usd(self) -> float:
        return sum(c.to_amount for c in self.fx_purchases)

    @property
    def fx_weighted_rate(self) -> float | None:
        return weighted_rate(self.fx_purchases)

    @property
    def nav_ars(self) -> float:
        return self.nav_series[-1].nav_ars if self.nav_series else self.market_value

    @property
    def nav_usd(self) -> float | None:
        return self.nav_series[-1].nav_usd if self.nav_series else None

    @property
    def cash(self) -> float:
        return self.nav_series[-1].cash_ars if self.nav_series else 0.0

    @property
    def net_invested(self) -> float:
        return self.deposits - self.withdrawals

    @property
    def total_pnl(self) -> float:
        """Ganancia total = lo que hay hoy - lo que pusiste neto."""
        return self.nav_ars - self.net_invested

    @property
    def total_pnl_pct(self) -> float | None:
        return self.total_pnl / self.deposits if self.deposits else None

    @property
    def days_invested(self) -> int:
        if not self.first_activity:
            return 0
        return (self.as_of - self.first_activity).days



@dataclass
class MonthPoint:
    """Cierre de un mes: cuanto llevabas puesto y cuanto valia."""

    month: date               # primer dia del mes
    deposits: float           # aportes netos de ese mes
    contributed: float        # capital acumulado hasta el cierre del mes
    value: float              # valuacion al ultimo dia del mes con dato
    ccl: float | None = None
    #: Cuanto rindio el mes en si, descontando lo que aportaste. Es la respuesta
    #: a "como me fue este mes", que no es lo mismo que cuanto crecio el saldo.
    month_return: float | None = None

    @property
    def gain(self) -> float:
        return self.value - self.contributed

    @property
    def gain_pct(self) -> float | None:
        return (self.gain / self.contributed) if self.contributed else None

    @property
    def value_usd(self) -> float | None:
        return (self.value / self.ccl) if self.ccl else None

    @property
    def contributed_usd(self) -> float | None:
        return (self.contributed / self.ccl) if self.ccl else None


@dataclass
class Benchmark:
    """Que hubiera pasado comprando y manteniendo un indice el mismo periodo."""

    ticker: str
    ret: float                 # rendimiento del indice, en pesos
    portfolio: float           # rendimiento de tu cartera, en pesos

    @property
    def difference(self) -> float:
        """Puntos porcentuales de ventaja (o desventaja) sobre el indice."""
        return self.portfolio - self.ret

    @property
    def beat(self) -> bool:
        return self.difference >= 0


@dataclass
class AssetMonths:
    """Como le fue a una especie en cada mes del periodo medido."""

    ticker: str
    returns: list[float | None]      # alineado con `PeriodMetrics.months`

    @property
    def best(self) -> float | None:
        medidos = [r for r in self.returns if r is not None]
        return max(medidos) if medidos else None

    @property
    def worst(self) -> float | None:
        medidos = [r for r in self.returns if r is not None]
        return min(medidos) if medidos else None


@dataclass
class PeriodMetrics:
    """Las mismas metricas, acotadas a la ventana en que hubo cartera de verdad.

    Existe porque una cuenta puede haberse usado para otra cosa antes de que
    hubiera inversiones -- comprar dolar MEP, por ejemplo -- y promediar las dos
    etapas no mide nada: diluye el rendimiento de la cartera en anios en los que
    no habia cartera.
    """

    since: date
    until: date
    opening: float = 0.0          # lo que ya habia en la cuenta la vispera
    deposits: float = 0.0
    withdrawals: float = 0.0
    value: float = 0.0
    income: float = 0.0           # dividendos y rentas cobrados en la ventana
    fees: float = 0.0             # comisiones e impuestos de la ventana
    xirr: float | None = None
    xirr_usd: float | None = None
    twr: float | None = None
    twr_annualized: float | None = None
    #: El mismo rendimiento medido en dolares: descuenta la devaluacion.
    return_usd: float | None = None
    benchmark: Benchmark | None = None
    volatility: float | None = None
    max_drawdown: float = 0.0
    max_drawdown_dates: tuple[date | None, date | None] = (None, None)
    months: list[MonthPoint] = field(default_factory=list)

    @property
    def contributed(self) -> float:
        """Plata tuya que hizo falta para llegar hasta aca."""
        return self.opening + self.deposits - self.withdrawals

    @property
    def gain(self) -> float:
        return self.value - self.contributed

    @property
    def gain_pct(self) -> float | None:
        return (self.gain / self.contributed) if self.contributed else None

    @property
    def days(self) -> int:
        return (self.until - self.since).days

    @property
    def green_months(self) -> tuple[int, int]:
        """Cuantos meses cerraron en verde, sobre cuantos meses medidos."""
        medidos = [m for m in self.months if m.month_return is not None]
        return sum(1 for m in medidos if m.month_return > 0), len(medidos)

    @property
    def best_month(self) -> MonthPoint | None:
        medidos = [m for m in self.months if m.month_return is not None]
        return max(medidos, key=lambda m: m.month_return) if medidos else None

    @property
    def last_month(self) -> MonthPoint | None:
        return self.months[-1] if self.months else None

    @property
    def monthly_average(self) -> float | None:
        """Cuanto venis poniendo por mes, en promedio."""
        return (self.deposits / len(self.months)) if self.months else None


def first_investment(events: Sequence[Event]) -> date | None:
    """Fecha de la primera operacion que es realmente una inversion.

    Las patas de una compra de dolar MEP/CCL ya vienen marcadas como
    `FX_CONVERSION` desde el ledger, asi que `is_trade` las deja afuera solo.
    Las tenencias iniciales (`OPENING`) tampoco cuentan: son anteriores por
    definicion, y tomarlas como inicio movería el periodo a una fecha inventada.
    """
    return min((e.date for e in events if e.is_trade and e.ticker), default=None)


def _ratio_since(actions: Sequence[CorporateAction], ticker: str, day: date) -> float:
    """Cuanto se multiplicaron los nominales de una especie despues de `day`.

    Sirve para llevar un precio viejo a la escala de hoy: si despues de esa
    fecha hubo un canje 3:1, el precio de entonces equivale a un tercio del que
    figura en el movimiento.
    """
    factor = 1.0
    for action in actions:
        if action.ticker == ticker and action.date > day and action.ratio > 0:
            factor *= action.ratio
    return factor


def monthly_returns_by_asset(
    positions: Sequence[Position], prices: PriceBook, months: Sequence[MonthPoint]
) -> list[AssetMonths]:
    """Rendimiento de cada especie mes a mes, para ver *cuando* se torcio que.

    El total de una especie no distingue entre caer de a poco y desplomarse un
    mes puntual, y son cosas distintas. Se usa la serie empalmada por canjes.

    El mes en que compraste se mide desde el dia de la compra, no desde el 1:
    dejarlo en blanco perderia informacion, y medirlo entero le atribuiria dias
    en los que la especie todavia no era tuya.
    """
    if len(months) < 2:
        return []
    cierres = [
        date(m.month.year + (m.month.month == 12), m.month.month % 12 + 1, 1)
        for m in months
    ]
    salida: list[AssetMonths] = []
    for position in positions:
        desde = position.first_buy
        retornos: list[float | None] = []
        for inicio, fin in zip(cierres, cierres[1:]):
            if desde is None or desde >= fin:
                retornos.append(None)
                continue
            antes = prices.get(position.ticker, max(inicio, desde))
            ahora = prices.get(position.ticker, fin)
            retornos.append((ahora / antes - 1) if (antes and ahora) else None)
        if any(r is not None for r in retornos):
            salida.append(AssetMonths(position.ticker, retornos))
    return salida


def _buy_and_hold(prices: PriceBook, ticker: str, start: date, end: date) -> float | None:
    """Rendimiento de comprar el indice al principio y no tocarlo mas."""
    first, last = prices.get(ticker, start), prices.get(ticker, end)
    if not first or not last:
        return None
    return last / first - 1.0


def _fill_month_returns(
    period: "PeriodMetrics",
    nav_points: Sequence[tuple[date, float]],
    flows_by_day: dict[date, float],
) -> None:
    """Cuanto rindio cada mes en si, sin contar lo que aportaste ese mes."""
    index = dict(return_index(nav_points, flows_by_day))
    closes: dict[tuple[int, int], float] = {}
    for day in sorted(index):
        closes[(day.year, day.month)] = index[day]
    previous = 1.0
    for month in period.months:
        value = closes.get((month.month.year, month.month.month))
        if value is None:
            continue
        month.month_return = value / previous - 1.0 if previous else None
        previous = value


def contributed_series(
    nav_series: Sequence[NavPoint], flows_by_day: dict[date, float], opening: float
) -> list[tuple[date, float]]:
    """Capital aportado acumulado, dia a dia.

    Es la linea de referencia del grafico de crecimiento: todo lo que la
    valuacion tenga por encima de esta escalera es ganancia.
    """
    out: list[tuple[date, float]] = []
    running = opening
    for point in nav_series:
        running += flows_by_day.get(point.date, 0.0)
        out.append((point.date, running))
    return out


def monthly_progress(
    nav_series: Sequence[NavPoint], flows_by_day: dict[date, float], opening: float
) -> list[MonthPoint]:
    """Un punto por mes con el cierre de capital aportado y valuacion."""
    if not nav_series:
        return []
    by_month: dict[tuple[int, int], NavPoint] = {}
    deposits: dict[tuple[int, int], float] = defaultdict(float)
    for point in nav_series:
        key = (point.date.year, point.date.month)
        by_month[key] = point            # el ultimo del mes queda
        deposits[key] += flows_by_day.get(point.date, 0.0)

    months: list[MonthPoint] = []
    running = opening
    for key in sorted(by_month):
        running += deposits[key]
        point = by_month[key]
        months.append(
            MonthPoint(
                month=date(key[0], key[1], 1),
                deposits=deposits[key],
                contributed=running,
                value=point.nav_ars,
                ccl=point.ccl,
            )
        )
    return months


def load_events(
    conn: sqlite3.Connection,
    rules: Rules | None = None,
    *,
    actions: Sequence[CorporateAction] | None = None,
) -> tuple[list[Event], list[FxConversion]]:
    """Ledger listo para consumir: clasificado, apareado y ajustado.

    Es el unico punto donde se arma la linea de tiempo de la cuenta. Todo lo que
    corrige la lectura cruda de PPI pasa por aca -- apareo de conversiones de
    moneda y acciones societarias -- para que el motor FIFO, la serie de
    valuacion y los flujos de la TIR vean exactamente los mismos eventos.
    """
    rows = conn.execute(
        "SELECT uid, agreement_date, settlement_date, currency, amount, price, description, "
        "ticker, quantity, balance, ordinal FROM movements WHERE agreement_date IS NOT NULL "
        "ORDER BY agreement_date, ordinal"
    ).fetchall()
    events = build_ledger([dict(r) for r in rows], rules)
    events, conversions = pair_fx_conversions(events)
    if actions:
        events = apply_corporate_actions(events, actions)
    return events, conversions


def load_instrument_meta(conn: sqlite3.Connection) -> dict[str, dict[str, str]]:
    return {
        r["ticker"]: {
            "description": r["description"] or "",
            "type": r["type"] or "",
            "currency": (r["currency"] or "").upper(),
        }
        for r in conn.execute("SELECT ticker, description, type, currency FROM instruments")
    }


#: Categorias cuyo importe forma parte del resultado de una especie. Las patas
#: de una conversion de moneda quedan afuera a proposito: no son inversion.
TICKER_FLOW_CATEGORIES = ("BUY", "SELL", "DIVIDEND", "COUPON", "AMORTIZATION", "FEE", "TAX")


def _events_for_holding(holding: Holding, events: Sequence[Event]) -> list[Event]:
    """Operaciones de esta tenencia: mismo ticker y mismo bolsillo."""
    return [
        e
        for e in events
        if e.ticker == holding.ticker and (e.currency or "ARS") == holding.currency
    ]


def _primary_key(holdings: dict[tuple[str, str], Holding], ticker: str) -> tuple[str, str] | None:
    """Tenencia que se queda con los dividendos y comisiones de una especie.

    Misma regla que usa el motor FIFO: la primera del ticker en aparecer. Los
    dividendos se imputan a la especie y no al bolsillo en que se cobran, asi
    que hay que elegir una sola tenencia o se contarian dos veces.
    """
    return next((k for k in holdings if k[0] == ticker), None)


def _flows_for_ticker(holding: Holding, events: Sequence[Event]) -> list[CashFlow]:
    return [
        CashFlow(e.date, e.cash_flow)
        for e in _events_for_holding(holding, events)
        if e.category in TICKER_FLOW_CATEGORIES
    ]


def _to_usd(flows: Sequence[CashFlow], fx: FxBook | None) -> list[CashFlow] | None:
    if not fx:
        return None
    out: list[CashFlow] = []
    for flow in flows:
        rate = fx.get(flow.date)
        if not rate:
            return None
        out.append(CashFlow(flow.date, flow.amount / rate))
    return out


def build_report(
    conn: sqlite3.Connection,
    *,
    rules: Rules | None = None,
    as_of: date | None = None,
    nav_from: date | None = None,
    benchmark: str | None = None,
) -> PortfolioReport:
    as_of = as_of or date.today()
    actions = load_corporate_actions(CORPORATE_ACTIONS_FILE)
    events, conversions = load_events(conn, rules, actions=actions)
    fifo = run_fifo(events)
    # Dos vistas de la misma serie, a proposito:
    #   `prices`   crudo, para valuar. Cantidad y precio de cada dia son los
    #              reales de ese dia; el canje ya lo aplica `quantities_by_day`.
    #              Ajustar tambien los precios contaria el canje dos veces.
    #   `adjusted` empalmado, para graficar y comparar contra el precio de hoy.
    prices = PriceBook.from_db(conn)
    adjusted = PriceBook.from_db(conn)
    adjusted.apply_ratio_changes(actions)
    fx = FxBook.from_db(conn)
    converter = Converter(fx)
    meta = load_instrument_meta(conn)
    instrument_currency = {t: m["currency"] for t, m in meta.items()}

    snapshot_prices = {
        r["ticker"]: (float(r["price"]), r["currency"] or "")
        for r in conn.execute(
            "SELECT ticker, price, currency FROM snapshots s WHERE kind='instrument' "
            f"AND ticker IS NOT NULL AND price > 0 AND {LATEST_PER_ACCOUNT}"
        )
    }

    ccl = fx.get(as_of) if fx else None
    positions: list[Position] = []
    closed_positions: list[Position] = []

    for holding in sorted(fifo.holdings.values(), key=lambda h: h.ticker):
        info = meta.get(holding.ticker, {})
        quote = snapshot_prices.get(holding.ticker)
        price = quote[0] if quote else None
        # La moneda del precio no tiene por que ser la del bolsillo en el que se
        # opero: un CEDEAR comprado con dolar cable cotiza en pesos.
        price_currency = quote[1] if quote else (info.get("currency") or holding.currency)
        price_date: date | None = as_of if price else None
        stale = False
        if price is None:
            last = prices.last(holding.ticker)
            if last:
                price_date, price = last
                stale = (as_of - price_date).days > 5

        flows = _flows_for_ticker(holding, events)
        market_value = holding.quantity * price if (price and holding.is_open) else (0.0 if not holding.is_open else None)
        terminal = list(flows)
        if market_value:
            terminal.append(CashFlow(as_of, market_value))
        rate = xirr(terminal) if len(terminal) >= 2 else None
        usd_flows = _to_usd(terminal, fx)
        rate_usd = xirr(usd_flows) if usd_flows and len(usd_flows) >= 2 else None

        unrealized = (market_value - holding.cost_basis) if market_value is not None else None
        total_pnl = None
        if unrealized is not None:
            total_pnl = unrealized + holding.realized_pnl + holding.income - holding.fees
        invested = holding.total_bought or holding.cost_basis

        # Cada importe se pasa a pesos con el dolar de *su* fecha: el costo con
        # el del dia de cada lote, el resultado realizado con el de cada punta,
        # la valuacion con el de hoy. Es lo unico sumable entre posiciones.
        cost_basis_ars = sum(
            converter.to_ars(lot.cost, holding.currency, lot.open_date) for lot in holding.lots
        )
        market_value_ars = (
            converter.to_ars(market_value, price_currency, price_date or as_of)
            if market_value is not None
            else None
        )
        realized_pnl_ars = sum(
            converter.to_ars(t.proceeds, holding.currency, t.close_date)
            - converter.to_ars(t.cost, holding.currency, t.open_date)
            for t in fifo.closed
            if t.ticker == holding.ticker and t.currency == holding.currency
        )
        # Dividendos y comisiones se buscan por especie, no por bolsillo, y se
        # imputan a una sola tenencia para no contarlos dos veces.
        mine = _primary_key(fifo.holdings, holding.ticker) == (holding.ticker, holding.currency)
        ticker_events = [e for e in events if e.ticker == holding.ticker] if mine else []
        income_ars = sum(
            converter.to_ars(e.cash_flow, e.currency, e.date)
            for e in ticker_events
            if e.category in INCOME
        )
        fees_ars = sum(
            converter.to_ars(e.gross, e.currency, e.date)
            for e in ticker_events
            if e.category in (FEE, TAX)
        )

        position = Position(
            ticker=holding.ticker,
            currency=holding.currency,
            description=info.get("description", ""),
            instrument_type=info.get("type", ""),
            quantity=holding.quantity,
            avg_cost=holding.avg_cost,
            cost_basis=holding.cost_basis,
            price=price,
            price_date=price_date,
            market_value=market_value,
            unrealized_pnl=unrealized,
            realized_pnl=holding.realized_pnl,
            income=holding.income,
            fees=holding.fees,
            first_buy=holding.first_buy,
            last_buy=holding.last_buy,
            holding_days=holding.holding_days,
            weighted_holding_days=holding.weighted_holding_days,
            buy_count=holding.buy_count,
            sell_count=holding.sell_count,
            xirr=rate,
            xirr_usd=rate_usd,
            total_pnl=total_pnl,
            total_pnl_pct=(total_pnl / invested) if (total_pnl is not None and invested) else None,
            stale_price=stale,
            market_value_ars=market_value_ars,
            cost_basis_ars=cost_basis_ars,
            unrealized_pnl_ars=(
                None if market_value_ars is None else market_value_ars - cost_basis_ars
            ),
            realized_pnl_ars=realized_pnl_ars,
            income_ars=income_ars,
            fees_ars=fees_ars,
            lots=[
                {
                    "open_date": lot.open_date,
                    "quantity": lot.quantity,
                    "unit_cost": lot.unit_cost,
                    "cost": lot.cost,
                    "synthetic": lot.synthetic,
                    "days": (as_of - lot.open_date).days,
                    "pnl_pct": ((price / lot.unit_cost - 1.0) if (price and lot.unit_cost) else None),
                }
                for lot in holding.lots
            ],
        )
        # Arrancamos unos dias antes de la primera compra: si ese dia no hubo
        # rueda (feriado, fin de semana) igual queda dentro de la serie.
        window_start = (holding.first_buy or as_of) - timedelta(days=10)
        # El grafico usa la serie empalmada: con la cruda, un cambio de ratio
        # dibuja un precipicio que nunca ocurrio.
        position.price_series = _downsample(
            adjusted.series(holding.ticker, start=window_start, end=as_of)
        )
        # Las marcas van sobre la serie empalmada, asi que el precio al que
        # operaste hay que llevarlo a la escala de hoy: si despues hubo un canje
        # 3:1, aquellos $52.600 son $17.533 en nominales de ahora.
        position.marks = [
            (
                e.date,
                (e.price or (e.gross / e.quantity if e.quantity else 0.0))
                / _ratio_since(actions, holding.ticker, e.date),
                e.category,
            )
            for e in events
            if e.ticker == holding.ticker and e.category in ("BUY", "SELL") and e.quantity
        ]
        (positions if holding.is_open else closed_positions).append(position)

    # Tenencias que el broker informa y el historial no explica: aparecen con
    # cantidad y valor, sin inventarles un costo.
    conocidas = {p.ticker for p in positions}
    for ticker, cantidad in sorted(snapshot_quantities(conn).items()):
        if ticker in conocidas or cantidad <= 0:
            continue
        quote = snapshot_prices.get(ticker)
        precio = quote[0] if quote else (prices.last(ticker) or (None, None))[1]
        if not precio:
            continue
        info = meta.get(ticker, {})
        moneda = (quote[1] if quote else info.get("currency")) or "Pesos"
        valor = cantidad * precio
        positions.append(Position(
            ticker=ticker,
            currency=moneda,
            description=info.get("description", ""),
            instrument_type=info.get("type", ""),
            quantity=cantidad,
            price=precio,
            price_date=as_of,
            market_value=valor,
            market_value_ars=converter.to_ars(valor, moneda, as_of),
            cost_unknown=True,
            price_series=_downsample(adjusted.series(ticker, end=as_of)),
        ))

    positions.sort(key=lambda p: -(p.market_value_ars or 0.0))
    closed_positions.sort(key=lambda p: (p.last_buy or date.min), reverse=True)

    first_activity = min((e.date for e in events if e.cash_flow or e.quantity), default=None)
    nav_series = build_nav_series(
        events, prices, fx, instrument_currency,
        conversions=conversions,
        start=nav_from or first_activity, end=as_of,
    )
    flows_by_day = external_flows_by_day(events, converter)
    deposits = sum(
        converter.to_ars(e.cash_flow, e.currency, e.date)
        for e in events
        if e.category == "DEPOSIT"
    )
    withdrawals = -sum(
        converter.to_ars(e.cash_flow, e.currency, e.date)
        for e in events
        if e.category == "WITHDRAWAL"
    )
    portfolio_income = sum(
        converter.to_ars(e.cash_flow, e.currency, e.date)
        for e in events
        if e.category in INCOME and not e.ticker
    )
    portfolio_fees = sum(
        converter.to_ars(e.gross, e.currency, e.date)
        for e in events
        if e.category in (FEE, TAX) and not e.ticker
    )

    nav_points = [(p.date, p.nav_ars) for p in nav_series]
    total_twr = twr(nav_points, flows_by_day)
    days = (as_of - first_activity).days if first_activity else 0
    # La peor caida se mide sobre el indice de retorno, no sobre la valuacion:
    # sacar plata de la cuenta no es una perdida.
    dd, dd_peak, dd_trough = max_drawdown(return_index(nav_points, flows_by_day))

    # Los flujos de la TIR salen de `flows_by_day`, que ya esta en pesos: un
    # retiro de US$4.000 no puede pesar lo mismo que uno de $4.000.
    terminal_value = nav_series[-1].nav_ars if nav_series else 0.0
    portfolio_flows = [
        CashFlow(day, -amount) for day, amount in sorted(flows_by_day.items()) if amount
    ]
    if terminal_value:
        portfolio_flows.append(CashFlow(as_of, terminal_value))
    portfolio_xirr = xirr(portfolio_flows)
    usd_portfolio = _to_usd(portfolio_flows, fx)
    portfolio_xirr_usd = xirr(usd_portfolio) if usd_portfolio else None

    # --- metricas de la etapa de inversion ------------------------------
    # Todo lo anterior a la primera compra real (el conducto de dolar MEP) queda
    # afuera: promediarlo diluye el rendimiento de la cartera en anios en los
    # que no habia cartera.
    investing: PeriodMetrics | None = None
    start = first_investment(events)
    if start and nav_series:
        window = [p for p in nav_series if p.date >= start]
        previous = [p for p in nav_series if p.date < start]
        opening = previous[-1].nav_ars if previous else 0.0
        window_flows = {d: a for d, a in flows_by_day.items() if d >= start}
        window_points = [(p.date, p.nav_ars) for p in window]
        window_twr = twr(window_points, window_flows)
        window_days = (as_of - start).days
        w_dd, w_peak, w_trough = max_drawdown(return_index(window_points, window_flows))
        window_cash = (
            [CashFlow(start, -opening)] if opening else []
        ) + [CashFlow(d, -a) for d, a in sorted(window_flows.items()) if a]
        terminal = window[-1].nav_ars if window else 0.0
        if terminal:
            window_cash.append(CashFlow(as_of, terminal))
        window_usd = _to_usd(window_cash, fx)
        investing = PeriodMetrics(
            since=start,
            until=as_of,
            opening=opening,
            deposits=sum(
                converter.to_ars(e.cash_flow, e.currency, e.date)
                for e in events
                if e.category == "DEPOSIT" and e.date >= start
            ),
            withdrawals=-sum(
                converter.to_ars(e.cash_flow, e.currency, e.date)
                for e in events
                if e.category == "WITHDRAWAL" and e.date >= start
            ),
            value=terminal,
            income=sum(
                converter.to_ars(e.cash_flow, e.currency, e.date)
                for e in events
                if e.category in INCOME and e.date >= start
            ),
            fees=sum(
                converter.to_ars(e.gross, e.currency, e.date)
                for e in events
                if e.category in (FEE, TAX) and e.date >= start
            ),
            xirr=xirr(window_cash) if len(window_cash) >= 2 else None,
            xirr_usd=xirr(window_usd) if (window_usd and len(window_usd) >= 2) else None,
            twr=window_twr,
            twr_annualized=(
                annualize(window_twr, window_days)
                if (window_twr is not None and window_days) else None
            ),
            volatility=volatility(daily_returns(window_points, window_flows)),
            max_drawdown=w_dd,
            max_drawdown_dates=(w_peak, w_trough),
            months=monthly_progress(window, window_flows, opening),
        )
        # El mismo rendimiento medido en dolares y contra un indice: dos formas
        # de contestar "gane o perdi" sin tener que explicar que es una TIR.
        usd_points = [(p.date, p.nav_usd) for p in window if p.nav_usd]
        usd_flows = {
            d: a / fx.get(d) for d, a in window_flows.items() if fx.get(d)
        }
        investing.return_usd = twr(usd_points, usd_flows) if len(usd_points) > 1 else None
        _fill_month_returns(investing, window_points, window_flows)
        if benchmark and window_twr is not None:
            reference = _buy_and_hold(adjusted, benchmark, start, as_of)
            if reference is not None:
                investing.benchmark = Benchmark(benchmark, reference, window_twr)

    warnings = list(fifo.warnings)
    warnings += reconcile(fifo, snapshot_quantities(conn))
    if converter.missing:
        warnings.append(
            f"{len(converter.missing)} fechas sin cotizacion de dolar: los importes en "
            f"moneda extranjera de esos dias se sumaron sin convertir "
            f"(desde {min(converter.missing)} hasta {max(converter.missing)})."
        )

    return PortfolioReport(
        as_of=as_of,
        positions=positions,
        closed_positions=closed_positions,
        closed_trades=fifo.closed,
        nav_series=nav_series,
        warnings=warnings,
        unclassified_events=unclassified(events),
        events=events,
        fifo=fifo,
        fx_conversions=conversions,
        corporate_actions=list(actions),
        investing=investing,
        asset_months=(
            monthly_returns_by_asset(positions, adjusted, investing.months)
            if investing else []
        ),
        flows_by_day=flows_by_day,
        ccl=ccl,
        deposits=deposits,
        withdrawals=withdrawals,
        portfolio_fees=portfolio_fees,
        portfolio_income=portfolio_income,
        xirr=portfolio_xirr,
        xirr_usd=portfolio_xirr_usd,
        twr=total_twr,
        twr_annualized=annualize(total_twr, days) if (total_twr is not None and days) else None,
        volatility=volatility(daily_returns(nav_points, flows_by_day)),
        max_drawdown=dd,
        max_drawdown_dates=(dd_peak, dd_trough),
        first_activity=first_activity,
    )


def benchmark_return(conn: sqlite3.Connection, ticker: str, start: date, end: date) -> float | None:
    """Retorno de comprar y mantener una especie en el mismo periodo."""
    prices = PriceBook.from_db(conn)
    prices.apply_ratio_changes(load_corporate_actions(CORPORATE_ACTIONS_FILE))
    first, last = prices.get(ticker.upper(), start), prices.get(ticker.upper(), end)
    if not first or not last:
        return None
    return last / first - 1.0
