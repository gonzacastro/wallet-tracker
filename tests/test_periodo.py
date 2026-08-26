"""La etapa que se mide: desde la primera inversion de verdad.

Una cuenta puede haberse usado para otra cosa antes de tener cartera -- comprar
dolar MEP, por ejemplo. Promediar esos anios no mide nada: diluye el rendimiento
de la cartera en periodos en los que no habia cartera.
"""

from datetime import date

import pytest

from wallet_tracker.analysis import (
    MonthPoint,
    PeriodMetrics,
    contributed_series,
    first_investment,
    monthly_progress,
)
from wallet_tracker.ledger import BUY, FX_CONVERSION, OPENING, SELL, Event
from wallet_tracker.valuation import NavPoint


def ev(day, category, amount, *, ticker=None, qty=0.0, currency="Pesos"):
    return Event(uid=f"{day}{category}{ticker}", date=date.fromisoformat(day),
                 category=category, description=category, currency=currency,
                 cash_flow=amount, ticker=ticker, quantity=qty)


def nav(day, value, ccl=None):
    return NavPoint(date.fromisoformat(day), value, 0.0, ccl)


# ------------------------------------------------ cuando arranca la cartera


def test_las_patas_de_una_conversion_no_cuentan_como_inversion():
    eventos = [
        ev("2023-02-15", FX_CONVERSION, -30_000.0, ticker="AL30", qty=300),
        ev("2023-02-16", FX_CONVERSION, 80.0, ticker="AL30", qty=300, currency="Dolar MEP"),
        ev("2025-12-30", BUY, -1_000_000.0, ticker="SPY", qty=19),
    ]
    assert first_investment(eventos) == date(2025, 12, 30)


def test_una_venta_tambien_marca_el_arranque():
    """Si el historial empieza con una venta, ahi ya habia cartera."""
    eventos = [ev("2024-05-10", SELL, 50_000, ticker="GGAL", qty=100)]
    assert first_investment(eventos) == date(2024, 5, 10)


def test_una_tenencia_inicial_no_corre_el_inicio_del_periodo():
    """Es anterior por definicion: tomarla como inicio inventaria una fecha."""
    eventos = [
        ev("2025-12-01", OPENING, 0.0, ticker="BTC", qty=0.0286),
        ev("2025-12-30", BUY, -1_000_000.0, ticker="SPY", qty=19),
    ]
    assert first_investment(eventos) == date(2025, 12, 30)


def test_sin_inversiones_no_hay_fecha_de_arranque():
    assert first_investment([ev("2023-02-15", "DEPOSIT", 100_000)]) is None
    assert first_investment([]) is None


# --------------------------------------------------------- capital aportado


def test_contributed_series_es_una_escalera():
    serie = [nav("2024-01-01", 1_000), nav("2024-01-02", 1_100), nav("2024-01-03", 6_200)]
    flujos = {date(2024, 1, 3): 5_000.0}
    assert contributed_series(serie, flujos, opening=1_000) == [
        (date(2024, 1, 1), 1_000.0),
        (date(2024, 1, 2), 1_000.0),
        (date(2024, 1, 3), 6_000.0),
    ]


def test_monthly_progress_cierra_cada_mes_con_el_ultimo_dato():
    serie = [
        nav("2025-12-30", 1_400_000.00, 1_500.00),
        nav("2025-12-31", 1_400_000.00, 1_500.00),
        nav("2026-01-02", 7_300_000.00, 1_540.00),
        nav("2026-01-31", 7_000_000.00, 1_500.00),
    ]
    flujos = {date(2025, 12, 30): 1_000_000.0, date(2026, 1, 2): 6_000_000.0}
    meses = monthly_progress(serie, flujos, opening=400_000.0)

    assert [m.month for m in meses] == [date(2025, 12, 1), date(2026, 1, 1)]
    assert meses[0].deposits == 1_000_000.0
    assert meses[0].contributed == pytest.approx(1_400_000.0)
    assert meses[0].value == pytest.approx(1_400_000.0)      # el ultimo del mes
    assert meses[0].gain == pytest.approx(0.0)
    assert meses[1].contributed == pytest.approx(7_400_000.0)
    assert meses[1].gain == pytest.approx(7_000_000.0 - 7_400_000.0)


def test_un_mes_sin_aportes_arrastra_el_capital_anterior():
    serie = [nav("2026-02-01", 6_900_000), nav("2026-03-01", 6_400_000)]
    meses = monthly_progress(serie, {}, opening=7_400_000)
    assert [m.deposits for m in meses] == [0.0, 0.0]
    assert [m.contributed for m in meses] == [7_400_000, 7_400_000]
    assert meses[-1].gain == pytest.approx(6_400_000 - 7_400_000)


def test_el_mes_conoce_su_valor_en_dolares():
    mes = MonthPoint(date(2026, 8, 1), 0.0, 8_000_000, 9_600_000, ccl=1_600.0)
    assert mes.value_usd == pytest.approx(6_000.0)
    assert mes.contributed_usd == pytest.approx(5_000.0)


# ----------------------------------------------------------- las cuentas


def test_capital_aportado_incluye_lo_que_ya_estaba_en_la_cuenta():
    periodo = PeriodMetrics(
        since=date(2025, 12, 30), until=date(2026, 8, 26),
        opening=400_000.0, deposits=8_000_000.0, withdrawals=0.0, value=9_240_000.0,
    )
    assert periodo.contributed == pytest.approx(8_400_000.0)
    assert periodo.gain == pytest.approx(840_000.0)
    assert periodo.gain_pct == pytest.approx(0.10)
    assert periodo.days == 239


def test_los_retiros_restan_del_capital_aportado():
    periodo = PeriodMetrics(
        since=date(2026, 1, 1), until=date(2026, 12, 31),
        opening=0.0, deposits=1_000_000.0, withdrawals=400_000.0, value=700_000.0,
    )
    assert periodo.contributed == pytest.approx(600_000.0)
    assert periodo.gain == pytest.approx(100_000.0)


def test_promedio_mensual_de_aportes():
    meses = [MonthPoint(date(2026, m, 1), 0.0, 0.0, 0.0) for m in range(1, 5)]
    periodo = PeriodMetrics(since=date(2026, 1, 1), until=date(2026, 4, 30),
                            deposits=4_000_000.0, months=meses)
    assert periodo.monthly_average == pytest.approx(1_000_000.0)


def test_sin_meses_no_hay_promedio():
    periodo = PeriodMetrics(since=date(2026, 1, 1), until=date(2026, 1, 2))
    assert periodo.monthly_average is None
    assert periodo.gain_pct is None


# ----------------------------------------------- cuando se movio cada una


def mes_simple(anio, m):
    return MonthPoint(date(anio, m, 1), 0.0, 0.0, 0.0)


def posicion(ticker, first_buy):
    from wallet_tracker.analysis import Position
    return Position(ticker=ticker, currency="Pesos", quantity=1, first_buy=first_buy)


def test_el_mapa_de_calor_mide_cada_mes_de_cierre_a_cierre():
    """Hay un retorno por cada mes menos el primero: el primero es la base."""
    from wallet_tracker.analysis import monthly_returns_by_asset
    from wallet_tracker.valuation import PriceBook

    precios = PriceBook({"SPY": [
        (date(2026, 1, 1), 100.0), (date(2026, 2, 1), 110.0), (date(2026, 3, 1), 99.0),
    ]})
    # Los meses de la cartera; las columnas del mapa son estos menos el primero.
    meses = [mes_simple(2025, 12), mes_simple(2026, 1), mes_simple(2026, 2)]
    filas = monthly_returns_by_asset([posicion("SPY", date(2025, 12, 1))], precios, meses)
    assert filas[0].ticker == "SPY"
    # enero: 100 -> 110.   febrero: 110 -> 99.
    assert filas[0].returns == [pytest.approx(0.10), pytest.approx(-0.10)]
    assert filas[0].best == pytest.approx(0.10)
    assert filas[0].worst == pytest.approx(-0.10)


def test_el_mes_de_compra_se_mide_desde_el_dia_que_compraste():
    """Medirlo entero le atribuiria dias en que la especie no era tuya."""
    from wallet_tracker.analysis import monthly_returns_by_asset
    from wallet_tracker.valuation import PriceBook

    precios = PriceBook({"X": [
        (date(2026, 1, 1), 100.0),      # antes de comprar: no cuenta
        (date(2026, 1, 20), 80.0),      # compraste aca
        (date(2026, 2, 1), 88.0),
    ]})
    meses = [mes_simple(2025, 12), mes_simple(2026, 1)]      # la columna es enero
    filas = monthly_returns_by_asset([posicion("X", date(2026, 1, 20))], precios, meses)
    # 80 -> 88 es +10%, no 100 -> 88 que seria -12%.
    assert filas[0].returns == [pytest.approx(0.10)]


def test_los_meses_anteriores_a_la_compra_quedan_vacios():
    from wallet_tracker.analysis import monthly_returns_by_asset
    from wallet_tracker.valuation import PriceBook

    precios = PriceBook({"GLD": [(date(2026, m, 1), 100.0 + m) for m in range(1, 6)]})
    meses = [mes_simple(2026, m) for m in range(1, 5)]       # columnas: feb, mar, abr
    filas = monthly_returns_by_asset([posicion("GLD", date(2026, 3, 5))], precios, meses)
    assert filas[0].returns[0] is None                       # febrero: todavia no la tenias
    assert filas[0].returns[1] is not None                   # marzo: compraste el 5
    assert filas[0].returns[2] is not None


def test_una_especie_sin_precios_no_ocupa_una_fila():
    from wallet_tracker.analysis import monthly_returns_by_asset
    from wallet_tracker.valuation import PriceBook

    meses = [mes_simple(2026, m) for m in (1, 2)]
    assert monthly_returns_by_asset([posicion("NADA", date(2025, 1, 1))], PriceBook({}), meses) == []


def test_sin_meses_suficientes_no_hay_mapa():
    from wallet_tracker.analysis import monthly_returns_by_asset
    from wallet_tracker.valuation import PriceBook

    assert monthly_returns_by_asset([posicion("X", date(2026, 1, 1))], PriceBook({}), []) == []
