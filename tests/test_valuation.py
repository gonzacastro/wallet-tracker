from datetime import date

import pytest

from wallet_tracker.conversions import FxConversion
from wallet_tracker.ledger import Event
from wallet_tracker.valuation import (
    FxBook,
    PriceBook,
    build_nav_series,
    cash_balances,
    external_flows_by_day,
    in_transit_by_day,
    quantities_by_day,
)


def ev(day, category, amount, *, ticker=None, qty=0.0, balance=0.0, ordinal=0, currency="ARS"):
    return Event(uid=f"{day}{category}{ordinal}", date=date.fromisoformat(day), category=category,
                 description=category, currency=currency, cash_flow=amount, ticker=ticker,
                 quantity=qty, balance=balance, ordinal=ordinal)


def test_pricebook_repite_el_ultimo_precio_conocido():
    book = PriceBook({"GGAL": [(date(2024, 1, 1), 10.0), (date(2024, 1, 5), 20.0)]})
    assert book.get("GGAL", date(2024, 1, 3)) == 10.0   # dia sin rueda
    assert book.get("GGAL", date(2024, 6, 1)) == 20.0   # despues del ultimo dato
    assert book.get("GGAL", date(2023, 12, 1)) is None  # antes del primero
    assert book.get("NADA", date(2024, 1, 3)) is None


def test_cash_balances_usa_el_saldo_informado_como_punto_de_partida():
    # El historial arranca con la cuenta ya fondeada: el saldo del primer
    # movimiento dice cuanto habia antes ($10.000, de los que se gastan $500).
    eventos = [ev("2024-01-01", "BUY", -500, ticker="GGAL", qty=10, balance=9500)]
    assert cash_balances(eventos)["ARS"] == [(date(2024, 1, 1), 9500)]


def test_cash_balances_acumula_si_no_hay_saldo_informado():
    eventos = [ev("2024-01-01", "DEPOSIT", 1000), ev("2024-01-02", "BUY", -400, ticker="X", qty=1)]
    assert cash_balances(eventos)["ARS"] == [(date(2024, 1, 1), 1000), (date(2024, 1, 2), 600)]


def test_cash_balances_cierra_el_dia_con_todos_los_movimientos_aplicados():
    eventos = [
        ev("2024-01-01", "BUY", -500, ticker="GGAL", qty=10, balance=9500, ordinal=0),
        ev("2024-01-01", "FEE", -50, balance=9450, ordinal=1),
    ]
    assert cash_balances(eventos)["ARS"] == [(date(2024, 1, 1), 9450)]


def test_cash_balances_ignora_los_saldos_intermedios_que_informa_ppi():
    # Dentro del dia PPI informa los saldos por fecha de liquidacion, no en el
    # orden en que ocurren: el ultimo puede no ser el de cierre. Manda la suma.
    eventos = [
        ev("2024-01-01", "DEPOSIT", 1_000_000, balance=1_000_000, ordinal=0),
        ev("2024-01-01", "BUY", -600_000, ticker="GGAL", qty=10, balance=400_000, ordinal=1),
        ev("2024-01-01", "BUY", -390_000, ticker="YPFD", qty=5, balance=999_999, ordinal=2),
    ]
    assert cash_balances(eventos)["ARS"] == [(date(2024, 1, 1), 10_000)]


def test_quantities_by_day_acumula_compras_y_ventas():
    eventos = [
        ev("2024-01-01", "BUY", -100, ticker="GGAL", qty=10),
        ev("2024-02-01", "BUY", -100, ticker="GGAL", qty=5),
        ev("2024-03-01", "SELL", 100, ticker="GGAL", qty=12),
    ]
    snaps = quantities_by_day(eventos)
    assert snaps[date(2024, 2, 1)] == {"GGAL": 15}
    assert snaps[date(2024, 3, 1)] == {"GGAL": 3}


def test_nav_suma_especies_mas_efectivo():
    eventos = [
        ev("2024-01-01", "DEPOSIT", 10_000, balance=10_000, ordinal=0),
        ev("2024-01-02", "BUY", -1_000, ticker="GGAL", qty=100, balance=9_000, ordinal=0),
    ]
    prices = PriceBook({"GGAL": [(date(2024, 1, 2), 10.0), (date(2024, 1, 3), 12.0)]})
    serie = build_nav_series(eventos, prices, start=date(2024, 1, 1), end=date(2024, 1, 3))
    assert [round(p.nav_ars) for p in serie] == [10_000, 10_000, 10_200]


def test_posiciones_en_dolares_se_convierten_con_el_ccl_del_dia():
    eventos = [ev("2024-01-01", "BUY", -100, ticker="GD30C", qty=10, balance=0)]
    prices = PriceBook({"GD30C": [(date(2024, 1, 1), 30.0)]})
    fx = FxBook([(date(2024, 1, 1), 1000.0)])
    serie = build_nav_series(eventos, prices, fx, {"GD30C": "USD"},
                             start=date(2024, 1, 1), end=date(2024, 1, 1))
    assert serie[0].instruments_ars == pytest.approx(10 * 30 * 1000)
    # El efectivo queda en -100 (se pago la compra) y tambien se mide en dolares.
    assert serie[0].cash_ars == pytest.approx(-100.0)
    assert serie[0].nav_usd == pytest.approx((300_000 - 100) / 1000)


def test_solo_aportes_y_retiros_cuentan_como_flujo_externo():
    eventos = [
        ev("2024-01-01", "DEPOSIT", 1000),
        ev("2024-01-02", "BUY", -400, ticker="X", qty=1),
        ev("2024-01-03", "DIVIDEND", 50, ticker="X"),
        ev("2024-01-04", "WITHDRAWAL", -200),
    ]
    flujos = external_flows_by_day(eventos)
    assert flujos == {date(2024, 1, 1): 1000, date(2024, 1, 4): -200}


def test_la_plata_en_transito_de_una_conversion_cuenta_en_la_valuacion():
    # Los pesos salieron el dia 1 y los dolares llegan el dia 3: en el medio la
    # plata existe (es el bono, ya comprado) y tiene que verse en el NAV.
    conversion = FxConversion(
        ticker="AL30",
        from_date=date(2024, 1, 1), from_currency="Pesos",
        from_amount=600_000.0, from_quantity=5_848,
        to_date=date(2024, 1, 3), to_currency="Dolar MEP",
        to_amount=500.0, to_quantity=5_848,
    )
    transito = in_transit_by_day([conversion])
    assert transito == {date(2024, 1, 1): 600_000.0, date(2024, 1, 2): 600_000.0}


def test_sin_conversiones_no_hay_nada_en_transito():
    assert in_transit_by_day([]) == {}


def test_el_nav_no_se_evapora_entre_las_dos_patas_de_una_conversion():
    eventos = [
        ev("2024-01-01", "DEPOSIT", 600_000, balance=600_000, ordinal=0, currency="Pesos"),
        ev("2024-01-01", "FX_CONVERSION", -598_000, ticker="AL30", qty=5_848,
           balance=2_000, ordinal=1, currency="Pesos"),
    ]
    conversion = FxConversion(
        ticker="AL30",
        from_date=date(2024, 1, 1), from_currency="Pesos",
        from_amount=598_000.0, from_quantity=5_848,
        to_date=date(2024, 1, 3), to_currency="Dolar MEP",
        to_amount=500.0, to_quantity=5_848,
    )
    serie = build_nav_series(eventos, PriceBook({}), conversions=[conversion],
                            start=date(2024, 1, 1), end=date(2024, 1, 2))
    assert [round(p.nav_ars) for p in serie] == [600_000, 600_000]


def test_los_retiros_en_dolares_se_convierten_antes_de_sumarse():
    eventos = [
        ev("2024-01-01", "DEPOSIT", 1_000_000, currency="Pesos"),
        ev("2024-01-02", "WITHDRAWAL", -1_000, currency="Dolar MEP"),
    ]
    fx = FxBook([(date(2024, 1, 1), 1_000.0)])
    flujos = external_flows_by_day(eventos, fx)
    assert flujos == {date(2024, 1, 1): 1_000_000, date(2024, 1, 2): -1_000_000}
