"""El repartidor de aportes del reporte HTML.

El calculo esta escrito dos veces: en `plan.allocate()` para la terminal y en
JavaScript adentro del reporte, para que puedas tipear un monto y ver el
resultado sin regenerar nada. Estos tests son el seguro contra que se
desincronicen.
"""

import json
import re
import shutil
import subprocess
from datetime import date

import pytest

from wallet_tracker.analysis import PortfolioReport, Position
from wallet_tracker.lots import FifoResult
from wallet_tracker.plan import allocate, equal_weights
from wallet_tracker.report import render_html


def funcion_js(html, nombre):
    """Extrae una funcion del reporte para correrla en node."""
    m = re.search(rf"function {nombre}\([^)]*\) \{{.*?\n\}}", html, re.S)
    assert m, f"no se encontro la funcion {nombre} en el reporte"
    return m.group(0)


def position(ticker, value, price, quantity):
    return Position(
        ticker=ticker,
        currency="Pesos",
        description=f"{ticker} SA",
        quantity=quantity,
        cost_basis=value * 0.9,
        cost_basis_ars=value * 0.9,
        price=price,
        market_value=value,
        market_value_ars=value,
        unrealized_pnl=value * 0.1,
        unrealized_pnl_ars=value * 0.1,
        first_buy=date(2026, 1, 2),
        price_series=[(date(2026, 1, 2), price * 0.9), (date(2026, 8, 26), price)],
        marks=[(date(2026, 1, 2), price * 0.9, "BUY")],
    )


#: La misma comision con la que se genera el reporte en el fixture.
COMISION = 0.006

CARTERA = [
    position("SPY", 3_000_000.0, 20_000.0, 150),
    position("TSLA", 800_000.0, 40_000.0, 20),
    position("JPM", 1_200_000.0, 40_000.0, 30),
]


@pytest.fixture
def reporte_html():
    report = PortfolioReport(
        as_of=date(2026, 8, 26),
        positions=CARTERA,
        closed_positions=[],
        closed_trades=[],
        nav_series=[],
        warnings=[],
        unclassified_events=[],
        events=[],
        fifo=FifoResult(holdings={}, closed=[], warnings=[]),
    )
    return render_html(report, COMISION)


def test_el_reporte_trae_los_datos_de_cada_especie(reporte_html):
    datos = json.loads(re.search(r"const DATOS = (\[.*?\]);", reporte_html, re.S).group(1))
    assert [d["ticker"] for d in datos] == ["SPY", "TSLA", "JPM"]
    assert datos[0]["price"] == 20_000.0
    assert datos[0]["value"] == 3_000_000.0


def test_hay_una_tarjeta_y_un_deslizador_por_especie(reporte_html):
    assert reporte_html.count('class="inst"') == len(CARTERA)
    assert reporte_html.count('class="tune-row"') == len(CARTERA)


def test_el_plan_solo_incluye_lo_que_tenes(reporte_html):
    """No se agregan especies a mano: el plan es sobre la cartera que existe."""
    for fuera in ('id="nuevo"', "ppi-extras", "quitar"):
        assert fuera not in reporte_html


def test_arrastrar_un_deslizador_no_redibuja_su_fila(reporte_html):
    """Si se reemplaza la fila entera, el navegador pierde el arrastre."""
    cuerpo = re.search(r"function pintarSliders\(\).*?\n\}", reporte_html, re.S).group(0)
    assert "textContent" in cuerpo
    assert "innerHTML" not in cuerpo


def test_cada_tarjeta_trae_su_mini_grafico(reporte_html):
    assert reporte_html.count('class="spark"') == len(CARTERA)
    # Un punto por operacion: una compra en cada una de las tres.
    assert reporte_html.count("<circle") == len(CARTERA)


def test_el_reporte_ya_no_trae_las_secciones_que_se_sacaron(reporte_html):
    for fuera in ("Posiciones abiertas", "Posiciones cerradas",
                  "Ultimas operaciones cerradas", "Conversiones de moneda",
                  "Dolar comprado"):
        assert fuera not in reporte_html


def test_el_javascript_no_apunta_a_elementos_que_no_existen(reporte_html):
    """Un id mal escrito rompe la pagina en silencio: el navegador no avisa."""
    ids_usados = set(re.findall(r'getElementById\("([^"]+)"\)', reporte_html))
    ids_presentes = set(re.findall(r'id="([^"]+)"', reporte_html))
    assert ids_usados, "el reporte tendria que traer el script del repartidor"
    assert ids_usados <= ids_presentes, f"faltan en el HTML: {ids_usados - ids_presentes}"


def test_las_clases_que_busca_el_script_estan_en_el_html(reporte_html):
    for selector in (".tune-row", ".chip[data-amount]", ".tune-row input[type=range]"):
        clase = selector.split("[")[0].lstrip(".")
        assert f'class="{clase}"' in reporte_html or f"{clase}" in reporte_html


def test_se_puede_dejar_una_especie_afuera_del_aporte(reporte_html):
    """Una casilla por fila, y un preset para las que vienen perdiendo."""
    assert reporte_html.count('class="incluir"') == len(CARTERA)
    assert 'id="preset-ganadoras"' in reporte_html
    assert 'id="preset-todas"' in reporte_html
    assert "ppi-excluidos-v1" in reporte_html


def test_el_retorno_de_cada_especie_viaja_al_navegador(reporte_html):
    """El preset de "saltear las que pierden" necesita saber cual pierde."""
    datos = json.loads(re.search(r"const DATOS = (\[.*?\]);", reporte_html, re.S).group(1))
    assert all("ret" in d for d in datos)


@pytest.mark.skipif(not shutil.which("node"), reason="node no esta instalado")
def test_excluir_una_especie_reparte_su_plata_entre_las_demas(reporte_html, tmp_path):
    datos = re.search(r"const DATOS = (\[.*?\]);", reporte_html, re.S).group(1)
    fuente = "\n".join(
        funcion_js(reporte_html, n) for n in ("repartir", "nominales", "aPagar")
    )
    harness = tmp_path / "excluir.js"
    harness.write_text(
        f"const DATOS = {datos};\n"
        f"const COMISION = {COMISION};\n"
        "const objetivo = {};\n"
        "DATOS.forEach(d => objetivo[d.ticker] = 100 / DATOS.length);\n"
        "const sinTSLA = {...objetivo, TSLA: 0};\n"
        f"{fuente}\n"
        "const con = repartir(1000000, objetivo).filas;\n"
        "const sin = repartir(1000000, sinTSLA).filas;\n"
        "console.log(JSON.stringify({\n"
        "  tslaCon: con.find(f => f.ticker === 'TSLA').monto,\n"
        "  tslaSin: sin.find(f => f.ticker === 'TSLA').monto,\n"
        "  totalCon: con.reduce((s, f) => s + f.monto, 0),\n"
        "  totalSin: sin.reduce((s, f) => s + f.monto, 0),\n"
        "}));",
        encoding="utf-8",
    )
    r = json.loads(subprocess.run(["node", str(harness)], capture_output=True,
                                  text=True, check=True).stdout)
    assert r["tslaCon"] > 0            # con todas, TSLA recibe
    assert r["tslaSin"] == 0           # excluida, no recibe nada
    # y la plata no se pierde: sigue repartiendose entera entre las demas
    assert r["totalSin"] == pytest.approx(r["totalCon"], abs=1.0)


@pytest.mark.skipif(not shutil.which("node"), reason="node no esta instalado")
def test_el_javascript_del_reporte_es_sintacticamente_valido(reporte_html, tmp_path):
    """Un parentesis de mas deja la pagina muerta sin que nada avise."""
    scripts = re.findall(r"<script>(.*?)</script>", reporte_html, re.S)
    assert scripts, "el reporte tendria que traer el script del repartidor"
    archivo = tmp_path / "todo.js"
    archivo.write_text("\n".join(scripts), encoding="utf-8")
    subprocess.run(["node", "--check", str(archivo)], capture_output=True, text=True, check=True)


@pytest.mark.skipif(not shutil.which("node"), reason="node no esta instalado")
@pytest.mark.parametrize("monto", [50_000, 1_000_000, 20_000_000])
def test_el_reparto_en_javascript_da_lo_mismo_que_en_python(reporte_html, tmp_path, monto):
    datos = re.search(r"const DATOS = (\[.*?\]);", reporte_html, re.S).group(1)
    fuente = "\n".join(
        funcion_js(reporte_html, n) for n in ("repartir", "nominales", "aPagar")
    )
    harness = tmp_path / "harness.js"
    harness.write_text(
        f"const DATOS = {datos};\n"
        f"const COMISION = {COMISION};\n"
        "const objetivo = {};\n"
        "DATOS.forEach(d => objetivo[d.ticker] = 100 / DATOS.length);\n"
        f"{fuente}\n"
        f"const r = repartir({monto}, objetivo).filas;\n"
        "console.log(JSON.stringify(r.map(f => "
        "[f.ticker, Math.round(f.monto * 100) / 100, nominales(f),"
        " Math.round(aPagar(f) * 100) / 100])));",
        encoding="utf-8",
    )
    salida = subprocess.run(
        ["node", str(harness)], capture_output=True, text=True, check=True
    ).stdout
    en_js = [tuple(fila) for fila in json.loads(salida)]

    holdings = {p.ticker: p.market_value_ars for p in CARTERA}
    precios = {p.ticker: p.price for p in CARTERA}
    en_python = [
        (
            row.ticker,
            round(row.amount, 2),
            row.units(precios[row.ticker], COMISION),
            round(row.cost(precios[row.ticker], COMISION), 2),
        )
        for row in allocate(holdings, equal_weights(holdings), monto)
    ]
    assert en_js == en_python
