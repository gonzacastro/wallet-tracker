"""Prueba la logica del wrapper (troceo, reintentos, fallbacks) con un SDK falso.

Es la parte que no se puede ejercitar contra la API real sin credenciales.
"""

from datetime import date
from types import SimpleNamespace

import pytest

from wallet_tracker.config import Settings
from wallet_tracker.ppi_api import PPIError, PPISession


def settings(**kwargs):
    base = dict(
        public_key="k", private_key="s", account_number="", sandbox=True,
        history_start=date(2020, 1, 1), db_path="x.db", ccl_ticker_ars="GD30",
        ccl_ticker_usd="GD30C", ccl_instrument_type="BONOS", ccl_settlement="A-24HS",
    )
    base.update(kwargs)
    return Settings(**base)


def session_with(fake_api, **kwargs) -> PPISession:
    session = PPISession(settings(**kwargs), throttle=0)
    session._ppi = fake_api
    return session


def test_los_movimientos_se_piden_por_tramos_y_se_concatenan():
    llamadas = []

    def get_movements(params):
        llamadas.append((params.from_date.date(), params.to_date.date()))
        return [{"description": f"mov {len(llamadas)}"}]

    session = session_with(SimpleNamespace(account=SimpleNamespace(get_movements=get_movements)))
    resultado = session.movements("123", date(2023, 1, 1), date(2025, 3, 1))

    assert len(llamadas) == 3          # tres ventanas de hasta un ano
    assert len(resultado) == 3
    assert llamadas[0][0] == date(2023, 1, 1)
    assert llamadas[-1][1] == date(2025, 3, 1)


def test_las_ordenes_se_piden_en_ventanas_mas_cortas():
    llamadas = []

    def get_orders(account, date_from, date_to):
        llamadas.append((date_from.date(), date_to.date()))
        return []

    session = session_with(SimpleNamespace(orders=SimpleNamespace(get_orders=get_orders)))
    session.orders("123", date(2024, 1, 1), date(2024, 12, 31))
    assert len(llamadas) == 5          # ventanas de 90 dias
    assert llamadas[-1][1] == date(2024, 12, 31)


def test_reintenta_y_termina_levantando_un_error_propio(monkeypatch):
    monkeypatch.setattr("wallet_tracker.ppi_api.time.sleep", lambda _: None)
    intentos = {"n": 0}

    def falla(_):
        intentos["n"] += 1
        raise Exception("500 Server Error")

    session = session_with(SimpleNamespace(account=SimpleNamespace(get_movements=falla)))
    with pytest.raises(PPIError, match="500"):
        session.movements("123", date(2024, 1, 1), date(2024, 2, 1))
    assert intentos["n"] == 3


def test_los_precios_prueban_plazos_hasta_encontrar_datos():
    pedidos = []

    def search(ticker, tipo, settlement, desde, hasta):
        pedidos.append(settlement)
        if settlement != "INMEDIATA":
            return []
        return [{"date": "2024-01-02T00:00:00", "price": 10, "volume": 5,
                 "openingPrice": 9, "max": 11, "min": 8}]

    session = session_with(SimpleNamespace(marketdata=SimpleNamespace(search=search)))
    filas = session.price_history("GGAL", "ACCIONES", date(2024, 1, 1), date(2024, 1, 3))

    assert pedidos[0] == "A-24HS" and "INMEDIATA" in pedidos
    assert filas == [{"ticker": "GGAL", "date": "2024-01-02", "settlement": "INMEDIATA",
                      "price": 10.0, "volume": 5.0, "opening": 9.0, "max": 11.0, "min": 8.0}]
    # El plazo que funciono queda cacheado para los proximos pedidos del ticker.
    pedidos.clear()
    session.price_history("GGAL", "ACCIONES", date(2024, 2, 1), date(2024, 2, 3))
    assert pedidos[0] == "INMEDIATA"


def test_los_instrumentos_vencidos_caen_en_el_buscador_sin_filtro():
    usados = []

    def search(*args):
        usados.append("search")
        return []

    def search_skip_filter(*args):
        usados.append("skip_filter")
        return [{"date": "2024-01-02", "price": 7}]

    session = session_with(SimpleNamespace(
        marketdata=SimpleNamespace(search=search, search_skip_filter=search_skip_filter)))
    filas = session.price_history("AL30", "BONOS", date(2024, 1, 1), date(2024, 1, 3))

    assert usados[:2] == ["search", "skip_filter"]
    assert filas[0]["price"] == 7.0


def test_sin_datos_de_precio_devuelve_lista_vacia():
    session = session_with(SimpleNamespace(
        marketdata=SimpleNamespace(search=lambda *a: [], search_skip_filter=lambda *a: [])))
    assert session.price_history("XXXX", "ACCIONES", date(2024, 1, 1), date(2024, 1, 3)) == []


def test_con_una_sola_cuenta_el_numero_se_autodetecta():
    session = session_with(SimpleNamespace(
        account=SimpleNamespace(get_accounts=lambda: [{"accountNumber": "9999"}])))
    assert session.resolve_account_number() == "9999"


def test_con_varias_cuentas_pide_elegir_una():
    session = session_with(SimpleNamespace(account=SimpleNamespace(
        get_accounts=lambda: [{"accountNumber": "1"}, {"accountNumber": "2"}])))
    with pytest.raises(PPIError, match="PPI_ACCOUNT_NUMBER"):
        session.resolve_account_number()


def test_el_numero_configurado_gana_sobre_la_autodeteccion():
    session = session_with(SimpleNamespace(), account_number="4242")
    assert session.resolve_account_number() == "4242"
