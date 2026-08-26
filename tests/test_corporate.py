"""Cambios de ratio de CEDEARs y conciliacion contra la foto del broker.

El caso tipico: una especie con 40 nominales comprados en dos tandas; despues
el CEDEAR cambia de ratio 1:3 y el precio se divide por tres. El broker informa
120 nominales; sin declarar el canje, el FIFO sigue viendo 40 contra el precio
nuevo y la especie aparece con un -61% que nunca ocurrio.
"""

import json
from datetime import date

import pytest

from wallet_tracker.corporate import (
    CorporateAction,
    apply_corporate_actions,
    load_corporate_actions,
    reconcile,
)
from wallet_tracker.ledger import BUY, RATIO_CHANGE, Event
from wallet_tracker.lots import run_fifo
from wallet_tracker.valuation import quantities_by_day

SPY_SPLIT = CorporateAction(ticker="SPY", date=date(2026, 5, 29), ratio=3.0)


def buy(day, ticker, qty, unit, currency="Pesos"):
    return Event(uid=f"b{day}{ticker}", date=date.fromisoformat(day), category=BUY,
                 description=f"COMPRA {ticker}", currency=currency,
                 cash_flow=-qty * unit, ticker=ticker, quantity=qty, price=unit)


def spy_events():
    # Dos tandas: 19 nominales a $50.000 y 21 a $50.000.
    return [
        buy("2025-12-30", "SPY", 19, 50_000.0),
        buy("2026-01-02", "SPY", 21, 50_000.0),
    ]


def test_el_cambio_de_ratio_multiplica_nominales_y_divide_el_costo():
    events = apply_corporate_actions(spy_events(), [SPY_SPLIT])
    holding = run_fifo(events).holdings[("SPY", "Pesos")]
    assert holding.quantity == pytest.approx(120.0)
    # La plata invertida no cambia: son los mismos pesos repartidos en mas papeles.
    assert holding.cost_basis == pytest.approx(2_000_000.0, abs=0.01)
    assert holding.avg_cost == pytest.approx(2_000_000.0 / 120, abs=0.01)


def test_sin_declarar_el_canje_quedan_los_nominales_viejos():
    holding = run_fifo(spy_events()).holdings[("SPY", "Pesos")]
    assert holding.quantity == pytest.approx(40.0)
    assert holding.avg_cost == pytest.approx(50_000.0, abs=0.01)


def test_los_lotes_se_reescalan_uno_por_uno():
    events = apply_corporate_actions(spy_events(), [SPY_SPLIT])
    lots = run_fifo(events).holdings[("SPY", "Pesos")].lots
    assert [round(lot.quantity) for lot in lots] == [57, 63]
    assert sum(lot.cost for lot in lots) == pytest.approx(2_000_000.0, abs=0.01)


def test_la_serie_de_tenencias_cambia_recien_el_dia_del_canje():
    """Antes del canje la valuacion usa nominales viejos y precio viejo."""
    events = apply_corporate_actions(spy_events(), [SPY_SPLIT])
    snaps = quantities_by_day(events)
    assert snaps[date(2026, 1, 2)] == {"SPY": 40}
    assert snaps[date(2026, 5, 29)] == {"SPY": 120}


def test_un_canje_sobre_una_especie_que_no_tenes_no_hace_nada():
    events = apply_corporate_actions(
        spy_events(), [CorporateAction("GGAL", date(2026, 5, 29), 3.0)]
    )
    assert run_fifo(events).holdings[("SPY", "Pesos")].quantity == pytest.approx(40.0)


def test_el_evento_inyectado_no_mueve_plata():
    event = SPY_SPLIT.as_event()
    assert event.category == RATIO_CHANGE
    assert event.cash_flow == 0.0
    assert event.ratio == 3.0


# ------------------------------------------------------------ conciliacion


def test_reconcile_detecta_el_ratio_y_sugiere_la_linea_de_configuracion():
    fifo = run_fifo(spy_events())
    avisos = reconcile(fifo, {"SPY": 120.0})
    assert len(avisos) == 1
    assert "el broker informa 120.00" in avisos[0]
    assert "el historial da 40.00" in avisos[0]
    assert "ratio 3:1" in avisos[0]
    assert '"ticker": "SPY"' in avisos[0]


def test_reconcile_calla_cuando_todo_cuadra():
    fifo = run_fifo(apply_corporate_actions(spy_events(), [SPY_SPLIT]))
    assert reconcile(fifo, {"SPY": 120.0}) == []


def test_reconcile_avisa_aunque_el_desvio_no_sea_un_ratio_limpio():
    fifo = run_fifo(spy_events())
    avisos = reconcile(fifo, {"SPY": 47.5})
    assert "factor 1.1875" in avisos[0]


def test_reconcile_detecta_una_especie_que_no_esta_en_el_historial():
    avisos = reconcile(run_fifo([]), {"GGAL": 500.0})
    assert "GGAL" in avisos[0] and "el historial da 0.00" in avisos[0]


# ------------------------------------------------------------------ carga


def test_load_corporate_actions(tmp_path):
    path = tmp_path / "corporate_actions.json"
    path.write_text(
        json.dumps([
            {"ticker": "spy", "date": "2026-05-29", "ratio": 3, "note": "cambio de ratio"},
            {"ticker": "GGAL", "date": "2024-01-01", "ratio": 0},   # ratio invalido: se ignora
            {"ticker": "YPFD", "ratio": 2},                          # sin fecha: se ignora
        ]),
        encoding="utf-8",
    )
    actions = load_corporate_actions(path)
    assert len(actions) == 1
    assert actions[0] == CorporateAction("SPY", date(2026, 5, 29), 3.0, "cambio de ratio")


def test_load_corporate_actions_sin_archivo(tmp_path):
    assert load_corporate_actions(tmp_path / "no-existe.json") == []
    assert load_corporate_actions(None) == []


def test_la_serie_de_precios_se_empalma_en_el_canje():
    """Sin esto, el grafico muestra un precipicio y las comparaciones mienten.

    Con un canje 1:3, el precio pasa de $56.000 a $18.750 de un dia para el otro.
    Comparar el precio de hoy contra el de antes del canje daria -60,9% cuando en
    realidad subio 17,4%.
    """
    from wallet_tracker.valuation import PriceBook

    prices = PriceBook({"SPY": [
        (date(2025, 12, 30), 52_250.0),
        (date(2026, 5, 28), 56_000.0),
        (date(2026, 5, 29), 18_750.0),
        (date(2026, 8, 26), 20_450.0),
    ]})
    prices.apply_ratio_changes([SPY_SPLIT])

    assert prices.get("SPY", date(2025, 12, 30)) == pytest.approx(17_416.67, abs=0.01)
    assert prices.get("SPY", date(2026, 5, 28)) == pytest.approx(18_666.67, abs=0.01)
    assert prices.get("SPY", date(2026, 5, 29)) == 18_750.0     # posterior: intacto
    antes = prices.get("SPY", date(2025, 12, 30))
    assert prices.get("SPY", date(2026, 8, 26)) / antes - 1 == pytest.approx(0.174, abs=0.001)


def test_un_canje_de_otra_especie_no_toca_la_serie():
    from wallet_tracker.valuation import PriceBook

    prices = PriceBook({"GGAL": [(date(2026, 1, 1), 100.0)]})
    prices.apply_ratio_changes([SPY_SPLIT])
    assert prices.get("GGAL", date(2026, 1, 1)) == 100.0


def test_las_marcas_de_operacion_se_llevan_a_la_escala_de_hoy():
    """El precio al que compraste, en nominales de ahora.

    La serie del mini grafico esta empalmada por el canje, asi que una compra a
    $52.600 de antes del 3:1 tiene que dibujarse en $17.533. Sin esto el punto
    cae fuera del grafico y no se ve.
    """
    from wallet_tracker.analysis import _ratio_since

    antes, despues = date(2026, 1, 2), date(2026, 6, 10)
    assert _ratio_since([SPY_SPLIT], "SPY", antes) == 3.0
    assert 52_600 / _ratio_since([SPY_SPLIT], "SPY", antes) == pytest.approx(17_533.33, abs=0.01)
    # Despues del canje el precio ya esta en la escala nueva: no se toca.
    assert _ratio_since([SPY_SPLIT], "SPY", despues) == 1.0
    # Y un canje de otra especie no la afecta.
    assert _ratio_since([SPY_SPLIT], "AAPL", antes) == 1.0


def test_varios_canjes_encadenados_se_multiplican():
    acciones = [
        CorporateAction("X", date(2025, 1, 1), 2.0),
        CorporateAction("X", date(2026, 1, 1), 5.0),
    ]
    from wallet_tracker.analysis import _ratio_since

    assert _ratio_since(acciones, "X", date(2024, 6, 1)) == 10.0
    assert _ratio_since(acciones, "X", date(2025, 6, 1)) == 5.0
    assert _ratio_since(acciones, "X", date(2026, 6, 1)) == 1.0
