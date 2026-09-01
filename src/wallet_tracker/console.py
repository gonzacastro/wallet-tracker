"""Presentacion en terminal con rich."""

from __future__ import annotations

from datetime import date
from typing import Iterable, Sequence

from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .analysis import MonthPoint, PortfolioReport, Position
from .attention import what_to_watch
from .conversions import FxConversion
from .lots import ClosedTrade
from .money import PESOS, is_ars, normalize_currency


def money(value: float | None, symbol: str = "$", decimals: int = 2) -> str:
    if value is None:
        return "-"
    return f"{symbol}{value:,.{decimals}f}"


def pct(value: float | None, decimals: int = 1) -> str:
    if value is None:
        return "-"
    return f"{value * 100:,.{decimals}f}%"


def colored(value: float | None, text: str | None = None) -> Text:
    if value is None:
        return Text("-", style="dim")
    style = "green" if value > 0 else ("red" if value < 0 else "white")
    return Text(text if text is not None else f"{value:,.2f}", style=style)


def _days(value: int | float | None) -> str:
    if value is None:
        return "-"
    days = int(value)
    if days >= 365:
        return f"{days / 365:.1f} anios"
    return f"{days} d"


def watch_panel(report: PortfolioReport) -> Panel | None:
    """Lo unico que hace falta leer al abrir: que amerita que hagas algo."""
    notas = what_to_watch(report)
    if not notas:
        return None
    lineas = [
        Text("· ", style="green" if n.is_good else ("red" if n.kind == "dato" else "cyan"))
        + Text(n.text, style="bold" if not n.is_good else "green")
        for n in notas
    ]
    return Panel(
        Group(*lineas), title="[bold]Que mirar hoy[/]", border_style="cyan", expand=False
    )


def summary_panel(report: PortfolioReport, *, advanced: bool = False) -> Panel:
    """Las tres preguntas, en plata y en porcentaje.

    Por defecto no muestra TIR ni TWR: son las dos que hay que explicar antes de
    poder leerlas. Lo mismo contestado en castellano -- cuanto ganaste, cuanto
    va este mes, cuanto llegaste a estar abajo y si le ganaste al indice -- vive
    en la tercera columna. `advanced` agrega las tasas para quien las quiera.
    """
    periodo = report.investing

    tengo = Table.grid(padding=(0, 2))
    tengo.add_column(style="dim")
    tengo.add_column(justify="right")
    tengo.add_row(Text("CUANTO TENGO", style="bold cyan"), "")
    tengo.add_row("Valor hoy", Text(money(report.total_value), style="bold"))
    if report.ccl:
        tengo.add_row("  en dolares", money(report.total_value / report.ccl, "US$"))
    tengo.add_row("  en especies", money(report.market_value))
    tengo.add_row("  en efectivo", money(report.cash))
    if report.untracked_value:
        tengo.add_row("  sin historial de costo", money(report.untracked_value))
    tengo.add_row("Dolar implicito", money(report.ccl) if report.ccl else "-")

    puse = Table.grid(padding=(0, 2))
    puse.add_column(style="dim")
    puse.add_column(justify="right")
    puse.add_row(Text("CUANTO PUSE Y GANE", style="bold cyan"), "")
    if periodo:
        puse.add_row("Pusiste", Text(money(periodo.contributed), style="bold"))
        puse.add_row("  aportando por mes", money(periodo.monthly_average))
        puse.add_row("Ganancia", colored(periodo.gain, money(periodo.gain)))
        puse.add_row("  sobre lo que pusiste", colored(periodo.gain_pct, pct(periodo.gain_pct)))
        puse.add_row("  medida en dolares", colored(periodo.return_usd, pct(periodo.return_usd)))
        if periodo.income:
            puse.add_row("  de eso, dividendos", money(periodo.income))
    else:
        puse.add_row("Aportes netos", money(report.net_invested))
        puse.add_row("Ganancia", colored(report.total_pnl, money(report.total_pnl)))

    viene = Table.grid(padding=(0, 2))
    viene.add_column(style="dim")
    viene.add_column(justify="right")
    viene.add_row(Text("COMO VIENE", style="bold cyan"), "")
    m = periodo or report
    if periodo and periodo.last_month and periodo.last_month.month_return is not None:
        ultimo = periodo.last_month
        viene.add_row(
            f"Este mes ({ultimo.month.strftime('%b')})",
            colored(ultimo.month_return, pct(ultimo.month_return)),
        )
    if periodo:
        verdes, total_meses = periodo.green_months
        if total_meses:
            viene.add_row("Meses en verde", f"{verdes} de {total_meses}")
    viene.add_row("Peor momento", colored(m.max_drawdown, pct(m.max_drawdown)))
    if periodo and periodo.benchmark:
        b = periodo.benchmark
        viene.add_row(
            f"vs. {b.ticker}",
            colored(b.difference, f"{b.difference * 100:+,.1f} pts"),
        )
    if advanced:
        viene.add_row("", "")
        viene.add_row("TIR anual", colored(m.xirr, pct(m.xirr)))
        viene.add_row("TIR anual en dolares", colored(m.xirr_usd, pct(m.xirr_usd)))
        viene.add_row("TWR", colored(m.twr, pct(m.twr)))
        viene.add_row("  anualizado", colored(m.twr_annualized, pct(m.twr_annualized)))
        viene.add_row("Volatilidad anual", pct(m.volatility))

    body = Table.grid(padding=(0, 4))
    body.add_column()
    body.add_column()
    body.add_column()
    body.add_row(tengo, puse, viene)

    if periodo:
        titulo = (
            f"[bold]Cartera al {report.as_of.isoformat()}[/] · invirtiendo desde "
            f"{periodo.since.isoformat()} ({_days(periodo.days)})"
        )
    else:
        since = report.first_activity.isoformat() if report.first_activity else "?"
        titulo = (
            f"[bold]Cartera al {report.as_of.isoformat()}[/] · desde {since} "
            f"({_days(report.days_invested)})"
        )
    return Panel(body, title=titulo, border_style="cyan")


def _diverging_bar(value: float, top: float, width: int = 11) -> Text:
    """Barra con eje al centro: verde a la derecha, roja a la izquierda."""
    if not top:
        return Text(" " * (width * 2 + 1))
    filled = min(int(round(abs(value) / top * width)), width)
    if value >= 0:
        return Text(" " * width + "\u2502", style="dim") + Text("\u2588" * filled, style="green")
    return (
        Text(" " * (width - filled))
        + Text("\u2588" * filled, style="red")
        + Text("\u2502", style="dim")
    )


def performance_table(positions: Sequence[Position]) -> Table:
    """De la mas fructifera al mayor clavo.

    Ordena por retorno *total*: precio mas dividendos menos costos. Una especie
    que subio poco pero paga buen dividendo rinde mas de lo que aparenta en la
    columna de precio.
    """
    table = Table(title="Como viene cada una", header_style="bold cyan", expand=True)
    table.add_column("Especie")
    table.add_column("", justify="center", width=23, no_wrap=True)
    table.add_column("Total", justify="right")
    table.add_column("Resultado", justify="right")
    table.add_column("Dividendos", justify="right")
    table.add_column("Desde", justify="center")

    ranked = [
        (p, p.total_return_pct)
        for p in positions
        if p.total_return_pct is not None
    ]
    ranked.sort(key=lambda item: -item[1])
    top = max((abs(r) for _, r in ranked), default=0.0)
    for position, ratio in ranked:
        table.add_row(
            Text(position.ticker, style="bold"),
            _diverging_bar(ratio, top),
            colored(ratio, pct(ratio)),
            colored(position.total_return_ars, money(position.total_return_ars)),
            money(position.income_ars) if position.income_ars else Text("-", style="dim"),
            position.first_buy.isoformat() if position.first_buy else "-",
        )
    return table


def monthly_table(months: Sequence[MonthPoint], limit: int = 18) -> Table:
    """Mes a mes: cuanto metiste, cuanto llevas puesto y cuanto hay arriba."""
    table = Table(
        title="Mes a mes: capital aportado y ganancia",
        header_style="bold cyan",
        expand=True,
    )
    for col, justify in (
        ("Mes", "left"), ("Aportaste", "right"), ("Capital acumulado", "right"),
        ("Valor", "right"), ("Ganancia", "right"), ("%", "right"), ("En USD", "right"),
    ):
        table.add_column(col, justify=justify)
    for m in list(months)[-limit:]:
        table.add_row(
            m.month.strftime("%Y-%m"),
            money(m.deposits) if m.deposits else Text("-", style="dim"),
            money(m.contributed),
            money(m.value),
            colored(m.gain, money(m.gain)),
            colored(m.gain_pct, pct(m.gain_pct)),
            money(m.value_usd, "US$", 0) if m.value_usd else "-",
        )
    return table


def positions_table(positions: Sequence[Position], *, title: str = "Posiciones abiertas") -> Table:
    table = Table(title=title, header_style="bold cyan", expand=True)
    table.add_column("Ticker")
    table.add_column("Desde", justify="center")
    table.add_column("Tiempo", justify="right")
    table.add_column("Cant.", justify="right")
    table.add_column("PPC", justify="right")
    table.add_column("Precio", justify="right")
    table.add_column("Valuacion", justify="right")
    table.add_column("Result. no real.", justify="right")
    table.add_column("%", justify="right")
    table.add_column("TIR a.", justify="right")
    table.add_column("TIR USD", justify="right")

    for p in positions:
        pnl_pct = (p.unrealized_pnl / p.cost_basis) if (p.unrealized_pnl is not None and p.cost_basis) else None
        table.add_row(
            Text(p.ticker, style="bold") + (Text(" *", style="yellow") if p.stale_price else Text("")),
            p.first_buy.isoformat() if p.first_buy else "-",
            _days(p.holding_days),
            f"{p.quantity:,.2f}",
            money(p.avg_cost),
            money(p.price),
            money(p.market_value),
            colored(p.unrealized_pnl, money(p.unrealized_pnl)),
            colored(pnl_pct, pct(pnl_pct)),
            colored(p.xirr, pct(p.xirr)),
            colored(p.xirr_usd, pct(p.xirr_usd)),
        )
    return table


def closed_positions_table(positions: Sequence[Position]) -> Table:
    table = Table(title="Posiciones cerradas", header_style="bold cyan", expand=True)
    for col, justify in (
        ("Ticker", "left"), ("Primera compra", "center"), ("Ultima op.", "center"),
        ("Invertido", "right"), ("Recuperado", "right"), ("Result. realizado", "right"),
        ("Dividendos", "right"), ("Costos", "right"), ("TIR a.", "right"),
    ):
        table.add_column(col, justify=justify)
    for p in positions:
        invested = p.cost_basis or 0.0
        table.add_row(
            Text(p.ticker, style="bold"),
            p.first_buy.isoformat() if p.first_buy else "-",
            p.last_buy.isoformat() if p.last_buy else "-",
            money(invested),
            money(p.realized_pnl + invested),
            colored(p.realized_pnl, money(p.realized_pnl)),
            money(p.income_ars),
            money(-p.fees_ars),
            colored(p.xirr, pct(p.xirr)),
        )
    return table


def trades_table(trades: Sequence[ClosedTrade], limit: int | None = None) -> Table:
    table = Table(title="Operaciones cerradas (apareo FIFO)", header_style="bold cyan", expand=True)
    for col in ("Ticker", "Compra", "Venta", "Dias", "Cant.", "Costo unit.", "Venta unit.", "Resultado", "%"):
        table.add_column(col, justify="right" if col not in ("Ticker",) else "left")
    rows = sorted(trades, key=lambda t: t.close_date, reverse=True)
    if limit:
        rows = rows[:limit]
    for t in rows:
        table.add_row(
            Text(t.ticker, style="bold"),
            t.open_date.isoformat(),
            t.close_date.isoformat(),
            str(t.holding_days),
            f"{t.quantity:,.2f}",
            money(t.unit_cost),
            money(t.unit_proceeds),
            colored(t.pnl, money(t.pnl)),
            colored(t.pnl_pct, pct(t.pnl_pct)),
        )
    return table


def lots_table(position: Position) -> Table:
    table = Table(title=f"Lotes abiertos de {position.ticker}", header_style="bold cyan")
    for col in ("Compra", "Antiguedad", "Cantidad", "Costo unit.", "Invertido", "Var. vs hoy"):
        table.add_column(col, justify="right" if col != "Compra" else "left")
    for lot in position.lots:
        table.add_row(
            lot["open_date"].isoformat(),
            _days(lot["days"]),
            f"{lot['quantity']:,.2f}",
            money(lot["unit_cost"]),
            money(lot["cost"]),
            colored(lot["pnl_pct"], pct(lot["pnl_pct"])),
        )
    return table


def short_currency(label: str | None) -> str:
    """Nombre corto del bolsillo para que entre en una columna: MEP, CABLE, ARS."""
    name = normalize_currency(label)
    if name == PESOS:
        return "ARS"
    return name.replace("DOLAR", "").replace(".", "").strip() or "USD"


def conversions_table(conversions: Sequence[FxConversion], limit: int | None = None) -> Table:
    """Compras de dolar MEP/CCL con el tipo de cambio que pagaste en cada una."""
    table = Table(
        title="Conversiones de moneda (dolar MEP/CCL)", header_style="bold cyan", expand=True
    )
    for col, justify in (
        ("Fecha", "left"), ("Especie", "left"), ("Ruta", "left"), ("Nominales", "right"),
        ("Sale", "right"), ("Entra", "right"), ("Tipo de cambio", "right"), ("Dias", "right"),
    ):
        table.add_column(col, justify=justify)
    rows = sorted(conversions, key=lambda c: c.from_date, reverse=True)
    if limit:
        rows = rows[:limit]
    for c in rows:
        out_symbol = "$" if is_ars(c.from_currency) else "US$"
        in_symbol = "$" if is_ars(c.to_currency) else "US$"
        table.add_row(
            c.from_date.isoformat(),
            Text(c.ticker, style="bold") + (Text("" if c.matched_by == "cantidad" else " canje", style="dim")),
            f"{short_currency(c.from_currency)} -> {short_currency(c.to_currency)}",
            f"{c.from_quantity:,.0f}",
            money(c.from_amount, out_symbol),
            money(c.to_amount, in_symbol),
            Text(money(c.rate), style="bold cyan") if c.is_fx_purchase else Text("-", style="dim"),
            str(c.days),
        )
    return table


def warnings_panel(report: PortfolioReport) -> Panel | None:
    items: list[str] = list(report.warnings)
    if report.unclassified_events:
        items.append(
            f"{len(report.unclassified_events)} movimientos sin clasificar "
            f"(ver `wallet-tracker movimientos --sin-clasificar`)."
        )
    stale = [p.ticker for p in report.positions if p.stale_price]
    if stale:
        items.append(f"Precio desactualizado en: {', '.join(stale)} (marcados con *).")
    if not items:
        return None
    shown = items[:12]
    extra = len(items) - len(shown)
    text = "\n".join(f"· {i}" for i in shown)
    if extra > 0:
        text += f"\n· ... y {extra} avisos mas"
    return Panel(text, title="[yellow]Avisos[/]", border_style="yellow")


def render_dashboard(
    report: PortfolioReport,
    console: Console,
    *,
    trades_limit: int = 12,
    conversions_limit: int = 12,
    full: bool = False,
    advanced: bool = False,
) -> None:
    """El panel por defecto responde por la cartera.

    Las compras de dolar MEP/CCL no son inversion: no generan posicion ni
    resultado, asi que solo aparecen con `full` (el flag --todo).
    """
    aviso = watch_panel(report)
    if aviso:
        console.print(aviso)
    console.print(summary_panel(report, advanced=advanced))
    if report.positions:
        console.print(performance_table(report.positions))
        console.print(positions_table(report.positions))
    if report.investing and len(report.investing.months) > 1:
        console.print(monthly_table(report.investing.months))
    if report.closed_positions:
        console.print(closed_positions_table(report.closed_positions))
    if report.closed_trades:
        console.print(trades_table(report.closed_trades, limit=trades_limit))
    if report.fx_conversions:
        if full:
            console.print(conversions_table(report.fx_conversions, limit=conversions_limit))
        else:
            console.print(
                f"[dim]Ademas compraste {money(report.fx_bought_usd, 'US$')} de dolar "
                f"MEP/CCL en {len(report.fx_purchases)} operaciones a "
                f"{money(report.fx_weighted_rate)} promedio. No cuentan como inversion; "
                f"vela con [/][bold]--todo[/][dim].[/]"
            )
    panel = warnings_panel(report)
    if panel:
        console.print(panel)


def allocation_table(rows: Sequence, total_actual: float, amount: float, *,
                     con_objetivo: bool, prices: dict[str, float] | None = None,
                     commission: float = 0.0) -> Table:
    """Que orden cargar en el broker para acercarte a tu objetivo."""
    fuente = "objetivo.json" if con_objetivo else "partes iguales (no hay objetivo.json)"
    table = Table(
        title=f"Repartir {money(amount)} · objetivo: {fuente}",
        header_style="bold cyan",
        expand=True,
    )
    for col, justify in (
        ("Especie", "left"), ("Tenes", "right"), ("Objetivo", "right"),
        ("Nominales", "right"), ("Poner en PPI", "right"), ("Queda en", "right"),
    ):
        table.add_column(col, justify=justify)

    prices = prices or {}
    final = total_actual + amount
    total_pagar = 0.0
    for row in rows:
        precio = prices.get(row.ticker)
        unidades = row.units(precio, commission)
        pagar = row.cost(precio, commission)
        total_pagar += pagar
        if not unidades:
            table.add_row(
                Text(row.ticker, style="dim"), pct(row.current_weight(total_actual)),
                pct(row.target_weight), Text("-", style="dim"),
                Text("ya esta arriba" if row.amount <= 0 else "no alcanza", style="dim"),
                pct(row.final_weight(final)),
            )
            continue
        table.add_row(
            Text(row.ticker, style="bold"),
            pct(row.current_weight(total_actual)),
            pct(row.target_weight),
            Text(f"{unidades}", style="bold") + Text(f" x {money(precio)}", style="dim"),
            Text(money(pagar), style="bold green"),
            pct(row.final_weight(final)),
        )
    table.add_section()
    sobra = amount - total_pagar
    table.add_row(
        "", "", "", Text("total", style="dim"), Text(money(total_pagar), style="bold"),
        Text(f"sobran {money(sobra)}", style="dim"),
    )
    return table


def movements_table(rows: Iterable, title: str = "Movimientos") -> Table:
    table = Table(title=title, header_style="bold cyan", expand=True)
    for col in ("Fecha", "Categoria", "Ticker", "Cant.", "Importe", "Descripcion"):
        table.add_column(col, justify="right" if col in ("Cant.", "Importe") else "left")
    for event in rows:
        table.add_row(
            event.date.isoformat() if isinstance(event.date, date) else str(event.date),
            event.category,
            event.ticker or "-",
            f"{event.quantity:,.2f}" if event.quantity else "-",
            colored(event.cash_flow, money(event.cash_flow)),
            event.description[:70],
        )
    return table
