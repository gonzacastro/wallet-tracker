"""Reparto de un aporte nuevo para acercar la cartera al objetivo."""

import json

import pytest

from wallet_tracker.plan import (
    Target,
    allocate,
    drift,
    equal_weights,
    load_targets,
)

# Una cartera de siete especies con una notoriamente mas pesada que el resto:
# es el caso que hace interesante el reparto, porque a esa no se le puede bajar
# el peso comprando.
CARTERA = {
    "SPY": 3_000_000.0, "AAPL": 1_200_000.0, "JPM": 1_200_000.0, "QQQ": 1_200_000.0,
    "GLD": 1_200_000.0, "XLF": 1_200_000.0, "TSLA": 800_000.0,
}


def total(rows):
    return sum(r.amount for r in rows)


# ------------------------------------------------------------- el objetivo


def test_equal_weights_reparte_parejo():
    objetivo = equal_weights(["SPY", "QQQ", "GLD"])
    assert [t.ticker for t in objetivo] == ["GLD", "QQQ", "SPY"]
    assert all(t.weight == pytest.approx(1 / 3) for t in objetivo)


def test_load_targets_normaliza_los_pesos(tmp_path):
    path = tmp_path / "objetivo.json"
    path.write_text(json.dumps([
        {"ticker": "spy", "objetivo": 30, "grupo": "base"},
        {"ticker": "JPM", "objetivo": 10, "grupo": "apuestas"},
        {"ticker": "GGAL", "objetivo": 0},        # sin peso: se ignora
    ]), encoding="utf-8")
    objetivo = load_targets(path)
    assert [(t.ticker, t.group) for t in objetivo] == [("SPY", "base"), ("JPM", "apuestas")]
    assert [t.weight for t in objetivo] == [pytest.approx(0.75), pytest.approx(0.25)]


def test_load_targets_acepta_partes_en_vez_de_porcentaje(tmp_path):
    path = tmp_path / "objetivo.json"
    path.write_text(json.dumps([
        {"ticker": "SPY", "objetivo": 3},
        {"ticker": "QQQ", "objetivo": 1},
    ]), encoding="utf-8")
    assert [t.weight for t in load_targets(path)] == [pytest.approx(0.75), pytest.approx(0.25)]


def test_load_targets_sin_archivo(tmp_path):
    assert load_targets(tmp_path / "no-existe.json") == []
    assert load_targets(None) == []


# --------------------------------------------------------------- el reparto


def test_el_aporte_se_reparte_entero():
    filas = allocate(CARTERA, equal_weights(CARTERA), 1_000_000)
    assert total(filas) == pytest.approx(1_000_000, abs=0.01)


def test_lo_que_esta_arriba_del_objetivo_no_recibe_nada():
    # SPY pesa 27,8% contra un objetivo de 14,3%: comprando no se lo puede bajar,
    # asi que la plata va a las otras y el peso de SPY baja por dilucion.
    filas = allocate(CARTERA, equal_weights(CARTERA), 1_000_000)
    spy = next(f for f in filas if f.ticker == "SPY")
    assert spy.amount == 0.0
    assert spy.final_weight(sum(CARTERA.values()) + 1_000_000) < spy.current_weight(sum(CARTERA.values()))


def test_la_mas_atrasada_recibe_mas():
    filas = allocate(CARTERA, equal_weights(CARTERA), 1_000_000)
    assert filas[0].ticker == "TSLA"          # la de menor peso actual
    assert filas[0].amount > filas[1].amount


def test_si_el_aporte_alcanza_la_cartera_queda_exacta_en_el_objetivo():
    """Con plata de sobra, todas terminan en su peso objetivo."""
    objetivo = [Target("A", 0.5), Target("B", 0.5)]
    filas = allocate({"A": 1_000.0, "B": 0.0}, objetivo, 10_000)
    final = 11_000.0
    assert {f.ticker: f.final_weight(final) for f in filas} == {
        "A": pytest.approx(0.5), "B": pytest.approx(0.5)
    }


def test_si_el_aporte_no_alcanza_se_reparte_a_prorrata_del_faltante():
    objetivo = [Target("A", 0.5), Target("B", 0.5)]
    # A esta en 1.000 y B en 0: a B le falta el triple que a A.
    filas = allocate({"A": 1_000.0, "B": 0.0}, objetivo, 100)
    asignado = {f.ticker: f.amount for f in filas}
    assert asignado["B"] > asignado["A"]
    assert asignado["A"] + asignado["B"] == pytest.approx(100)


def test_una_especie_del_objetivo_que_todavia_no_tenes_recibe_plata():
    objetivo = [Target("SPY", 0.5), Target("VTI", 0.5)]
    filas = allocate({"SPY": 1_000_000.0}, objetivo, 500_000)
    vti = next(f for f in filas if f.ticker == "VTI")
    assert vti.current == 0.0
    assert vti.amount == pytest.approx(500_000)


def test_no_se_asignan_montos_irrisorios():
    objetivo = [Target("A", 0.9999999), Target("B", 0.0000001)]
    filas = allocate({"A": 1_000_000.0, "B": 0.0}, objetivo, 1_000)
    # A B le tocarian 10 centavos: no vale la pena una orden por eso.
    assert next(f for f in filas if f.ticker == "B").amount == 0.0


def test_los_nominales_son_los_enteros_que_entran():
    """Se compran papeles enteros: 4,99 nominales son 4."""
    filas = allocate({"AAPL": 1_139_880.0}, [Target("AAPL", 1.0)], 123_677)
    assert filas[0].units(24_780.0) == 4
    assert filas[0].units(0.0) == 0
    assert filas[0].units(None) == 0


def test_la_comision_puede_sacar_un_nominal():
    """Con 646.300 entran 50 GLD a 12.740... hasta que se suma la comision."""
    fila = allocate({"GLD": 1_000_000.0}, [Target("GLD", 1.0)], 649_000)[0]
    assert fila.units(12_740.0, 0.0) == 50            # 649.000 / 12.740 = 50,9
    assert fila.units(12_740.0, 0.02) == 49           # con 2% ya no entra el 50


def test_el_costo_es_lo_que_se_tipea_en_el_broker():
    """El campo de monto del broker es un presupuesto: con este entran exactos."""
    fila = allocate({"GLD": 1_000_000.0}, [Target("GLD", 1.0)], 646_300)[0]
    assert fila.units(12_740.0, 0.006) == 50
    assert fila.cost(12_740.0, 0.006) == pytest.approx(50 * 12_740 * 1.006)
    # y ese monto alcanza justo para esos 50 nominales
    assert fila.cost(12_740.0, 0.006) >= 50 * 12_740


def test_sin_precio_no_hay_nominales_ni_costo():
    fila = allocate({"X": 1_000.0}, [Target("X", 1.0)], 500)[0]
    assert fila.units(None) == 0 and fila.cost(None) == 0.0


def test_sin_objetivo_o_sin_plata_no_hay_reparto():
    assert allocate(CARTERA, [], 1_000) == []
    assert allocate(CARTERA, equal_weights(CARTERA), 0) == []
    assert allocate(CARTERA, equal_weights(CARTERA), -500) == []


# ------------------------------------------------------------------ desvio


def test_drift_ordena_de_la_mas_atrasada_a_la_mas_adelantada():
    desvio = drift(CARTERA, equal_weights(CARTERA))
    assert desvio[0][0] == "TSLA"     # la mas por debajo del objetivo
    assert desvio[-1][0] == "SPY"     # la mas por encima
    actual, objetivo = desvio[-1][1], desvio[-1][2]
    assert actual > objetivo
