"""Interfaz de linea de comandos."""

from __future__ import annotations

import csv
import shutil
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from . import __version__
from .analysis import build_report
from .config import ENV_TEMPLATE, Settings, load_settings
from .console import (
    allocation_table,
    lots_table,
    movements_table,
    positions_table,
    render_dashboard,
    summary_panel,
    trades_table,
)
from .db import connect, get_state, init_db
from .ledger import Rules

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Seguimiento de tu cartera: PPI y Binance en un solo lugar.",
)
console = Console()

RULES_FILE = "rules.json"
TARGETS_FILE = "objetivo.json"

#: Nombre del archivo de la base de ejemplo. Vive al lado de la base real pero
#: nunca es la misma: mezclar una cartera sintetica con la cuenta de verdad
#: arruina todos los totales y no hay forma de separarlas despues.
DEMO_DB_NAME = "demo.db"

DEMO_FLAG = "demo"


def _demo_db_path(settings: Settings) -> Path:
    return settings.db_path.with_name(DEMO_DB_NAME)


def _settings(
    env: Optional[Path] = None, db: Optional[Path] = None, demo: bool = False
) -> Settings:
    settings = load_settings(env)
    if db:
        object.__setattr__(settings, "db_path", db)
    elif demo:
        object.__setattr__(settings, "db_path", _demo_db_path(settings))
    return settings


def _open_db(settings: Settings) -> sqlite3.Connection:
    conn = connect(settings.db_path)
    init_db(conn)
    return conn


def _sync_binance(settings: Settings, conn: sqlite3.Connection, log) -> dict:
    """Trae la cartera de Binance si hay claves. Sin claves, no hace nada."""
    if not settings.has_binance:
        return {}
    from .binance_api import BinanceError
    from .binance_sync import full_sync as binance_sync

    try:
        return {f"binance_{k}": v for k, v in binance_sync(settings, conn, log=log).items()}
    except BinanceError as exc:
        # El 451 es la restriccion geografica de Binance, no un problema del
        # programa: conviene decirlo asi y no escupir el JSON de la respuesta.
        if "HTTP 451" in str(exc):
            console.print(
                "[yellow]Binance no responde desde tu ubicacion[/] (restriccion "
                "geografica). El resto del reporte se arma igual, con los ultimos "
                "datos de cripto que se pudieron bajar."
            )
        else:
            console.print(f"[yellow]Binance:[/] no se pudo sincronizar ({str(exc)[:120]})")
        return {}


def _is_demo_db(conn: sqlite3.Connection) -> bool:
    return get_state(conn, DEMO_FLAG) == "true"


def _has_movements(conn: sqlite3.Connection) -> bool:
    return bool(conn.execute("SELECT 1 FROM movements LIMIT 1").fetchone())


def _rules() -> Rules:
    return Rules.load(RULES_FILE if Path(RULES_FILE).exists() else None)


def _report(settings: Settings, as_of: Optional[str] = None):
    conn = _open_db(settings)
    if not _has_movements(conn):
        console.print(
            f"[yellow]La base {settings.db_path} esta vacia.[/] Corre "
            "[bold]wallet-tracker sync[/] para bajar tu historial, o "
            "[bold]wallet-tracker demo[/] para probar con datos de ejemplo."
        )
        raise typer.Exit(code=1)
    if _is_demo_db(conn):
        console.print(
            f"[yellow]Datos de ejemplo[/] ({settings.db_path}): cartera sintetica, "
            "no es tu cuenta real."
        )
    day = date.fromisoformat(as_of) if as_of else date.today()
    return conn, build_report(conn, rules=_rules(), as_of=day, benchmark=settings.benchmark)


@app.command()
def version() -> None:
    """Muestra la version."""
    console.print(f"wallet-tracker {__version__}")


@app.command()
def init(
    env: Optional[Path] = typer.Option(None, "--env", help="Ruta al archivo .env"),
) -> None:
    """Crea el .env a partir del ejemplo y prepara la base local."""
    target = Path(env or ".env")
    example = Path(".env.example")
    if target.exists():
        console.print(f"[yellow]{target} ya existe, no se toca.[/]")
    else:
        if example.exists():
            shutil.copy(example, target)
        else:
            target.write_text(ENV_TEMPLATE, encoding="utf-8")
        console.print(f"[green]Creado {target}[/] - completa PPI_PUBLIC_KEY y PPI_PRIVATE_KEY.")
    settings = _settings(env)
    _open_db(settings).close()
    console.print(f"Base lista en [bold]{settings.db_path}[/]")
    console.print(
        "\nPara obtener las credenciales: entra a tu cuenta PPI -> pestana "
        "[bold]Gestiones[/] -> activar [bold]servicio API[/] y generar las claves."
    )


@app.command()
def sync(
    desde: Optional[str] = typer.Option(None, "--desde", help="Fecha inicial YYYY-MM-DD (fuerza resync)"),
    sin_precios: bool = typer.Option(False, "--sin-precios", help="No bajar series de precios"),
    env: Optional[Path] = typer.Option(None, "--env"),
    db: Optional[Path] = typer.Option(None, "--db"),
) -> None:
    """Descarga movimientos, ordenes, tenencias y precios desde PPI."""
    from .sync import full_sync  # import diferido: solo aca hace falta la red

    settings = _settings(env, db)
    if not settings.has_credentials:
        console.print(
            "[red]Faltan credenciales.[/] Completa PPI_PUBLIC_KEY y PPI_PRIVATE_KEY en el .env.\n"
            "Se generan desde tu cuenta PPI -> pestana Gestiones -> servicio API."
        )
        raise typer.Exit(code=1)
    conn = _open_db(settings)
    if _is_demo_db(conn):
        console.print(
            f"[red]{settings.db_path} es una base de ejemplo[/] y no se puede sincronizar: "
            "los datos sinteticos quedarian mezclados con los reales y no hay forma de "
            "separarlos despues.\n"
            f"Borrala ([bold]rm {settings.db_path}*[/]) o sincroniza contra otra "
            "([bold]--db[/])."
        )
        raise typer.Exit(code=1)
    since = date.fromisoformat(desde) if desde else None
    with console.status("Sincronizando..."):
        stats = full_sync(settings, conn, since=since, with_prices=not sin_precios,
                          log=lambda m: console.log(m))
        stats.update(_sync_binance(settings, conn, log=lambda m: console.log(m)))
    conn.commit()
    console.print(f"[green]Listo.[/] {stats}")


@app.command()
def demo(
    db: Optional[Path] = typer.Option(None, "--db", help=f"Base a escribir (default: {DEMO_DB_NAME} junto a la real)"),
    forzar: bool = typer.Option(False, "--forzar", help="Escribir aunque la base tenga datos reales"),
    env: Optional[Path] = typer.Option(None, "--env"),
) -> None:
    """Carga una cartera sintetica en una base aparte, para probar sin credenciales."""
    from .demo import seed

    settings = _settings(env, db, demo=True)
    conn = _open_db(settings)
    if _has_movements(conn) and not _is_demo_db(conn) and not forzar:
        console.print(
            f"[red]{settings.db_path} tiene movimientos reales[/] y no se va a pisar con "
            "datos de ejemplo.\n"
            "Corre [bold]wallet-tracker demo[/] sin [bold]--db[/] para usar la base de "
            "ejemplo, o agrega [bold]--forzar[/] si de verdad queres borrar esa base."
        )
        raise typer.Exit(code=1)
    conn.executescript(
        "DELETE FROM movements; DELETE FROM prices; DELETE FROM fx; "
        "DELETE FROM instruments; DELETE FROM snapshots; DELETE FROM accounts; "
        "DELETE FROM orders; DELETE FROM sync_state;"
    )
    stats = seed(conn)
    conn.commit()
    console.print(f"[green]Datos de ejemplo cargados[/] en {settings.db_path}: {stats}")
    console.print("Proba: [bold]wallet-tracker resumen --demo[/]")


@app.command()
def ver(
    salida: Path = typer.Option(Path("reports/cartera.html"), "--salida", "-o"),
    sin_bajar: bool = typer.Option(False, "--sin-bajar", help="No sincronizar, usar lo que ya hay"),
    env: Optional[Path] = typer.Option(None, "--env"),
    db: Optional[Path] = typer.Option(None, "--db"),
) -> None:
    """Sincroniza, arma el reporte y lo abre. Es el unico comando que necesitas."""
    from .report import write_report

    settings = _settings(env, db)
    conn = _open_db(settings)
    if _is_demo_db(conn):
        console.print(f"[yellow]{settings.db_path} es la base de ejemplo.[/]")
    elif not sin_bajar and settings.has_credentials:
        from .sync import full_sync

        with console.status("Actualizando..."):
            full_sync(settings, conn, log=lambda m: None)
            _sync_binance(settings, conn, log=lambda m: None)
        conn.commit()
    elif not sin_bajar:
        console.print("[yellow]Sin credenciales:[/] se usa lo que ya hay en la base.")

    _, report = _report(settings)
    path = write_report(report, salida, settings.commission)
    console.print(f"[green]Listo.[/] {path}")
    typer.launch(str(path))


@app.command()
def resumen(
    fecha: Optional[str] = typer.Option(None, "--fecha", help="Valuar a una fecha (YYYY-MM-DD)"),
    todo: bool = typer.Option(False, "--todo", help="Incluir las compras de dolar MEP/CCL"),
    avanzado: bool = typer.Option(False, "--avanzado", help="Agregar TIR, TWR y volatilidad"),
    env: Optional[Path] = typer.Option(None, "--env"),
    db: Optional[Path] = typer.Option(None, "--db"),
    demo: bool = typer.Option(False, "--demo", help="Leer la base de ejemplo en vez de la real"),
) -> None:
    """Panorama de la cartera: cuanto tenes, cuanto pusiste y como viene."""
    settings = _settings(env, db, demo)
    conn, report = _report(settings, fecha)
    render_dashboard(report, console, full=todo, advanced=avanzado)
    last = get_state(conn, "last_sync")
    if last:
        console.print(f"[dim]Ultima sincronizacion: {last}[/]")


@app.command()
def posiciones(
    cerradas: bool = typer.Option(False, "--cerradas", help="Mostrar solo posiciones cerradas"),
    env: Optional[Path] = typer.Option(None, "--env"),
    db: Optional[Path] = typer.Option(None, "--db"),
    demo: bool = typer.Option(False, "--demo", help="Leer la base de ejemplo en vez de la real"),
) -> None:
    """Tabla de tenencias con costo, resultado y antiguedad."""
    settings = _settings(env, db, demo)
    _, report = _report(settings)
    if cerradas:
        from .console import closed_positions_table

        console.print(closed_positions_table(report.closed_positions))
    else:
        console.print(positions_table(report.positions))


@app.command()
def instrumento(
    ticker: str = typer.Argument(..., help="Ticker a analizar, ej. GGAL"),
    env: Optional[Path] = typer.Option(None, "--env"),
    db: Optional[Path] = typer.Option(None, "--db"),
    demo: bool = typer.Option(False, "--demo", help="Leer la base de ejemplo en vez de la real"),
) -> None:
    """Historia completa de una especie: lotes, operaciones y resultado."""
    settings = _settings(env, db, demo)
    _, report = _report(settings)
    ticker = ticker.upper()
    matches = [p for p in report.positions + report.closed_positions if p.ticker == ticker]
    if not matches:
        console.print(f"[yellow]No hay operaciones de {ticker} en la base.[/]")
        raise typer.Exit(code=1)
    for position in matches:
        console.print(positions_table([position], title=f"{ticker} - {position.description}"))
        if position.lots:
            console.print(lots_table(position))
        trades = [t for t in report.closed_trades if t.ticker == ticker]
        if trades:
            console.print(trades_table(trades))
        events = [e for e in report.events if e.ticker == ticker]
        if events:
            console.print(movements_table(events, title=f"Movimientos de {ticker}"))


@app.command()
def movimientos(
    sin_clasificar: bool = typer.Option(False, "--sin-clasificar", help="Solo los que ninguna regla interpreto"),
    categoria: Optional[str] = typer.Option(None, "--categoria", help="Filtrar por categoria, ej. BUY"),
    ticker: Optional[str] = typer.Option(None, "--ticker"),
    limite: int = typer.Option(50, "--limite"),
    env: Optional[Path] = typer.Option(None, "--env"),
    db: Optional[Path] = typer.Option(None, "--db"),
    demo: bool = typer.Option(False, "--demo", help="Leer la base de ejemplo en vez de la real"),
) -> None:
    """Lista los movimientos ya interpretados por el clasificador."""
    settings = _settings(env, db, demo)
    _, report = _report(settings)
    events = report.unclassified_events if sin_clasificar else report.events
    if categoria:
        events = [e for e in events if e.category == categoria.upper()]
    if ticker:
        events = [e for e in events if (e.ticker or "") == ticker.upper()]
    events = sorted(events, key=lambda e: e.date, reverse=True)[:limite]
    console.print(movements_table(events))
    if sin_clasificar and events:
        console.print(
            "[dim]Podes agregar reglas propias en rules.json: "
            '[{"pattern": "texto o regex", "category": "FEE"}][/]'
        )


@app.command()
def reporte(
    salida: Path = typer.Option(Path("reports/reporte.html"), "--salida", "-o"),
    abrir: bool = typer.Option(False, "--abrir", help="Abrir el archivo al terminar"),
    env: Optional[Path] = typer.Option(None, "--env"),
    db: Optional[Path] = typer.Option(None, "--db"),
    demo: bool = typer.Option(False, "--demo", help="Leer la base de ejemplo en vez de la real"),
) -> None:
    """Genera un reporte HTML autocontenido con graficos."""
    from .report import write_report

    settings = _settings(env, db, demo)
    _, report = _report(settings)
    path = write_report(report, salida, settings.commission)
    console.print(f"[green]Reporte generado:[/] {path}")
    if abrir:
        typer.launch(str(path))


@app.command()
def aportar(
    monto: float = typer.Argument(..., help="Cuanta plata vas a poner, en pesos"),
    env: Optional[Path] = typer.Option(None, "--env"),
    db: Optional[Path] = typer.Option(None, "--db"),
    demo: bool = typer.Option(False, "--demo", help="Leer la base de ejemplo en vez de la real"),
) -> None:
    """Reparte un aporte nuevo para acercar la cartera a tu objetivo."""
    from .plan import allocate, equal_weights, load_targets

    settings = _settings(env, db, demo)
    _, report = _report(settings)
    holdings = {p.ticker: (p.market_value_ars or 0.0) for p in report.positions}
    if not holdings:
        console.print("[yellow]No hay posiciones abiertas para repartir el aporte.[/]")
        raise typer.Exit(code=1)

    targets_file = load_targets(TARGETS_FILE)
    targets = targets_file or equal_weights(holdings)
    rows = allocate(holdings, targets, monto)
    console.print(
        allocation_table(
            rows,
            sum(holdings.values()),
            monto,
            con_objetivo=bool(targets_file),
            prices={p.ticker: p.price for p in report.positions if p.price},
            commission=settings.commission,
        )
    )
    console.print(
        f"[dim]La columna [/][bold]Poner en PPI[/][dim] ya incluye la comision "
        f"({settings.commission:.2%}): es el numero que va en el campo de monto.\n"
        "Esto no dice que instrumento va a subir: dice donde poner la plata nueva para\n"
        "que la cartera se parezca al reparto que vos definiste, comprando y sin vender nada.\n"
        f"Para cambiar el objetivo, edita [/][bold]{TARGETS_FILE}[/][dim] "
        "(hay un ejemplo en objetivo.example.json).[/]"
    )


@app.command()
def exportar(
    destino: Path = typer.Option(Path("exports"), "--destino", "-o"),
    env: Optional[Path] = typer.Option(None, "--env"),
    db: Optional[Path] = typer.Option(None, "--db"),
    demo: bool = typer.Option(False, "--demo", help="Leer la base de ejemplo en vez de la real"),
) -> None:
    """Exporta posiciones, operaciones y movimientos a CSV."""
    settings = _settings(env, db, demo)
    _, report = _report(settings)
    destino.mkdir(parents=True, exist_ok=True)

    with (destino / "posiciones.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow([
            "ticker", "descripcion", "tipo", "moneda", "cantidad", "costo_unitario",
            "costo_total", "precio", "valuacion", "resultado_no_realizado",
            "resultado_realizado", "dividendos", "costos", "primera_compra",
            "dias_tenencia", "tir_anual", "tir_anual_usd",
        ])
        for p in report.positions + report.closed_positions:
            writer.writerow([
                p.ticker, p.description, p.instrument_type, p.currency, p.quantity,
                p.avg_cost, p.cost_basis, p.price, p.market_value, p.unrealized_pnl,
                p.realized_pnl, p.income, p.fees,
                p.first_buy.isoformat() if p.first_buy else "", p.holding_days,
                p.xirr, p.xirr_usd,
            ])

    with (destino / "operaciones.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["ticker", "compra", "venta", "dias", "cantidad", "costo_unit",
                         "venta_unit", "resultado", "resultado_pct"])
        for t in report.closed_trades:
            writer.writerow([t.ticker, t.open_date, t.close_date, t.holding_days, t.quantity,
                             t.unit_cost, t.unit_proceeds, t.pnl, t.pnl_pct])

    with (destino / "movimientos.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["fecha", "categoria", "ticker", "cantidad", "precio", "importe",
                         "moneda", "descripcion"])
        for e in report.events:
            writer.writerow([e.date, e.category, e.ticker or "", e.quantity, e.price,
                             e.cash_flow, e.currency, e.description])

    with (destino / "conversiones.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["fecha_compra", "fecha_venta", "dias", "ticker", "nominales_compra",
                         "nominales_venta", "moneda_origen", "importe_origen", "moneda_destino",
                         "importe_destino", "tipo_de_cambio", "apareo"])
        for c in report.fx_conversions:
            writer.writerow([c.from_date, c.to_date, c.days, c.ticker, c.from_quantity,
                             c.to_quantity, c.from_currency, c.from_amount, c.to_currency,
                             c.to_amount, c.rate, c.matched_by])

    with (destino / "valuacion_diaria.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["fecha", "especies_ars", "efectivo_ars", "total_ars", "ccl", "total_usd"])
        for point in report.nav_series:
            writer.writerow([point.date, point.instruments_ars, point.cash_ars, point.nav_ars,
                             point.ccl, point.nav_usd])

    console.print(f"[green]Exportado a[/] {destino}/ (5 archivos CSV)")


if __name__ == "__main__":
    app()
