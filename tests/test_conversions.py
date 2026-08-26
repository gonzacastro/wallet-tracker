"""Cambios de moneda hechos con un titulo como vehiculo.

Los datos son sinteticos pero reproducen la estructura del caso que motivo este
modulo: 31 operaciones en las que se compra un titulo en pesos y se vende el
mismo titulo en dolares uno a cinco dias despues. Leidas como compraventa, cada
una dejaba la pata en pesos abierta para siempre y la pata en dolares huerfana.
"""

from datetime import date

import pytest

from wallet_tracker.conversions import (
    FxConversion,
    fx_purchases,
    pair_fx_conversions,
    weighted_rate,
)
from wallet_tracker.ledger import BUY, FX_CONVERSION, SELL, Event
from wallet_tracker.lots import run_fifo

# (fecha compra en pesos, fecha venta en dolares, nominales, pesos pagados, dolares recibidos)
MEP_REAL = [
    ("2022-07-06", "2022-07-09", 400, 50493.74, 158.41),
    ("2022-07-27", "2022-07-29", 2600, 254751.88, 774.19),
    ("2022-08-10", "2022-08-11", 400, 42174.58, 123.95),
    ("2022-08-26", "2022-08-31", 7100, 860066.01, 2471.96),
    ("2022-09-10", "2022-09-12", 12500, 1841248.86, 4848.16),
    ("2022-09-25", "2022-09-26", 3400, 535028.30, 1317.98),
    ("2022-10-14", "2022-10-15", 12500, 1728067.29, 3948.32),
    ("2022-11-04", "2022-11-05", 1800, 260041.72, 577.29),
    ("2022-12-02", "2022-12-07", 5200, 981595.66, 2056.59),
    ("2022-12-22", "2022-12-25", 7100, 1124457.62, 2153.65),
    ("2023-01-10", "2023-01-12", 7100, 1290190.83, 2339.73),
    ("2023-01-25", "2023-01-26", 950, 209040.49, 351.68),
    ("2023-02-19", "2023-02-21", 9300, 2147764.34, 3284.88),
    ("2023-03-19", "2023-03-24", 3400, 858968.35, 1242.15),
    ("2023-03-31", "2023-04-02", 12500, 2775848.77, 3883.26),
    ("2023-04-27", "2023-04-28", 3400, 1026819.60, 1323.25),
    ("2023-05-22", "2023-05-27", 3400, 1029352.30, 1239.23),
    ("2023-06-11", "2023-06-12", 950, 304388.41, 334.48),
    ("2023-06-28", "2023-07-01", 400, 136555.91, 138.01),
    ("2023-07-24", "2023-07-29", 3400, 1471640.23, 1396.74),
    ("2023-08-09", "2023-08-12", 9300, 4035858.80, 3521.48),
    ("2023-08-21", "2023-08-22", 950, 413254.75, 343.60),
    ("2023-09-02", "2023-09-05", 950, 502294.65, 385.97),
    ("2023-09-25", "2023-09-26", 12500, 5603358.33, 4095.78),
    ("2023-10-09", "2023-10-12", 9300, 4306433.86, 2923.34),
    ("2023-10-24", "2023-10-27", 1800, 1084311.94, 685.04),
    ("2023-11-13", "2023-11-15", 3400, 1639442.48, 1000.20),
    ("2023-12-06", "2023-12-07", 7100, 3560561.58, 2125.98),
    ("2023-12-25", "2023-12-26", 400, 236380.63, 135.80),
    ("2024-01-21", "2024-01-23", 9300, 5187125.13, 2865.91),
    ("2024-02-08", "2024-02-11", 12500, 7010721.47, 3650.60),
]

TOTAL_NOMINALES = 165_300
TOTAL_ARS = 52_508_238.51
TOTAL_USD = 55_697.61
TC_PONDERADO = 942.74


def trade(day, category, ticker, qty, amount, currency, ordinal=0):
    return Event(
        uid=f"{day}-{category}-{ticker}-{ordinal}",
        date=date.fromisoformat(day),
        category=category,
        description=f"{category} {ticker}",
        currency=currency,
        cash_flow=amount,
        ticker=ticker,
        quantity=qty,
        ordinal=ordinal,
    )


def mep_events():
    """Las 62 patas, con la forma en que las devuelve la API."""
    events = []
    for buy_day, sell_day, qty, ars, usd in MEP_REAL:
        events.append(trade(buy_day, BUY, "AL30", qty, -ars, "Pesos"))
        events.append(trade(sell_day, SELL, "AL30", qty, usd, "Dolar MEP"))
    return events


# ------------------------------------------------------------------ apareo


def test_las_31_operaciones_se_aparean_todas():
    events, conversions = pair_fx_conversions(mep_events())
    assert len(conversions) == 31
    assert all(e.category == FX_CONVERSION for e in events)


def test_los_totales_de_las_conversiones_son_los_de_la_cuenta():
    _, conversions = pair_fx_conversions(mep_events())
    assert sum(c.from_quantity for c in conversions) == TOTAL_NOMINALES
    assert sum(c.from_amount for c in conversions) == pytest.approx(TOTAL_ARS, abs=0.01)
    assert sum(c.to_amount for c in conversions) == pytest.approx(TOTAL_USD, abs=0.01)
    assert weighted_rate(conversions) == pytest.approx(TC_PONDERADO, abs=0.01)


def test_cada_conversion_guarda_el_tipo_de_cambio_de_ese_dia():
    _, conversions = pair_fx_conversions(mep_events())
    por_fecha = {c.from_date.isoformat(): c for c in conversions}
    # La serie sigue una curva creciente, como el tipo de cambio del periodo.
    assert por_fecha["2022-07-06"].rate == pytest.approx(318.75, abs=0.01)
    assert por_fecha["2024-02-08"].rate == pytest.approx(1920.43, abs=0.01)
    assert all(c.is_fx_purchase for c in conversions)
    assert all(c.matched_by == "cantidad" for c in conversions)
    assert max(c.days for c in conversions) == 5


def test_no_generan_posicion_ni_resultado():
    """El corazon del arreglo: cambiar de moneda no es invertir en el titulo."""
    events, _ = pair_fx_conversions(mep_events())
    result = run_fifo(events)
    assert result.holdings == {}
    assert result.closed == []
    assert result.warnings == []


def test_sin_aparear_aparece_la_posicion_fantasma():
    """Prueba de contraste: asi se veia el bug antes del arreglo."""
    result = run_fifo(mep_events())
    fantasma = result.holdings[("AL30", "Pesos")]
    assert fantasma.quantity == TOTAL_NOMINALES
    assert len(result.warnings) == 31
    assert all("sin compra previa" in w for w in result.warnings)


# ----------------------------------------------------------- casos limite


def test_una_compraventa_de_verdad_no_se_toca():
    """Misma moneda en las dos patas: es una inversion, no una conversion."""
    events = [
        trade("2024-01-10", BUY, "GGAL", 100, -100_000, "Pesos"),
        trade("2024-01-12", SELL, "GGAL", 100, 120_000, "Pesos"),
    ]
    out, conversions = pair_fx_conversions(events)
    assert conversions == []
    assert [e.category for e in out] == [BUY, SELL]


def test_no_aparea_si_pasaron_mas_dias_que_la_ventana():
    events = [
        trade("2024-01-10", BUY, "AL30", 100, -100_000, "Pesos"),
        trade("2024-02-20", SELL, "AL30", 100, 90, "Dolar MEP"),
    ]
    _, conversions = pair_fx_conversions(events)
    assert conversions == []


def test_no_aparea_si_las_cantidades_no_coinciden():
    events = [
        trade("2024-01-10", BUY, "AL30", 100, -100_000, "Pesos"),
        trade("2024-01-11", SELL, "AL30", 60, 55, "Dolar MEP"),
    ]
    _, conversions = pair_fx_conversions(events)
    assert conversions == []


def test_vender_dolares_tambien_es_conversion():
    """El camino inverso: comprar el bono en dolares y venderlo en pesos."""
    events = [
        trade("2024-01-10", BUY, "AL30", 100, -90, "Dolar MEP"),
        trade("2024-01-11", SELL, "AL30", 100, 100_000, "Pesos"),
    ]
    _, conversions = pair_fx_conversions(events)
    assert len(conversions) == 1
    assert not conversions[0].is_fx_purchase
    assert conversions[0].rate == pytest.approx(90 / 100_000)


def test_con_varios_candidatos_gana_el_mas_cercano_en_el_tiempo():
    events = [
        trade("2024-01-10", BUY, "AL30", 100, -100_000, "Pesos"),
        trade("2024-01-15", SELL, "AL30", 100, 80, "Dolar MEP"),
        trade("2024-01-11", SELL, "AL30", 100, 90, "Dolar MEP"),
    ]
    _, conversions = pair_fx_conversions(events)
    assert len(conversions) == 1
    assert conversions[0].to_date == date(2024, 1, 11)


# ------------------------------------------- canje entre bolsillos de dolar


def canje_spy():
    """Cable -> MEP usando SPY: 7 unidades del ETF son 140 CEDEARs (ratio 20)."""
    return [
        trade("2024-04-25", BUY, "SPY", 7, -3536.44, "Dolar Cable"),
        trade("2024-04-30", SELL, "SPY", 140, 3714.18, "Dolar MEP"),
    ]


def test_el_canje_con_ratio_entero_se_aparea():
    events, conversions = pair_fx_conversions(canje_spy())
    assert len(conversions) == 1
    conversion = conversions[0]
    assert conversion.matched_by == "ratio"
    assert conversion.ratio == pytest.approx(20.0)
    assert not conversion.is_fx_purchase
    assert run_fifo(events).holdings == {}


def test_el_canje_no_se_aparea_si_se_apaga_el_nivel_dos():
    _, conversions = pair_fx_conversions(canje_spy(), allow_ratio=False)
    assert conversions == []


def test_no_aparea_por_ratio_si_los_importes_estan_lejos():
    """Dos decisiones de inversion distintas, no un canje."""
    events = [
        trade("2024-04-25", BUY, "SPY", 7, -3536.44, "Dolar Cable"),
        trade("2024-04-30", SELL, "SPY", 140, 12_000.00, "Dolar MEP"),
    ]
    _, conversions = pair_fx_conversions(events)
    assert conversions == []


def test_el_apareo_exacto_gana_sobre_el_apareo_por_ratio():
    events = [
        trade("2024-04-25", BUY, "SPY", 7, -3536.44, "Dolar Cable"),
        trade("2024-04-26", SELL, "SPY", 140, 3714.18, "Dolar MEP", ordinal=1),
        trade("2024-04-27", SELL, "SPY", 7, 3600.00, "Dolar MEP", ordinal=2),
    ]
    _, conversions = pair_fx_conversions(events)
    assert len(conversions) == 1
    assert conversions[0].matched_by == "cantidad"
    assert conversions[0].to_quantity == 7


# ------------------------------------------------------------- accesorios


def test_fx_purchases_filtra_las_compras_de_moneda_dura():
    _, conversions = pair_fx_conversions(mep_events() + canje_spy())
    assert len(conversions) == 32
    assert len(fx_purchases(conversions)) == 31


def test_weighted_rate_pondera_por_monto_y_no_por_operacion():
    grande = FxConversion("AL30", date(2024, 1, 1), "Pesos", 1_000_000.0, 1000,
                          date(2024, 1, 2), "Dolar MEP", 1000.0, 1000)
    chica = FxConversion("AL30", date(2024, 1, 3), "Pesos", 1_000.0, 1,
                         date(2024, 1, 4), "Dolar MEP", 0.5, 1)
    # Promedio simple: $1.500. Ponderado: casi $1.000, porque la chica no pesa.
    assert weighted_rate([grande, chica]) == pytest.approx(1_001_000 / 1000.5)
