"""Corrida completa sobre la cartera sintetica: la base, las metricas y las salidas."""

from datetime import date

import pytest

from wallet_tracker.analysis import build_report
from wallet_tracker.db import connect, init_db
from wallet_tracker.demo import seed
from wallet_tracker.report import render_html, write_report


@pytest.fixture(scope="module")
def report(tmp_path_factory):
    db = tmp_path_factory.mktemp("data") / "demo.db"
    conn = connect(db)
    init_db(conn)
    seed(conn)
    return build_report(conn)


def test_la_cartera_tiene_posiciones_y_operaciones(report):
    assert report.positions, "deberia haber posiciones abiertas"
    assert report.closed_trades, "deberia haber operaciones apareadas"
    assert all(p.first_buy is not None for p in report.positions)
    assert all(p.holding_days and p.holding_days > 0 for p in report.positions)


def test_cada_posicion_conoce_su_costo_y_su_valuacion(report):
    for position in report.positions:
        assert position.quantity > 0
        assert position.avg_cost > 0
        assert position.price and position.price > 0
        assert position.market_value == pytest.approx(position.quantity * position.price)
        assert position.unrealized_pnl == pytest.approx(position.market_value - position.cost_basis)


def test_la_ganancia_total_cierra_contra_sus_componentes(report):
    otros = sum(e.cash_flow for e in report.events
                if e.category in ("OTHER", "ADJUSTMENT") and not e.is_external_flow)
    componentes = (
        report.unrealized_pnl + report.realized_pnl + report.income - report.fees + otros
    )
    assert report.total_pnl == pytest.approx(componentes, rel=1e-6)


def test_la_valuacion_cierra_contra_aportes_mas_resultado(report):
    assert report.nav_ars == pytest.approx(report.net_invested + report.total_pnl, rel=1e-9)


def test_las_tasas_se_calculan_y_son_razonables(report):
    assert report.xirr is not None and -0.99 < report.xirr < 10
    assert report.twr is not None
    assert report.volatility is not None and report.volatility > 0
    assert -1 <= report.max_drawdown <= 0


def test_la_serie_de_valuacion_es_diaria_y_termina_hoy(report):
    fechas = [p.date for p in report.nav_series]
    assert fechas == sorted(fechas)
    assert fechas[-1] == date.today()
    assert (fechas[-1] - fechas[0]).days + 1 == len(fechas)
    assert all(p.nav_ars >= 0 for p in report.nav_series)


def test_la_medicion_en_dolares_usa_el_ccl(report):
    assert report.ccl and report.ccl > 0
    assert report.nav_usd == pytest.approx(report.nav_ars / report.ccl)


def test_el_reporte_html_es_autocontenido(report, tmp_path):
    html = render_html(report)
    assert "<title>" in html and "</svg>" in html
    # El reporte trae JavaScript propio (el repartidor de aportes), pero nada
    # que venga de afuera: ni CDNs, ni imagenes remotas, ni llamadas de red.
    for prohibido in ("http://", "https://", "fetch(", "XMLHttpRequest", "<script src"):
        assert prohibido not in html
    destino = write_report(report, tmp_path / "r.html")
    assert destino.exists() and destino.stat().st_size > 5_000


def test_el_reporte_abre_sin_conexion(report):
    """Todo el JavaScript va inline: el archivo tiene que andar suelto."""
    html = render_html(report)
    import re

    for etiqueta in re.findall(r"<script[^>]*>", html):
        assert "src=" not in etiqueta


def test_los_avisos_marcan_lo_que_no_se_pudo_interpretar(report):
    # La cartera de ejemplo incluye a proposito un movimiento sin regla.
    assert len(report.unclassified_events) == 1
    assert "no identificado" in report.unclassified_events[0].description.lower()
