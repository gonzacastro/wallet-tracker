"""Genera una cuenta sintetica para probar el proyecto sin credenciales.

Sirve para ver que hace la herramienta antes de conectarla a tu cuenta real,
y como banco de pruebas para las metricas.
"""

from __future__ import annotations

import random
import sqlite3
from datetime import date, timedelta

from .db import dumps, init_db, set_state
from .ppi_api import normalize_movements, normalize_snapshot

ACCOUNT = "DEMO-0001"

INSTRUMENTS = {
    "GGAL":  {"type": "ACCIONES", "currency": "ARS", "description": "Grupo Financiero Galicia", "p0": 320.0,  "drift": 0.0022, "vol": 0.028},
    "YPFD":  {"type": "ACCIONES", "currency": "ARS", "description": "YPF S.A.",                 "p0": 9800.0, "drift": 0.0019, "vol": 0.031},
    "AAPL":  {"type": "CEDEARS",  "currency": "ARS", "description": "Apple Inc. (CEDEAR)",      "p0": 4100.0, "drift": 0.0017, "vol": 0.022},
    "AL30":  {"type": "BONOS",    "currency": "ARS", "description": "Bonar 2030",               "p0": 32000.0,"drift": 0.0013, "vol": 0.018},
    "GD30":  {"type": "BONOS",    "currency": "ARS", "description": "Global 2030",              "p0": 36000.0,"drift": 0.0014, "vol": 0.018},
    "GD30C": {"type": "BONOS",    "currency": "USD", "description": "Global 2030 cable",        "p0": 36.0,   "drift": 0.0004, "vol": 0.012},
}

PLAN = [
    # (dias desde el inicio, ticker, lado, cantidad)
    (5,   "GGAL", "compra", 300),
    (12,  "AL30", "compra", 40),
    (40,  "AAPL", "compra", 60),
    (95,  "GGAL", "compra", 200),
    (140, "YPFD", "compra", 12),
    (190, "AAPL", "venta",  25),
    (240, "GD30", "compra", 30),
    (300, "GGAL", "venta",  250),
    (360, "YPFD", "compra", 8),
    (430, "AL30", "venta",  15),
    (500, "AAPL", "compra", 20),
]


def _walk(p0: float, drift: float, vol: float, days: int, rng: random.Random) -> list[float]:
    prices = [p0]
    for _ in range(days):
        shock = rng.gauss(drift, vol)
        prices.append(max(prices[-1] * (1 + shock), 0.01))
    return prices


def seed(conn: sqlite3.Connection, *, days: int = 560, seed_value: int = 7) -> dict[str, int]:
    """Carga movimientos, precios y dolar implicito sinteticos en la base."""
    init_db(conn)
    rng = random.Random(seed_value)
    start = date.today() - timedelta(days=days)

    # --- precios -----------------------------------------------------------
    series: dict[str, list[float]] = {
        ticker: _walk(cfg["p0"], cfg["drift"], cfg["vol"], days, rng)
        for ticker, cfg in INSTRUMENTS.items()
    }
    price_rows = []
    for ticker, values in series.items():
        for offset, price in enumerate(values):
            day = start + timedelta(days=offset)
            if day.weekday() >= 5:  # sin rueda los fines de semana
                continue
            price_rows.append(
                {"ticker": ticker, "date": day.isoformat(), "settlement": "A-24HS",
                 "price": round(price, 4), "volume": rng.randint(1000, 90000),
                 "opening": round(price * 0.997, 4), "max": round(price * 1.012, 4),
                 "min": round(price * 0.988, 4)}
            )
    conn.executemany(
        "INSERT OR REPLACE INTO prices (ticker,date,settlement,price,volume,opening,max,min) "
        "VALUES (:ticker,:date,:settlement,:price,:volume,:opening,:max,:min)", price_rows
    )

    # --- dolar implicito GD30/GD30C ---------------------------------------
    fx_rows = []
    for offset in range(days + 1):
        day = start + timedelta(days=offset)
        if day.weekday() >= 5:
            continue
        ccl = series["GD30"][offset] / series["GD30C"][offset]
        fx_rows.append({"date": day.isoformat(), "ccl": round(ccl, 4), "source": "GD30/GD30C"})
    conn.executemany(
        "INSERT OR REPLACE INTO fx (date,ccl,source) VALUES (:date,:ccl,:source)", fx_rows
    )

    # --- instrumentos ------------------------------------------------------
    conn.executemany(
        "INSERT OR REPLACE INTO instruments (ticker,description,currency,type,market,raw) "
        "VALUES (?,?,?,?,?,?)",
        [
            (t, c["description"], c["currency"], c["type"], "BYMA", dumps(c))
            for t, c in INSTRUMENTS.items()
        ],
    )
    conn.execute(
        "INSERT OR REPLACE INTO accounts (account_number,name,raw) VALUES (?,?,?)",
        (ACCOUNT, "Cuenta demo", dumps({"demo": True})),
    )

    # --- movimientos -------------------------------------------------------
    raw_movements: list[dict] = []

    def price_on(ticker: str, offset: int) -> float:
        return series[ticker][min(offset, len(series[ticker]) - 1)]

    def add(offset: int, description: str, amount: float, ticker=None, quantity=0.0, price=0.0):
        day = start + timedelta(days=offset)
        raw_movements.append(
            {
                "agreementDate": day.isoformat() + "T12:00:00",
                "settlementDate": (day + timedelta(days=1)).isoformat() + "T12:00:00",
                "currency": "ARS",
                "amount": round(amount, 2),
                "price": round(price, 4),
                "description": description,
                "ticker": ticker,
                "quantity": quantity,
                "balance": 0.0,
            }
        )

    add(0, "Transferencia recibida desde CBU propio", 2_500_000)
    for month in range(1, days // 30):
        add(month * 30, "Transferencia recibida desde CBU propio", 350_000)

    for offset, ticker, side, quantity in PLAN:
        price = price_on(ticker, offset)
        gross = price * quantity
        if side == "compra":
            add(offset, f"Compra {INSTRUMENTS[ticker]['type']} {ticker}", -gross, ticker, quantity, price)
        else:
            add(offset, f"Venta {INSTRUMENTS[ticker]['type']} {ticker}", gross, ticker, quantity, price)
        add(offset, f"Comision s/{side} {ticker}", -gross * 0.005, ticker)
        add(offset, f"I.V.A. s/comision {ticker}", -gross * 0.005 * 0.21, ticker)
        add(offset, f"Derechos de Mercado s/{side} {ticker}", -gross * 0.0008, ticker)

    for offset in (150, 330, 510):
        if offset <= days:
            add(offset, "Acreditacion de Dividendos GGAL", 18_500, "GGAL")
    for offset in (180, 360, 540):
        if offset <= days:
            add(offset, "Pago de Renta AL30", 21_000, "AL30")
    add(470, "Extraccion - transferencia enviada", -400_000)
    add(520, "Movimiento no identificado XYZ", -1_250)

    # La API devuelve los movimientos en orden cronologico y con el saldo
    # acumulado de la cuenta: replicamos ese contrato.
    raw_movements.sort(key=lambda m: m["agreementDate"])
    balance = 0.0
    for mov in raw_movements:
        balance += mov["amount"]
        mov["balance"] = round(balance, 2)

    rows = normalize_movements(ACCOUNT, raw_movements)
    conn.executemany(
        "INSERT OR REPLACE INTO movements (uid,account_number,agreement_date,settlement_date,"
        "currency,amount,price,description,ticker,quantity,balance,ordinal,raw) VALUES (:uid,"
        ":account_number,:agreement_date,:settlement_date,:currency,:amount,:price,:description,"
        ":ticker,:quantity,:balance,:ordinal,:raw)", rows
    )

    # --- foto de tenencias -------------------------------------------------
    held: dict[str, float] = {}
    for offset, ticker, side, quantity in PLAN:
        held[ticker] = held.get(ticker, 0.0) + (quantity if side == "compra" else -quantity)
    payload = {
        "groupedAvailability": [
            {"currency": "ARS", "availability": [
                {"name": "ARS", "simbol": "$", "amount": round(balance, 2), "settlement": "INMEDIATA"}
            ]}
        ],
        "groupedInstruments": [
            {"name": INSTRUMENTS[t]["type"], "instruments": [
                {"ticker": t, "quantity": q, "price": round(series[t][-1], 4),
                 "amount": round(q * series[t][-1], 2), "currency": "ARS", "settlement": "A-24HS"}
            ]}
            for t, q in held.items() if q > 0
        ],
    }
    snapshot_rows = normalize_snapshot(ACCOUNT, payload, f"{date.today().isoformat()}T18:00:00")
    conn.executemany(
        "INSERT INTO snapshots (ts,account_number,kind,group_name,ticker,currency,settlement,"
        "quantity,price,amount,raw) VALUES (:ts,:account_number,:kind,:group_name,:ticker,"
        ":currency,:settlement,:quantity,:price,:amount,:raw)", snapshot_rows
    )
    set_state(conn, "demo", "true")
    conn.commit()
    return {"movements": len(rows), "prices": len(price_rows), "fx": len(fx_rows)}
