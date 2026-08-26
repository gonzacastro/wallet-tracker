"""Cliente de Binance: normalizacion y firma, sin tocar la red."""

from datetime import date, datetime, timezone

import pytest

from wallet_tracker import binance_api
from wallet_tracker.binance_api import (
    Balance,
    BinanceError,
    BinanceSession,
    normalize_balances,
    normalize_converts,
    normalize_deposits,
    normalize_trades,
    price_history,
    symbol_base,
)
from wallet_tracker.ledger import BUY, SELL, build_ledger
from wallet_tracker.lots import run_fifo


def ms(anio, mes, dia):
    return int(datetime(anio, mes, dia, 12, tzinfo=timezone.utc).timestamp() * 1000)


COMPRA = {
    "symbol": "BTCUSDT", "id": 4001, "price": "42000.00", "qty": "0.05",
    "quoteQty": "2100.00", "commission": "0.00005", "commissionAsset": "BTC",
    "time": ms(2024, 3, 11), "isBuyer": True,
}
COMPRA2 = {
    "symbol": "BTCUSDT", "id": 4002, "price": "50000.00", "qty": "0.02",
    "quoteQty": "1000.00", "commission": "0.00002", "commissionAsset": "BTC",
    "time": ms(2024, 9, 5), "isBuyer": True,
}


# ------------------------------------------------------------------ simbolos


@pytest.mark.parametrize("symbol, base", [
    ("BTCUSDT", "BTC"), ("ETHUSDC", "ETH"), ("SOLBUSD", "SOL"), ("RAROTOKEN", "RAROTOKEN"),
])
def test_symbol_base(symbol, base):
    assert symbol_base(symbol) == base


# ------------------------------------------------------------------- saldos


def test_una_stablecoin_es_efectivo_no_una_tenencia():
    assert Balance("USDT", 120.0, 0.0).is_cash
    assert not Balance("BTC", 0.07, 0.0).is_cash


def test_el_saldo_suma_lo_libre_y_lo_bloqueado():
    assert Balance("BTC", 0.05, 0.02).total == pytest.approx(0.07)


def test_normalize_balances_separa_cripto_de_efectivo():
    filas = normalize_balances(
        "BINANCE",
        [Balance("BTC", 0.07, 0.0), Balance("USDT", 120.0, 0.0)],
        {"BTC": 78_734.85},
        "2026-08-26T10:00:00",
    )
    btc = next(f for f in filas if f["ticker"] == "BTC")
    usdt = next(f for f in filas if f["ticker"] is None)
    assert btc["kind"] == "instrument"
    assert btc["quantity"] == pytest.approx(0.07)
    assert btc["amount"] == pytest.approx(0.07 * 78_734.85)
    assert usdt["kind"] == "cash"
    assert usdt["currency"] == "USDT"
    assert usdt["amount"] == pytest.approx(120.0)


# -------------------------------------------------------------- operaciones


def test_una_compra_se_guarda_como_movimiento():
    fila = normalize_trades("BINANCE", "BTCUSDT", [COMPRA])[0]
    assert fila["ticker"] == "BTC"
    assert fila["currency"] == "USDT"
    assert fila["quantity"] == pytest.approx(0.05)
    assert fila["amount"] == pytest.approx(-2100.0)     # sale plata: compraste
    assert fila["agreement_date"] == "2024-03-11"
    assert fila["description"] == "COMPRA BTC"


def test_una_venta_entra_plata():
    venta = {**COMPRA, "id": 4003, "isBuyer": False}
    fila = normalize_trades("BINANCE", "BTCUSDT", [venta])[0]
    assert fila["amount"] == pytest.approx(2100.0)
    assert fila["description"] == "VENTA BTC"


def test_el_uid_es_estable_entre_sincronizaciones():
    """Volver a bajar lo mismo tiene que pisar la fila, no duplicarla."""
    a = normalize_trades("BINANCE", "BTCUSDT", [COMPRA])[0]["uid"]
    b = normalize_trades("BINANCE", "BTCUSDT", [COMPRA])[0]["uid"]
    distinto = normalize_trades("BINANCE", "BTCUSDT", [COMPRA2])[0]["uid"]
    assert a == b and a != distinto


# ------------------------------- lo importante: que el resto no note nada


def test_las_compras_de_binance_pasan_por_el_mismo_motor_que_los_cedears():
    """Ni el clasificador ni el FIFO saben de donde vienen los datos."""
    filas = normalize_trades("BINANCE", "BTCUSDT", [COMPRA, COMPRA2])
    eventos = build_ledger(filas)
    assert [e.category for e in eventos] == [BUY, BUY]

    holding = run_fifo(eventos).holdings[("BTC", "USDT")]
    assert holding.quantity == pytest.approx(0.07)
    assert holding.cost_basis == pytest.approx(3_100.0)
    assert holding.avg_cost == pytest.approx(3_100.0 / 0.07)
    assert holding.first_buy == date(2024, 3, 11)


def test_una_venta_de_cripto_realiza_resultado():
    venta = {"symbol": "BTCUSDT", "id": 5000, "price": "60000", "qty": "0.05",
             "quoteQty": "3000.00", "time": ms(2025, 1, 20), "isBuyer": False}
    eventos = build_ledger(normalize_trades("BINANCE", "BTCUSDT", [COMPRA, venta]))
    assert [e.category for e in eventos] == [BUY, SELL]
    resultado = run_fifo(eventos)
    assert resultado.holdings[("BTC", "USDT")].realized_pnl == pytest.approx(900.0)


# ---------------------------------------------------------------- precios


def test_price_history_pagina_hasta_el_final(monkeypatch):
    """Binance devuelve 1000 velas por llamada: hay que seguir pidiendo."""
    llamadas = []

    def falso(path, params=None, **kwargs):
        llamadas.append(params["startTime"])
        if len(llamadas) > 2:
            return []
        base = params["startTime"]
        return [
            [base + i * 86_400_000, "1", "2", "0.5", str(100 + i), "10"]
            for i in range(3)
        ]

    monkeypatch.setattr(binance_api, "_request", falso)
    filas = price_history("BTCUSDT", date(2026, 1, 1), date(2026, 3, 1))
    assert len(filas) == 6
    assert len(llamadas) == 3            # dos con datos y una vacia que corta
    assert filas[0]["ticker"] == "BTC"
    assert filas[0]["settlement"] == "SPOT"
    assert filas[0]["price"] == 100.0    # el cierre, no la apertura


def test_el_historial_de_precios_no_necesita_claves(monkeypatch):
    """Es un endpoint publico: si pidiera firma, no andaria sin configurar nada."""
    visto = {}

    def falso(path, params=None, **kwargs):
        visto.update(kwargs)
        return []

    monkeypatch.setattr(binance_api, "_request", falso)
    price_history("BTCUSDT", date(2026, 1, 1))
    assert not visto.get("api_secret")


# --------------------------------------------------------------- credenciales


def test_sin_claves_no_se_consulta_nada_privado():
    session = BinanceSession("", "")
    assert not session.has_credentials
    with pytest.raises(BinanceError, match="claves"):
        session.balances()


# ------------------------------------------------------------ conversiones

CONVERT_COMPRA = {
    "orderId": "2198512764329167622", "orderStatus": "SUCCESS",
    "fromAsset": "USDT", "fromAmount": "999.99",
    "toAsset": "BTC", "toAmount": "0.01412184", "createTime": ms(2026, 2, 9),
}


def test_una_conversion_de_stablecoin_a_cripto_es_una_compra():
    """El boton Convert no aparece en myTrades: sin esto, comprar asi cambiaria
    el saldo y no dejaria rastro del costo."""
    fila = normalize_converts("BINANCE", [CONVERT_COMPRA])[0]
    assert fila["description"] == "COMPRA BTC"
    assert fila["ticker"] == "BTC"
    assert fila["currency"] == "USDT"
    assert fila["quantity"] == pytest.approx(0.01412184)
    assert fila["amount"] == pytest.approx(-999.99)
    assert fila["price"] == pytest.approx(999.99 / 0.01412184)


def test_una_conversion_de_cripto_a_stablecoin_es_una_venta():
    venta = {**CONVERT_COMPRA, "orderId": "9", "fromAsset": "BTC", "fromAmount": "0.005",
             "toAsset": "USDT", "toAmount": "400.00"}
    fila = normalize_converts("BINANCE", [venta])[0]
    assert fila["description"] == "VENTA BTC"
    assert fila["amount"] == pytest.approx(400.0)
    assert fila["quantity"] == pytest.approx(0.005)


def test_una_conversion_entre_criptos_se_saltea():
    """Es comprar y vender a la vez: mezclaria el costo de las dos puntas."""
    cripto = {**CONVERT_COMPRA, "orderId": "9", "fromAsset": "ETH", "fromAmount": "1"}
    assert normalize_converts("BINANCE", [cripto]) == []


def test_las_conversiones_fallidas_no_llegan_al_ledger(monkeypatch):
    """El cliente descarta lo que no se concreto antes de normalizarlo."""
    def falso(path, params=None, **kwargs):
        return {"list": [CONVERT_COMPRA, {**CONVERT_COMPRA, "orderId": "x",
                                          "orderStatus": "FAILED"}]}

    monkeypatch.setattr(binance_api, "_request", falso)
    monkeypatch.setattr(binance_api.time, "sleep", lambda _: None)
    session = BinanceSession("k", "s")
    filas = [c for _, tramo in session.converts(date(2026, 2, 1), date(2026, 2, 20))
             for c in tramo]
    assert [c["orderId"] for c in filas] == ["2198512764329167622"]


def test_el_barrido_de_conversiones_entrega_tramos_para_guardar_avance(monkeypatch):
    """Si Binance corta a mitad, lo ya bajado no se pierde."""
    monkeypatch.setattr(binance_api, "_request", lambda *a, **k: {"list": []})
    monkeypatch.setattr(binance_api.time, "sleep", lambda _: None)
    tramos = list(BinanceSession("k", "s").converts(date(2026, 1, 1), date(2026, 4, 1)))
    assert [t[0] for t in tramos] == [date(2026, 1, 31), date(2026, 3, 2), date(2026, 4, 1)]


def test_el_uid_de_una_conversion_es_estable():
    a = normalize_converts("BINANCE", [CONVERT_COMPRA])[0]["uid"]
    b = normalize_converts("BINANCE", [CONVERT_COMPRA])[0]["uid"]
    otra = normalize_converts("BINANCE", [{**CONVERT_COMPRA, "orderId": "otro"}])[0]["uid"]
    assert a == b and a != otra


# --------------------------------------------------------------- depositos


def test_un_deposito_de_stablecoin_es_un_ingreso_de_fondos():
    filas, avisos = normalize_deposits("BINANCE", [
        {"coin": "USDT", "amount": "999.99", "insertTime": ms(2026, 2, 9), "txId": "abc"},
    ])
    assert avisos == []
    assert filas[0]["description"] == "Ingreso de Fondos"
    assert filas[0]["amount"] == pytest.approx(999.99)
    assert filas[0]["ticker"] is None
    assert build_ledger(filas)[0].category == "DEPOSIT"


def test_un_deposito_de_cripto_avisa_en_vez_de_inventar_el_costo():
    """Llega un activo cuyo precio de compra Binance no informa."""
    filas, avisos = normalize_deposits("BINANCE", [
        {"coin": "BTC", "amount": "0.02", "insertTime": ms(2024, 6, 1), "txId": "abc"},
    ])
    assert filas == []
    assert "no se conoce a que precio" in avisos[0]
    assert "0.02000000 BTC" in avisos[0]
