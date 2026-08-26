from datetime import date

import pytest

from wallet_tracker.money import (
    ARS,
    PESOS,
    USD,
    Converter,
    currency_class,
    is_ars,
    normalize_currency,
)
from wallet_tracker.valuation import FxBook


@pytest.mark.parametrize(
    "label, esperado",
    [
        ("Pesos", PESOS),
        ("pesos", PESOS),
        ("ARS", PESOS),
        ("$", PESOS),
        (None, PESOS),
        ("", PESOS),
        ("Dolar MEP", "DOLAR MEP"),
        ("Dolar Cable", "DOLAR CABLE"),
        # Mismo bolsillo con distinta liquidacion: no son dos monedas.
        ("Dolar Cable - Rescate", "DOLAR CABLE"),
        ("DolarCV7000 Ext.", "DOLARCV7000 EXT."),
        ("Dólar Divisa | CCL", "DOLAR DIVISA | CCL"),
    ],
)
def test_normalize_currency(label, esperado):
    assert normalize_currency(label) == esperado


def test_currency_class_todo_lo_que_no_es_peso_es_dolar():
    assert currency_class("Pesos") == ARS
    assert currency_class("Dolar MEP") == USD
    assert currency_class("DolarCV7000 Ext.") == USD
    assert is_ars("ARS") and not is_ars("Dolar Cable")


def test_converter_deja_los_pesos_intactos():
    fx = FxBook([(date(2024, 1, 1), 1000.0)])
    assert Converter(fx).to_ars(500.0, "Pesos", date(2024, 1, 1)) == 500.0


def test_converter_pasa_dolares_a_pesos_con_la_cotizacion_del_dia():
    fx = FxBook([(date(2023, 1, 1), 400.0), (date(2024, 1, 1), 1000.0)])
    converter = Converter(fx)
    assert converter.to_ars(10.0, "Dolar MEP", date(2023, 6, 1)) == 4000.0
    assert converter.to_ars(10.0, "Dolar MEP", date(2024, 6, 1)) == 10_000.0


def test_converter_anota_las_fechas_sin_cotizacion_en_vez_de_mentir():
    converter = Converter(None)
    assert converter.to_ars(10.0, "Dolar MEP", date(2024, 1, 1)) == 10.0
    assert converter.missing == {date(2024, 1, 1)}


def test_converter_no_marca_como_faltante_un_importe_en_pesos():
    converter = Converter(None)
    converter.to_ars(500.0, "Pesos", date(2024, 1, 1))
    assert converter.missing == set()
