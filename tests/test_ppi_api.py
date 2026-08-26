from datetime import date, datetime

import pytest

from wallet_tracker.config import Settings
from wallet_tracker.ppi_api import (
    PPIError,
    PPISession,
    _chunks,
    _iso,
    normalize_movements,
    normalize_orders,
    normalize_snapshot,
)


def settings(**kwargs):
    base = dict(
        public_key="", private_key="", account_number="", sandbox=True,
        history_start=date(2020, 1, 1), db_path="x.db", ccl_ticker_ars="GD30",
        ccl_ticker_usd="GD30C", ccl_instrument_type="BONOS", ccl_settlement="A-24HS",
    )
    base.update(kwargs)
    return Settings(**base)


@pytest.mark.parametrize("entrada, esperado", [
    ("2022-01-19T14:32:51.776Z", "2022-01-19"),
    ("2022-01-19T14:32:51", "2022-01-19"),
    ("2022-01-19 14:32:51", "2022-01-19"),
    ("2022-01-19", "2022-01-19"),
    ("19/01/2022", "2022-01-19"),
    (datetime(2022, 1, 19, 10, 0), "2022-01-19"),
    (None, None),
    ("", None),
])
def test_normaliza_los_formatos_de_fecha_de_la_api(entrada, esperado):
    assert _iso(entrada) == esperado


def test_los_rangos_largos_se_trocean_sin_huecos_ni_solapamiento():
    tramos = list(_chunks(date(2023, 1, 1), date(2025, 3, 1)))
    assert tramos[0][0] == date(2023, 1, 1)
    assert tramos[-1][1] == date(2025, 3, 1)
    for (_, fin), (inicio, _) in zip(tramos, tramos[1:]):
        assert (inicio - fin).days == 1


def test_sin_credenciales_el_login_explica_como_conseguirlas():
    with pytest.raises(PPIError, match="Gestiones"):
        PPISession(settings()).login()


def test_movimientos_identicos_del_mismo_dia_no_se_pisan():
    # Dos compras iguales el mismo dia son dos movimientos distintos, no uno.
    crudos = [
        {"agreementDate": "2024-03-01T12:00:00", "settlementDate": "2024-03-02T12:00:00",
         "currency": "ARS", "amount": -1000, "price": 10, "description": "Compra GGAL",
         "ticker": "ggal", "quantity": 100, "balance": 5000},
    ] * 2
    filas = normalize_movements("123", crudos)
    assert len({f["uid"] for f in filas}) == 2
    assert [f["ordinal"] for f in filas] == [0, 1]
    assert filas[0]["ticker"] == "GGAL"  # se normaliza a mayusculas


def test_el_uid_es_estable_entre_sincronizaciones():
    crudo = [{"agreementDate": "2024-03-01T12:00:00", "currency": "ARS", "amount": -1000,
              "description": "Compra GGAL", "ticker": "GGAL", "quantity": 100}]
    assert normalize_movements("123", crudo)[0]["uid"] == normalize_movements("123", crudo)[0]["uid"]


def test_las_ordenes_sin_id_se_descartan():
    filas = normalize_orders("123", [{"id": 5, "ticker": "ggal", "quantity": "10"}, {"ticker": "X"}])
    assert len(filas) == 1
    assert filas[0]["order_id"] == 5 and filas[0]["ticker"] == "GGAL" and filas[0]["quantity"] == 10.0


def test_la_foto_de_tenencias_se_aplana_en_efectivo_e_instrumentos():
    payload = {
        "groupedAvailability": [{"currency": "ARS", "availability": [
            {"name": "ARS", "amount": 1500, "settlement": "INMEDIATA"}]}],
        "groupedInstruments": [{"name": "ACCIONES", "instruments": [
            {"ticker": "ggal", "quantity": 100, "price": 10, "amount": 1000}]}],
    }
    filas = normalize_snapshot("123", payload, "2024-03-01T18:00:00")
    tipos = {f["kind"] for f in filas}
    assert tipos == {"cash", "instrument"}
    assert [f for f in filas if f["kind"] == "instrument"][0]["ticker"] == "GGAL"
