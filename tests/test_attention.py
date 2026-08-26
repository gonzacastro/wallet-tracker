"""Que mirar hoy: las condiciones que ameritan un renglon y las que no."""

from datetime import date

from wallet_tracker.analysis import MonthPoint, PeriodMetrics, PortfolioReport, Position
from wallet_tracker.attention import MAX_NOTES, what_to_watch
from wallet_tracker.lots import FifoResult


def posicion(ticker, valor, retorno, *, precio=1_000.0, stale=False):
    return Position(
        ticker=ticker,
        currency="Pesos",
        quantity=10,
        cost_basis=valor / (1 + retorno),
        cost_basis_ars=valor / (1 + retorno),
        price=precio,
        market_value=valor,
        market_value_ars=valor,
        unrealized_pnl_ars=valor - valor / (1 + retorno),
        stale_price=stale,
    )


def mes(anio, m, deposits=0.0, contributed=1_000_000.0, value=1_000_000.0, ccl=1_000.0):
    return MonthPoint(date(anio, m, 1), deposits, contributed, value, ccl=ccl)


def reporte(*, positions=None, meses=None, cash=0.0, warnings=None, nav=None):
    positions = positions or [posicion("SPY", 1_000_000.0, 0.10)]
    periodo = PeriodMetrics(
        since=date(2026, 1, 1), until=date(2026, 8, 26), months=meses or [],
    ) if meses is not None else None
    puntos = nav or []
    return PortfolioReport(
        as_of=date(2026, 8, 26),
        positions=positions,
        closed_positions=[],
        closed_trades=[],
        nav_series=puntos,
        warnings=warnings or [],
        unclassified_events=[],
        events=[],
        fifo=FifoResult(holdings={}, closed=[], warnings=[]),
        investing=periodo,
    )


def textos(report):
    return [n.text for n in what_to_watch(report)]


# --------------------------------------------------- cuando no hay nada que decir


def test_una_cartera_tranquila_no_dice_nada():
    # Aporto el mes pasado, nada viene mal, sin efectivo suelto, y el maximo en
    # dolares quedo atras: no hay nada que decir, asi que no se dice nada.
    valores = [1_000_000, 1_400_000, 1_200_000, 1_250_000]
    meses = [mes(2026, m, value=v) for m, v in enumerate(valores, start=5)]
    meses[-1] = mes(2026, 8, deposits=500_000, value=valores[-1])
    assert what_to_watch(reporte(meses=meses)) == []


def test_sin_etapa_de_inversion_no_se_rompe():
    assert what_to_watch(reporte(meses=None)) == []


# ------------------------------------------------------------------- aportes


def test_avisa_cuando_hace_meses_que_no_aportas():
    meses = [mes(2026, 1, deposits=1_000_000)] + [mes(2026, m) for m in range(2, 9)]
    assert "Hace 7 meses que no aportas" in textos(reporte(meses=meses))


def test_no_molesta_si_aportaste_el_mes_pasado():
    meses = [mes(2026, m) for m in range(1, 8)] + [mes(2026, 8, deposits=500_000)]
    assert not any("no aportas" in t for t in textos(reporte(meses=meses)))


def test_no_avisa_si_nunca_aportaste_en_la_ventana():
    """Sin ningun aporte no hay ritmo que romper: no hay nada que reclamar."""
    meses = [mes(2026, m) for m in range(1, 9)]
    assert not any("no aportas" in t for t in textos(reporte(meses=meses)))


# --------------------------------------------------------------- rezagadas


def test_nombra_la_especie_que_viene_mal():
    posiciones = [posicion("SPY", 1_000_000.0, 0.10), posicion("TSLA", 500_000.0, -0.209)]
    assert "TSLA viene -20.9%" in textos(reporte(positions=posiciones, meses=[]))


def test_una_caida_chica_no_amerita_renglon():
    posiciones = [posicion("XLF", 500_000.0, -0.05)]
    assert not any("viene" in t for t in textos(reporte(positions=posiciones, meses=[])))


def test_cuenta_cuantas_vienen_mal():
    posiciones = [
        posicion("TSLA", 500_000.0, -0.30),
        posicion("XLF", 500_000.0, -0.20),
        posicion("GLD", 500_000.0, -0.16),
    ]
    assert "TSLA viene -30.0% (y 2 mas)" in textos(reporte(positions=posiciones, meses=[]))


# ------------------------------------------------------------------ efectivo


def test_avisa_por_plata_parada():
    # El efectivo sale del ultimo punto de la serie de valuacion.
    from wallet_tracker.valuation import NavPoint

    r = reporte(positions=[posicion("SPY", 1_000_000.0, 0.10, precio=20_000.0)], meses=[])
    r.nav_series = [NavPoint(date(2026, 8, 26), 1_000_000.0, 200_000.0, 1_000.0)]
    assert "Tenes $200,000 sin invertir (16.7% de la cartera)" in textos(r)


def test_no_avisa_si_el_efectivo_es_marginal():
    from wallet_tracker.valuation import NavPoint

    r = reporte(positions=[posicion("SPY", 1_000_000.0, 0.10, precio=20_000.0)], meses=[])
    r.nav_series = [NavPoint(date(2026, 8, 26), 1_000_000.0, 5_000.0, 1_000.0)]
    assert not any("sin invertir" in t for t in textos(r))


def test_no_avisa_si_el_efectivo_no_alcanza_ni_para_un_papel():
    from wallet_tracker.valuation import NavPoint

    r = reporte(positions=[posicion("SPY", 100_000.0, 0.10, precio=90_000.0)], meses=[])
    r.nav_series = [NavPoint(date(2026, 8, 26), 100_000.0, 10_000.0, 1_000.0)]
    assert not any("sin invertir" in t for t in textos(r))


# ---------------------------------------------------------------- los datos


def test_lo_que_no_concilia_va_primero():
    aviso = "SPY: el broker informa 120.00 nominales y el historial da 40.00 (ratio 3:1 -> ...)"
    posiciones = [posicion("TSLA", 500_000.0, -0.30)]
    notas = what_to_watch(reporte(positions=posiciones, meses=[], warnings=[aviso]))
    assert notas[0].kind == "dato"
    assert "SPY no concilia" in notas[0].text


def test_avisa_por_precio_desactualizado():
    posiciones = [posicion("GLD", 500_000.0, 0.05, stale=True)]
    assert any("desactualizado" in t for t in textos(reporte(positions=posiciones, meses=[])))


# ----------------------------------------------------------------- novedades


def test_reconoce_el_mejor_momento_en_dolares():
    meses = [mes(2026, m, value=1_000_000 * m, ccl=1_000.0) for m in range(1, 9)]
    assert "Estas en tu mejor momento medido en dolares" in textos(reporte(meses=meses))


def test_reconoce_haber_cruzado_a_ganancia():
    meses = [
        mes(2026, 7, contributed=1_000_000, value=900_000, ccl=1_000.0),
        mes(2026, 8, contributed=1_000_000, value=1_100_000, ccl=2_000.0),
    ]
    assert "Cruzaste a ganancia este mes" in textos(reporte(meses=meses))


def test_la_buena_noticia_no_desplaza_a_lo_que_hay_que_hacer():
    """Las pendientes tienen su cupo y el logro va aparte, no compite."""
    meses = [mes(2026, 1, deposits=1_000)] + [
        mes(2026, m, value=1_000_000 * m, ccl=1_000.0) for m in range(2, 9)
    ]
    posiciones = [
        posicion("TSLA", 500_000.0, -0.30, stale=True),
        posicion("XLF", 500_000.0, -0.20),
    ]
    notas = what_to_watch(reporte(positions=posiciones, meses=meses, warnings=["X: el broker informa"]))
    assert sum(1 for n in notas if not n.is_good) == MAX_NOTES
    assert sum(1 for n in notas if n.is_good) == 1
    assert notas[-1].is_good


def test_no_repite_el_aviso_de_lo_que_ya_se_sabe_sin_historial():
    """Sin costo conocido, "no concilia" no agrega nada: ya lo dice el otro."""
    posicion_sin_costo = posicion("BTC", 5_000_000.0, 0.0)
    object.__setattr__(posicion_sin_costo, "cost_unknown", True)
    notas = textos(reporte(
        positions=[posicion_sin_costo], meses=[],
        warnings=["BTC: el broker informa 0.04 nominales y el historial da 0.00"],
    ))
    assert not any("no concilia" in t for t in notas)
    assert any("Sin historial de compra en BTC" in t for t in notas)
