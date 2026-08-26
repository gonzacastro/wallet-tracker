from datetime import date

import pytest

from wallet_tracker.ledger import Event
from wallet_tracker.lots import run_fifo


def buy(day, ticker, qty, unit, currency="ARS"):
    return Event(uid=f"b{day}", date=date.fromisoformat(day), category="BUY",
                 description=f"Compra {ticker}", currency=currency,
                 cash_flow=-qty * unit, ticker=ticker, quantity=qty, price=unit)


def sell(day, ticker, qty, unit, currency="ARS"):
    return Event(uid=f"s{day}", date=date.fromisoformat(day), category="SELL",
                 description=f"Venta {ticker}", currency=currency,
                 cash_flow=qty * unit, ticker=ticker, quantity=qty, price=unit)


def other(day, category, amount, ticker=None):
    return Event(uid=f"o{day}{category}", date=date.fromisoformat(day), category=category,
                 description=category, currency="ARS", cash_flow=amount, ticker=ticker)


def test_costo_promedio_y_fecha_de_primera_compra():
    result = run_fifo([buy("2024-01-10", "GGAL", 100, 10.0), buy("2024-06-10", "GGAL", 100, 20.0)])
    holding = result.holdings[("GGAL", "ARS")]
    assert holding.quantity == 200
    assert holding.avg_cost == 15.0
    assert holding.first_buy == date(2024, 1, 10)
    assert holding.last_buy == date(2024, 6, 10)


def test_venta_parcial_consume_el_lote_mas_viejo_primero():
    result = run_fifo([
        buy("2024-01-10", "GGAL", 100, 10.0),
        buy("2024-06-10", "GGAL", 100, 20.0),
        sell("2024-09-10", "GGAL", 150, 30.0),
    ])
    holding = result.holdings[("GGAL", "ARS")]
    # 100 al costo de 10 (+20 c/u) y 50 al costo de 20 (+10 c/u)
    assert holding.realized_pnl == 100 * 20 + 50 * 10
    assert holding.quantity == 50
    assert holding.avg_cost == 20.0  # queda solo el lote nuevo
    assert [t.holding_days for t in result.closed] == [244, 92]


def test_venta_total_deja_la_posicion_cerrada():
    result = run_fifo([buy("2024-01-10", "AL30", 10, 100.0), sell("2024-02-10", "AL30", 10, 130.0)])
    holding = result.holdings[("AL30", "ARS")]
    assert not holding.is_open
    assert holding.quantity == 0.0
    assert holding.cost_basis == 0.0
    assert holding.realized_pnl == 300.0


def test_venta_sin_compra_previa_avisa_y_no_inventa_ganancia():
    result = run_fifo([sell("2024-02-10", "YPFD", 5, 100.0)])
    assert result.holdings[("YPFD", "ARS")].realized_pnl == 0.0
    assert any("sin compra" in w for w in result.warnings)


def test_dividendos_comisiones_e_impuestos_se_imputan_a_la_especie():
    result = run_fifo([
        buy("2024-01-10", "GGAL", 100, 10.0),
        other("2024-01-10", "FEE", -50, "GGAL"),
        other("2024-01-10", "TAX", -10, "GGAL"),
        other("2024-05-10", "DIVIDEND", 500, "GGAL"),
    ])
    holding = result.holdings[("GGAL", "ARS")]
    assert holding.fees == 60
    assert holding.income == 500
    assert holding.cost_basis == 1000  # los costos no ensucian el costo del lote


def test_costos_sin_ticker_van_al_nivel_cartera():
    result = run_fifo([other("2024-01-10", "FEE", -300)])
    assert result.portfolio_fees == 300
    assert result.holdings == {}


def test_resultado_total_incluye_no_realizado_realizado_dividendos_y_costos():
    result = run_fifo([
        buy("2024-01-10", "GGAL", 100, 10.0),
        sell("2024-03-10", "GGAL", 50, 20.0),
        other("2024-04-10", "DIVIDEND", 100, "GGAL"),
        other("2024-04-10", "FEE", -40, "GGAL"),
    ])
    holding = result.holdings[("GGAL", "ARS")]
    # 50 papeles a 30 = 1500 de valuacion, con 500 de costo -> 1000 no realizado
    assert holding.total_pnl(30.0) == 1000 + 500 + 100 - 40


def test_el_precio_unitario_sale_del_importe_real_no_del_informado():
    # El importe manda: incluye el precio efectivo de ejecucion.
    event = Event(uid="x", date=date(2024, 1, 1), category="BUY", description="Compra",
                  currency="ARS", cash_flow=-1100.0, ticker="GGAL", quantity=100, price=10.0)
    holding = run_fifo([event]).holdings[("GGAL", "ARS")]
    assert holding.avg_cost == 11.0


def fx_leg(day, ticker, qty, amount, currency):
    return Event(uid=f"fx{day}{currency}", date=date.fromisoformat(day),
                 category="FX_CONVERSION", description=f"conversion {ticker}",
                 currency=currency, cash_flow=amount, ticker=ticker, quantity=qty)


def test_las_patas_de_una_conversion_no_generan_tenencia():
    # Conservan ticker e importe para poder mostrarlas, pero el FIFO las ignora:
    # comprar dolares con un bono no es tener el bono.
    result = run_fifo([
        buy("2024-01-10", "GGAL", 100, 10.0),
        fx_leg("2024-02-01", "AL30", 2379, -1_697_264.71, "Pesos"),
        fx_leg("2024-02-02", "AL30", 2379, 1_378.77, "Dolar MEP"),
    ])
    assert set(result.holdings) == {("GGAL", "ARS")}
    assert result.warnings == []


def test_un_cambio_de_ratio_no_toca_la_plata_invertida():
    ratio = Event(uid="r1", date=date(2024, 6, 1), category="RATIO_CHANGE",
                  description="canje", currency="", cash_flow=0.0,
                  ticker="GGAL", ratio=4.0)
    result = run_fifo([buy("2024-01-10", "GGAL", 100, 10.0), ratio])
    holding = result.holdings[("GGAL", "ARS")]
    assert (holding.quantity, holding.cost_basis, holding.avg_cost) == (400.0, 1000.0, 2.5)


def income(day, ticker, amount, currency):
    return Event(uid=f"d{day}{currency}", date=date.fromisoformat(day), category="DIVIDEND",
                 description=f"Dividendo en efectivo / {ticker}", currency=currency,
                 cash_flow=amount, ticker=ticker)


def test_el_dividendo_se_imputa_a_la_especie_aunque_llegue_en_otra_moneda():
    # Un CEDEAR comprado en pesos cobra el dividendo en dolares: es la misma
    # tenencia. Separarlas dejaba una posicion fantasma con cantidad cero.
    result = run_fifo([
        buy("2026-01-02", "JPM", 30, 33_000.0, currency="Pesos"),
        income("2026-02-03", "JPM", 1.94, "DolarCV7000 Ext."),
        income("2026-02-03", "JPM", -46.14, "Pesos"),
    ])
    assert list(result.holdings) == [("JPM", "Pesos")]
    holding = result.holdings[("JPM", "Pesos")]
    assert holding.quantity == 30
    assert holding.income == pytest.approx(1.94 - 46.14)


def test_un_dividendo_sin_tenencia_previa_crea_la_suya():
    result = run_fifo([income("2026-02-03", "JPM", 1.94, "DolarCV7000 Ext.")])
    assert list(result.holdings) == [("JPM", "DolarCV7000 Ext.")]


def opening(day, ticker, qty, unit, currency="USDT"):
    """Tenencia que ya tenias: costo conocido, fecha de compra no."""
    return Event(uid=f"op{day}{ticker}", date=date.fromisoformat(day), category="OPENING",
                 description=f"Tenencia inicial {ticker}", currency=currency,
                 cash_flow=0.0, ticker=ticker, quantity=qty, price=unit)


def test_una_tenencia_inicial_arma_su_lote_con_el_costo_declarado():
    result = run_fifo([opening("2025-12-01", "BTC", 0.0286, 68_029.0)])
    holding = result.holdings[("BTC", "USDT")]
    assert holding.quantity == pytest.approx(0.0286)
    assert holding.cost_basis == pytest.approx(0.0286 * 68_029.0)
    assert holding.avg_cost == pytest.approx(68_029.0)


def test_una_tenencia_inicial_no_mueve_plata():
    """No fue un aporte de ahora: ya estaba. Si contara como flujo, la ganancia
    del periodo se inflaria por el valor entero de la tenencia."""
    evento = opening("2025-12-01", "BTC", 0.0286, 68_029.0)
    assert evento.cash_flow == 0.0
    assert not evento.is_external_flow


def test_se_puede_vender_una_tenencia_inicial():
    result = run_fifo([
        opening("2025-12-01", "BTC", 0.02, 60_000.0),
        sell("2026-06-01", "BTC", 0.02, 80_000.0, currency="USDT"),
    ])
    assert result.holdings[("BTC", "USDT")].realized_pnl == pytest.approx(400.0)
    assert result.warnings == []
