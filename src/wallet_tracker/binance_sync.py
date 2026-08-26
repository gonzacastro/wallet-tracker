"""Descarga de la cuenta de Binance hacia la misma base local.

Las filas quedan con la forma que ya usa el resto del proyecto -- movimientos,
precios y foto de tenencias -- asi que desde el ledger para adelante nada sabe
ni le importa de donde vinieron. El motor FIFO, la valuacion, el repartidor de
aportes y el reporte tratan a BTC igual que a un CEDEAR.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta
from typing import Any, Callable

from .binance_api import (
    STABLECOINS,
    BinanceError,
    BinanceSession,
    normalize_balances,
    normalize_converts,
    normalize_deposits,
    normalize_trades,
    price_history,
    symbol_base,
)
from .config import Settings
from .db import dumps, get_state, insert_many, set_state, upsert

#: Numero de cuenta con el que se guardan las filas de Binance. Distinguirlas
#: de las de PPI permite, si algun dia hace falta, mirarlas por separado.
ACCOUNT = "BINANCE"

#: Desde cuando buscar precios si no se puede saber cuando compraste.
DEFAULT_START = date(2021, 1, 1)

#: Al re-sincronizar conversiones se vuelve unos dias atras, por las que
#: pudieran haber quedado a caballo de la ultima corrida.
CONVERT_OVERLAP = timedelta(days=3)

Logger = Callable[[str], None]


def _noop(_: str) -> None:
    pass


def _held_assets(session: BinanceSession) -> tuple[list, list[str]]:
    """Saldos de la cuenta y los activos que no son efectivo."""
    balances = session.balances()
    activos = sorted(b.asset for b in balances if not b.is_cash)
    return balances, activos


def full_sync(
    settings: Settings, conn: sqlite3.Connection, *, log: Logger = _noop
) -> dict[str, int]:
    """Trae saldos, operaciones y precios de Binance."""
    session = BinanceSession(settings.binance_key, settings.binance_secret)
    if not session.has_credentials:
        return {}

    # Las conversiones (el boton "Convert") no salen en las operaciones spot y
    # son la via mas comun para comprar: sin esto, una compra cambiaria el saldo
    # sin dejar rastro del costo.
    ultima = get_state(conn, "binance_converts_until")
    desde = (date.fromisoformat(ultima) - CONVERT_OVERLAP) if ultima else DEFAULT_START
    log(f"Binance: conversiones desde {desde} ...")
    escritas = 0
    try:
        # Se guarda el avance en cada ventana: si Binance corta a mitad del
        # barrido, la proxima corrida sigue desde donde llego y no desde 2021.
        for hasta, crudas in session.converts(desde):
            escritas += upsert(conn, "movements", normalize_converts(ACCOUNT, crudas))
            set_state(conn, "binance_converts_until", hasta.isoformat())
            conn.commit()
        log(f"  {escritas} conversiones")
    except BinanceError as exc:
        log(f"  interrumpido ({exc}); sigue la proxima vez desde donde quedo")

    log(f"Binance: depositos desde {desde} ...")
    try:
        deps, avisos = normalize_deposits(ACCOUNT, session.deposits(desde))
        upsert(conn, "movements", deps)
        for aviso in avisos:
            log(f"  {aviso}")
        log(f"  {len(deps)} depositos")
    except BinanceError as exc:
        deps = []
        log(f"  no se pudieron traer depositos ({exc})")

    log("Binance: saldos ...")
    balances, activos = _held_assets(session)
    if not activos:
        log("  sin criptomonedas en la cuenta")
    stats = {"movimientos": escritas + len(deps), "precios": 0}

    instrumentos: list[dict[str, Any]] = []
    precios_hoy: dict[str, float] = {}

    for asset in activos:
        symbol = session.find_symbol(asset)
        if not symbol:
            log(f"  {asset}: sin par contra stablecoin, se omite")
            continue

        # Operaciones: definen el costo y desde cuando lo tenes.
        try:
            crudas = session.trades(symbol)
        except BinanceError as exc:
            log(f"  {asset}: no se pudieron traer operaciones ({exc})")
            crudas = []
        filas = normalize_trades(ACCOUNT, symbol, crudas)
        stats["movimientos"] += upsert(conn, "movements", filas)

        # Precios desde la primera compra (o desde el default si no hay).
        desde = (
            date.fromisoformat(min(f["agreement_date"] for f in filas))
            if filas else DEFAULT_START
        )
        try:
            velas = price_history(symbol, desde)
        except BinanceError as exc:
            log(f"  {asset}: no se pudieron traer precios ({exc})")
            velas = []
        stats["precios"] += upsert(conn, "prices", velas)
        if velas:
            precios_hoy[asset] = velas[-1]["price"]

        instrumentos.append({
            "ticker": asset,
            "description": f"{asset} en Binance",
            "currency": symbol[len(asset):] or "USDT",
            "type": "CRIPTO",
            "market": "BINANCE",
            "raw": dumps({"symbol": symbol}),
        })
        log(f"  {asset}: {len(filas)} operaciones, {len(velas)} dias de precio")

    if instrumentos:
        upsert(conn, "instruments", instrumentos)

    ts = datetime.now().isoformat(timespec="seconds")
    conn.execute("DELETE FROM snapshots WHERE ts = ? AND account_number = ?", (ts, ACCOUNT))
    filas_saldo = normalize_balances(ACCOUNT, balances, precios_hoy, ts)
    insert_many(conn, "snapshots", filas_saldo)
    upsert(conn, "accounts", [{
        "account_number": ACCOUNT, "name": "Binance", "raw": dumps({"fuente": "binance"}),
    }])
    set_state(conn, "binance_synced_at", ts)
    stats["saldos"] = len(filas_saldo)
    log(f"  {len(filas_saldo)} lineas de saldo")
    return stats


def is_crypto(conn: sqlite3.Connection, ticker: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM instruments WHERE ticker = ? AND type = 'CRIPTO'", (ticker,)
    ).fetchone()
    return bool(row)


def crypto_tickers(conn: sqlite3.Connection) -> set[str]:
    """Especies que maneja Binance: PPI no las conoce y no hay que pedirselas."""
    return {
        r["ticker"]
        for r in conn.execute("SELECT ticker FROM instruments WHERE type = 'CRIPTO'")
    } | set(STABLECOINS)


__all__ = [
    "ACCOUNT",
    "crypto_tickers",
    "full_sync",
    "is_crypto",
    "symbol_base",
]
