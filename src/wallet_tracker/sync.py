"""Descarga incremental de la cuenta PPI hacia la base local."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta
from typing import Any, Callable, Iterable

from .config import Settings
from .db import dumps, get_state, insert_many, set_state, upsert
from .ledger import clean_ticker
from .ppi_api import (
    PPIError,
    PPISession,
    normalize_movements,
    normalize_orders,
    normalize_snapshot,
)

Logger = Callable[[str], None]

#: Al re-sincronizar volvemos unos dias para atras: PPI puede publicar
#: movimientos con fecha retroactiva (liquidaciones, ajustes).
OVERLAP_DAYS = 15


def _noop(_: str) -> None:
    pass


def _tickers_in_db(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT DISTINCT ticker FROM movements WHERE ticker IS NOT NULL "
        "UNION SELECT DISTINCT ticker FROM snapshots WHERE ticker IS NOT NULL "
        "UNION SELECT DISTINCT ticker FROM orders WHERE ticker IS NOT NULL"
    ).fetchall()
    # `clean_ticker` filtra el centinela "TICKER NOT FOUND" que puede haber
    # quedado guardado por versiones anteriores.
    return {t for t in (clean_ticker(r["ticker"]) for r in rows) if t}


def _type_hints(conn: sqlite3.Connection) -> dict[str, tuple[str, str]]:
    """Tipo y moneda de cada especie segun tus propias tenencias y ordenes.

    Esto *manda* sobre lo que devuelve el buscador de instrumentos de la API.
    Muchos tickers existen dos veces: "AAPL" es una accion de NYSE en dolares y
    tambien un CEDEAR de BYMA en pesos, y la busqueda por ticker devuelve la
    primera. Si le creemos, despues bajamos la serie de precios del papel
    equivocado y valuamos 46 CEDEARs a US$309 en vez de $24.770. La foto de
    tenencias, en cambio, dice exactamente que tenes vos.
    """
    hints: dict[str, tuple[str, str]] = {}
    for row in conn.execute(
        "SELECT ticker, group_name, currency FROM snapshots WHERE ticker IS NOT NULL "
        "AND group_name IS NOT NULL AND ts = (SELECT MAX(ts) FROM snapshots)"
    ):
        hints.setdefault(row["ticker"], (row["group_name"], row["currency"] or ""))
    for row in conn.execute(
        "SELECT ticker, instrument_type, currency FROM orders WHERE ticker IS NOT NULL "
        "AND instrument_type IS NOT NULL"
    ):
        hints.setdefault(row["ticker"], (row["instrument_type"], row["currency"] or ""))
    return hints


def sync_accounts(session: PPISession, conn: sqlite3.Connection, log: Logger = _noop) -> str:
    accounts = session.accounts()
    upsert(
        conn,
        "accounts",
        [
            {
                "account_number": str(a.get("accountNumber")),
                "name": a.get("name"),
                "raw": dumps(a),
            }
            for a in accounts
            if a.get("accountNumber")
        ],
    )
    account = session.resolve_account_number()
    log(f"Cuenta: {account}")
    return account


def sync_movements(
    session: PPISession,
    conn: sqlite3.Connection,
    account: str,
    start: date,
    end: date | None = None,
    log: Logger = _noop,
) -> int:
    end = end or date.today()
    log(f"Movimientos {start} -> {end} ...")
    raw = session.movements(account, start, end)
    rows = normalize_movements(account, raw)
    written = upsert(conn, "movements", rows)
    set_state(conn, "movements_synced_until", end.isoformat())
    log(f"  {written} movimientos guardados")
    return written


def sync_orders(
    session: PPISession,
    conn: sqlite3.Connection,
    account: str,
    start: date,
    end: date | None = None,
    log: Logger = _noop,
) -> int:
    end = end or date.today()
    log(f"Ordenes {start} -> {end} ...")
    try:
        raw = session.orders(account, start, end)
    except PPIError as exc:
        log(f"  aviso: no se pudieron traer ordenes ({exc})")
        return 0
    rows = normalize_orders(account, raw)
    written = upsert(conn, "orders", rows)
    set_state(conn, "orders_synced_until", end.isoformat())
    log(f"  {written} ordenes guardadas")
    return written


def sync_snapshot(
    session: PPISession, conn: sqlite3.Connection, account: str, log: Logger = _noop
) -> int:
    log("Tenencias actuales ...")
    payload = session.balances_and_positions(account)
    ts = datetime.now().isoformat(timespec="seconds")
    rows = normalize_snapshot(account, payload, ts)
    conn.execute("DELETE FROM snapshots WHERE ts = ?", (ts,))
    insert_many(conn, "snapshots", rows)
    set_state(conn, "last_snapshot", ts)
    log(f"  {len(rows)} lineas de tenencia")
    return len(rows)


def sync_instruments(
    session: PPISession, conn: sqlite3.Connection, log: Logger = _noop
) -> int:
    stored = {
        r["ticker"]: (r["type"] or "", r["currency"] or "")
        for r in conn.execute("SELECT ticker, type, currency FROM instruments")
    }
    hints = _type_hints(conn)

    # Se re-resuelve lo que falta y tambien lo que quedo guardado con un tipo
    # que contradice a tus tenencias (bases creadas antes de esta correccion).
    from .binance_sync import crypto_tickers

    ajenas = crypto_tickers(conn)
    pending = sorted(
        t
        for t in _tickers_in_db(conn) - ajenas
        if t not in stored or (t in hints and hints[t][0] and stored[t][0] != hints[t][0])
    )
    if not pending:
        return 0
    log(f"Instrumentos: resolviendo {len(pending)} especies ...")
    rows: list[dict[str, Any]] = []
    retyped: list[str] = []
    for ticker in pending:
        info = None
        try:
            info = session.instrument_info(ticker)
        except PPIError as exc:
            log(f"  {ticker}: no se pudo resolver ({exc})")
        hint_type, hint_currency = hints.get(ticker, ("", ""))
        resolved_type = hint_type or (info.type if info and info.type else "")
        resolved_currency = hint_currency or (info.currency if info else "")
        if info and hint_type and info.type and info.type != hint_type:
            log(f"  {ticker}: la API dice {info.type}, tus tenencias dicen {hint_type} (mandan las tenencias)")
        if stored.get(ticker, ("", ""))[0] != resolved_type:
            retyped.append(ticker)
        rows.append(
            {
                "ticker": ticker,
                "description": info.description if info else "",
                "currency": resolved_currency,
                "type": resolved_type,
                "market": info.market if info else "",
                "raw": dumps(
                    {
                        "api": info.__dict__ if info else None,
                        "hint": {"type": hint_type, "currency": hint_currency},
                    }
                ),
            }
        )
    upsert(conn, "instruments", rows)

    # Si cambio el tipo, la serie de precios que teniamos es la del papel
    # equivocado: se borra para que se vuelva a bajar completa.
    for ticker in retyped:
        if stored.get(ticker):
            conn.execute("DELETE FROM prices WHERE ticker = ?", (ticker,))
            log(f"  {ticker}: cambio de tipo, se descarta la serie de precios anterior")
    log(f"  {len(rows)} instrumentos actualizados")
    return len(rows)


def _price_window(conn: sqlite3.Connection, ticker: str, fallback_start: date) -> tuple[date, date] | None:
    """Rango de fechas de precios que falta bajar para un ticker."""
    row = conn.execute(
        "SELECT MIN(agreement_date) AS first_date FROM movements WHERE ticker = ?", (ticker,)
    ).fetchone()
    first = date.fromisoformat(row["first_date"]) if row and row["first_date"] else fallback_start
    have = conn.execute(
        "SELECT MIN(date) AS mn, MAX(date) AS mx FROM prices WHERE ticker = ?", (ticker,)
    ).fetchone()
    start = first - timedelta(days=7)
    end = date.today()
    if have and have["mx"]:
        last_have = date.fromisoformat(have["mx"])
        first_have = date.fromisoformat(have["mn"])
        if first_have <= start and last_have >= end - timedelta(days=1):
            return None
        if first_have <= start:
            start = last_have  # solo el tramo nuevo
    return (start, end)


def sync_prices(
    session: PPISession,
    conn: sqlite3.Connection,
    fallback_start: date,
    tickers: Iterable[str] | None = None,
    log: Logger = _noop,
) -> int:
    types = {
        r["ticker"]: (r["type"] or "")
        for r in conn.execute("SELECT ticker, type FROM instruments")
    }
    from .binance_sync import crypto_tickers

    # Las cripto las trae Binance: pedirselas a PPI da error y ensucia el log.
    ajenas = crypto_tickers(conn)
    targets = sorted((set(tickers) if tickers else _tickers_in_db(conn)) - ajenas)
    total = 0
    log(f"Precios historicos de {len(targets)} especies ...")
    for ticker in targets:
        window = _price_window(conn, ticker, fallback_start)
        if window is None:
            continue
        start, end = window
        instrument_type = types.get(ticker) or ""
        if not instrument_type:
            log(f"  {ticker}: sin tipo de instrumento conocido, se omite")
            continue
        try:
            rows = session.price_history(ticker, instrument_type, start, end)
        except PPIError as exc:
            log(f"  {ticker}: error bajando precios ({exc})")
            continue
        if not rows:
            log(f"  {ticker}: sin datos de precio para {instrument_type}")
            continue
        total += upsert(conn, "prices", rows)
    log(f"  {total} precios guardados")
    return total


def sync_fx(
    session: PPISession,
    conn: sqlite3.Connection,
    settings: Settings,
    start: date,
    log: Logger = _noop,
) -> int:
    """Serie del dolar implicito = precio en pesos / precio en dolares del mismo bono."""
    ars_t, usd_t = settings.ccl_ticker_ars, settings.ccl_ticker_usd
    log(f"Dolar implicito con {ars_t}/{usd_t} ...")
    end = date.today()
    try:
        ars = session.price_history(ars_t, settings.ccl_instrument_type, start, end, settings.ccl_settlement)
        usd = session.price_history(usd_t, settings.ccl_instrument_type, start, end, settings.ccl_settlement)
    except PPIError as exc:
        log(f"  no se pudo calcular el dolar implicito ({exc})")
        return 0
    if not ars or not usd:
        log("  sin datos suficientes para el dolar implicito")
        return 0
    upsert(conn, "prices", ars)
    upsert(conn, "prices", usd)
    usd_by_date = {r["date"]: r["price"] for r in usd if r["price"]}
    rows = [
        {"date": r["date"], "ccl": r["price"] / usd_by_date[r["date"]], "source": f"{ars_t}/{usd_t}"}
        for r in ars
        if r["price"] and usd_by_date.get(r["date"])
    ]
    written = upsert(conn, "fx", rows)
    log(f"  {written} cotizaciones de dolar implicito")
    return written


def full_sync(
    settings: Settings,
    conn: sqlite3.Connection,
    *,
    since: date | None = None,
    with_prices: bool = True,
    log: Logger = _noop,
) -> dict[str, int]:
    """Sincronizacion completa. Incremental salvo que se pase `since`."""
    session = PPISession(settings).login()
    account = sync_accounts(session, conn, log)

    if since is None:
        last = get_state(conn, "movements_synced_until")
        since = (
            date.fromisoformat(last) - timedelta(days=OVERLAP_DAYS)
            if last
            else settings.history_start
        )
        since = max(since, settings.history_start)

    stats = {
        "movements": sync_movements(session, conn, account, since, log=log),
        "orders": sync_orders(session, conn, account, since, log=log),
        "snapshot": sync_snapshot(session, conn, account, log=log),
    }
    conn.commit()
    stats["instruments"] = sync_instruments(session, conn, log=log)
    conn.commit()
    if with_prices:
        stats["prices"] = sync_prices(session, conn, settings.history_start, log=log)
        stats["fx"] = sync_fx(session, conn, settings, settings.history_start, log=log)
    conn.commit()
    set_state(conn, "last_sync", datetime.now().isoformat(timespec="seconds"))
    return stats
