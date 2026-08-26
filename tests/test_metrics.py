from datetime import date

import pytest

from wallet_tracker.metrics import (
    CashFlow,
    annualize,
    cagr,
    daily_returns,
    max_drawdown,
    npv,
    period_returns,
    return_index,
    twr,
    volatility,
    xirr,
)


def test_xirr_de_un_flujo_simple_a_un_ano():
    tir = xirr([CashFlow(date(2023, 1, 1), -1000), CashFlow(date(2024, 1, 1), 1100)])
    assert tir == pytest.approx(0.10, abs=1e-6)


def test_xirr_pondera_cuando_pusiste_la_plata():
    flujos = [
        CashFlow(date(2023, 1, 1), -1000),
        CashFlow(date(2023, 7, 1), -500),
        CashFlow(date(2024, 1, 1), 1700),
    ]
    tir = xirr(flujos)
    assert npv(tir, flujos) == pytest.approx(0.0, abs=1e-6)
    # Rendimiento simple = 13.3%, pero el segundo aporte estuvo solo medio ano.
    assert tir > 0.15


def test_xirr_devuelve_none_si_no_hay_cambio_de_signo():
    assert xirr([CashFlow(date(2023, 1, 1), -1000)]) is None
    assert xirr([CashFlow(date(2023, 1, 1), -1000), CashFlow(date(2024, 1, 1), -500)]) is None


def test_xirr_con_perdida_es_negativa():
    tir = xirr([CashFlow(date(2023, 1, 1), -1000), CashFlow(date(2024, 1, 1), 700)])
    assert tir == pytest.approx(-0.30, abs=1e-6)


def test_cagr_y_annualize():
    assert cagr(100, 200, 365) == pytest.approx(1.0)
    assert annualize(1.0, 365) == pytest.approx(1.0)
    assert annualize(0.10, 730) == pytest.approx(0.0488, abs=1e-4)


def test_twr_ignora_el_aporte_del_dia():
    nav = [(date(2024, 1, 1), 100.0), (date(2024, 1, 2), 110.0),
           (date(2024, 1, 3), 210.0), (date(2024, 1, 4), 189.0)]
    # El dia 3 entraron 100 pesos: no debe leerse como ganancia.
    resultado = twr(nav, {date(2024, 1, 3): 100.0})
    assert resultado == pytest.approx(1.10 * 1.00 * (189 / 210) - 1, abs=1e-9)


def test_twr_sin_flujos_es_el_rendimiento_puro():
    nav = [(date(2024, 1, 1), 100.0), (date(2024, 6, 1), 150.0)]
    assert twr(nav, {}) == pytest.approx(0.5)


def test_max_drawdown_encuentra_pico_y_piso():
    nav = [(date(2024, 1, 1), 100.0), (date(2024, 1, 2), 150.0),
           (date(2024, 1, 3), 90.0), (date(2024, 1, 4), 120.0)]
    caida, pico, piso = max_drawdown(nav)
    assert caida == pytest.approx(-0.40)
    assert (pico, piso) == (date(2024, 1, 2), date(2024, 1, 3))


def test_volatilidad_de_serie_constante_es_cero():
    nav = [(date(2024, 1, i + 1), 100.0) for i in range(10)]
    assert volatility(daily_returns(nav, {})) == pytest.approx(0.0)


def test_el_aporte_del_dia_ya_se_puede_invertir_ese_dia():
    # Caso real: entran $6.000.000 sobre una cartera de $1.348.174 y se compran
    # CEDEARs el mismo dia. Lo unico que se perdio fue el costo de operar. Si el
    # aporte se descuenta del cierre en vez de sumarse a la base, ese 0,75% se
    # lee como una caida del 4,07%.
    nav = [(date(2026, 1, 1), 1_348_174.70), (date(2026, 1, 2), 7_293_287.36)]
    resultado = twr(nav, {date(2026, 1, 2): 6_000_000.0})
    assert resultado == pytest.approx(-0.0075, abs=1e-4)


def test_ningun_periodo_puede_rendir_menos_de_menos_cien_por_ciento():
    """La garantia que sostiene la cadena del TWR, sobre una serie hostil."""
    nav = [(date(2024, 1, d), v) for d, v in enumerate(
        [1_000.0, 0.15, 500_000.0, 0.0, -20.0, 3.0, 900_000.0], start=1)]
    flujos = {date(2024, 1, 3): 600_000.0, date(2024, 1, 5): -400_000.0,
              date(2024, 1, 7): 850_000.0}
    assert all(r > -1.0 for _, r in period_returns(nav, flujos))
    # Y el indice nunca cruza a negativo, que es lo que rompia todo aguas abajo.
    assert all(v > 0 for _, v in return_index(nav, flujos))


def test_twr_saltea_los_periodos_que_arrancan_en_cero():
    nav = [(date(2024, 1, 1), 0.0), (date(2024, 1, 2), 1_000.0),
           (date(2024, 1, 3), 1_100.0)]
    assert twr(nav, {date(2024, 1, 2): 1_000.0}) == pytest.approx(0.10)


def test_un_aporte_a_una_cuenta_vacia_no_es_rendimiento():
    nav = [(date(2024, 1, 1), 0.15), (date(2024, 1, 2), 600_000.15)]
    assert twr(nav, {date(2024, 1, 2): 600_000.0}) == pytest.approx(0.0, abs=1e-6)


def test_return_index_es_el_valor_de_un_peso_invertido():
    nav = [(date(2024, 1, 1), 100.0), (date(2024, 1, 2), 110.0),
           (date(2024, 1, 3), 220.0)]
    indice = return_index(nav, {date(2024, 1, 3): 100.0})
    # El dia 3 habia $110 y entraron $100: la base son $210 y cerro en $220.
    assert [round(v, 4) for _, v in indice] == [1.0, 1.1, 1.1524]


def test_la_peor_caida_sobre_el_indice_ignora_los_retiros():
    # La valuacion cae de 1000 a 100 porque retiraste 900, no porque perdiste.
    nav = [(date(2024, 1, 1), 1_000.0), (date(2024, 1, 2), 100.0),
           (date(2024, 1, 3), 90.0)]
    flujos = {date(2024, 1, 2): -900.0}
    assert max_drawdown(nav)[0] == pytest.approx(-0.91)          # sobre la valuacion: enganoso
    assert max_drawdown(return_index(nav, flujos))[0] == pytest.approx(-0.10)
