from datetime import date

import pytest

from wallet_tracker.ledger import (
    BUY,
    DEPOSIT,
    DIVIDEND,
    FEE,
    OTHER,
    SELL,
    TAX,
    WITHDRAWAL,
    Rules,
    build_ledger,
    classify,
    normalize_text,
    ticker_from_description,
)


def mov(description, *, ticker=None, quantity=0.0, amount=0.0, price=0.0, day="2024-03-01", **extra):
    base = {
        "uid": description[:8],
        "agreement_date": day,
        "settlement_date": day,
        "currency": "ARS",
        "amount": amount,
        "price": price,
        "description": description,
        "ticker": ticker,
        "quantity": quantity,
        "balance": 0.0,
        "ordinal": 0,
    }
    base.update(extra)
    return base


def test_normalize_text_quita_acentos_y_mayusculas():
    assert normalize_text("Suscripción FCI  Pellegrini") == "suscripcion fci pellegrini"


@pytest.mark.parametrize(
    "description, expected",
    [
        ("Transferencia recibida desde CBU propio", DEPOSIT),
        ("Extraccion - transferencia enviada", WITHDRAWAL),
        ("Acreditacion de Dividendos GGAL", DIVIDEND),
        ("Comision de mercado", FEE),
        ("Impuesto Ley 25413", TAX),
        ("I.V.A. sobre comisiones", TAX),
        ("Un movimiento que nadie previo", OTHER),
    ],
)
def test_categorias_de_movimientos_de_caja(description, expected):
    assert classify(mov(description, amount=-100)).category == expected


def test_comision_sobre_compra_no_se_confunde_con_una_compra():
    # La descripcion contiene "compra" pero es un costo: gana la regla de comisiones.
    event = classify(mov("Derechos de Mercado s/Compra GGAL", ticker="GGAL", amount=-120))
    assert event.category == FEE


def test_compra_y_venta_por_signo_del_importe():
    compra = classify(mov("Compra ACCIONES GGAL", ticker="GGAL", quantity=100, amount=-150_000))
    venta = classify(mov("Venta ACCIONES GGAL", ticker="GGAL", quantity=100, amount=150_000))
    assert (compra.category, venta.category) == (BUY, SELL)


def test_el_signo_del_importe_corrige_una_descripcion_enganosa():
    # Reversion de una compra: dice "compra" pero entra plata.
    event = classify(mov("Compra ACCIONES GGAL (reversion)", ticker="GGAL", quantity=100, amount=150_000))
    assert event.category == SELL


def test_sin_ticker_ni_cantidad_no_hay_operacion():
    event = classify(mov("Compra de moneda extranjera", amount=-1000))
    assert event.category == OTHER


def test_reglas_propias_ganan_sobre_las_default(tmp_path):
    archivo = tmp_path / "rules.json"
    archivo.write_text('[{"pattern": "dividendo", "category": "OTHER"}]', encoding="utf-8")
    rules = Rules.load(archivo)
    assert classify(mov("Acreditacion de Dividendos", amount=100), rules).category == OTHER


def test_build_ledger_ordena_cronologicamente_y_compras_primero():
    events = build_ledger(
        [
            mov("Venta ACCIONES GGAL", ticker="GGAL", quantity=10, amount=1000, day="2024-03-02"),
            mov("Compra ACCIONES GGAL", ticker="GGAL", quantity=10, amount=-900, day="2024-03-02"),
            mov("Transferencia recibida", amount=5000, day="2024-01-01"),
        ]
    )
    assert [e.date for e in events] == [date(2024, 1, 1), date(2024, 3, 2), date(2024, 3, 2)]
    assert events[1].category == BUY  # la compra del dia va antes que la venta


def test_ticker_not_found_es_un_centinela_no_una_especie():
    # PPI manda ese literal cuando no supo resolver la especie del movimiento.
    # Tomarlo como ticker inventaba una posicion con ese nombre.
    event = classify(mov("Acreditacion de renta", ticker="TICKER NOT FOUND", amount=1.94))
    assert event.ticker is None


def test_la_especie_de_un_dividendo_se_recupera_de_la_descripcion():
    # PPI no manda el ticker en estos movimientos, pero lo nombra al final de
    # la descripcion. Sin esto los dividendos no se le imputan a nadie.
    event = classify(mov("Dividendo en efectivo / AAPL", ticker="TICKER NOT FOUND", amount=1.94))
    assert (event.ticker, event.category) == ("AAPL", DIVIDEND)


@pytest.mark.parametrize("description", [
    "Ingreso de Fondos",
    "Movimiento Manual / Debito/Credito - Compensacion de monedas",
    "Transferencia recibida desde CBU propio",
])
def test_no_inventa_especie_cuando_la_descripcion_no_la_nombra(description):
    assert ticker_from_description(description) is None


def test_solo_se_recupera_la_especie_en_movimientos_de_renta():
    # Una comision que termina en mayusculas no debe crear una especie.
    event = classify(mov("Movimiento Manual / Debito Aranceles ADR",
                         ticker="TICKER NOT FOUND", amount=-6.49))
    assert event.ticker is None
    assert event.category == FEE


def test_un_movimiento_sin_especie_no_se_lee_como_operacion():
    event = classify(mov("Ingreso de Fondos", ticker="TICKER NOT FOUND", amount=1_000_000))
    assert event.ticker is None
    assert event.category == DEPOSIT
