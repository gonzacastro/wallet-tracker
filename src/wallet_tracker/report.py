"""Reporte HTML autocontenido (sin dependencias externas ni CDNs)."""

from __future__ import annotations

import html
import json
from datetime import date
from pathlib import Path
from typing import Sequence

from .analysis import PortfolioReport, contributed_series
from .attention import what_to_watch

CSS = """
:root {
  --bg: #fbfaf8; --panel: #ffffff; --ink: #1c1b19; --muted: #6b6864;
  --line: #e5e1da; --pos: #157f52; --neg: #b3261e; --accent: #2f6f8f;
  --grid: #efece6;
}
:root:not([data-theme="light"]) {}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg: #14140f; --panel: #1c1c17; --ink: #f0eee9; --muted: #a3a09a;
    --line: #302f28; --pos: #4ac68a; --neg: #ef8a80; --accent: #7fbcd8;
    --grid: #262620;
  }
}
:root[data-theme="dark"] {
  --bg: #14140f; --panel: #1c1c17; --ink: #f0eee9; --muted: #a3a09a;
  --line: #302f28; --pos: #4ac68a; --neg: #ef8a80; --accent: #7fbcd8;
  --grid: #262620;
}
* { box-sizing: border-box; }
body {
  margin: 0; padding: 2rem 1.25rem 4rem; background: var(--bg); color: var(--ink);
  font: 15px/1.5 ui-sans-serif, -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
}
.wrap { max-width: 1180px; margin: 0 auto; }
h1 { font-size: 1.6rem; margin: 0 0 .25rem; letter-spacing: -.02em; }
h2 { font-size: 1.05rem; margin: 2.5rem 0 .75rem; letter-spacing: -.01em; }
.sub { color: var(--muted); margin: 0 0 2rem; font-size: .92rem; }
.cards { display: grid; gap: .75rem; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); }
.card {
  background: var(--panel); border: 1px solid var(--line); border-radius: 10px; padding: .9rem 1rem;
}
.card .k { color: var(--muted); font-size: .78rem; text-transform: uppercase; letter-spacing: .04em; }
.card .v { font-size: 1.32rem; font-weight: 600; margin-top: .2rem; font-variant-numeric: tabular-nums; }
.card .n { color: var(--muted); font-size: .8rem; margin-top: .15rem; }
.pos { color: var(--pos); } .neg { color: var(--neg); }
.panel {
  background: var(--panel); border: 1px solid var(--line); border-radius: 10px;
  padding: 1rem; overflow-x: auto;
}
table { border-collapse: collapse; width: 100%; font-size: .88rem; }
th, td { padding: .5rem .6rem; text-align: right; white-space: nowrap; }
th { color: var(--muted); font-weight: 600; font-size: .76rem; text-transform: uppercase;
     letter-spacing: .04em; border-bottom: 1px solid var(--line); }
td { border-bottom: 1px solid var(--grid); font-variant-numeric: tabular-nums; }
tr:last-child td { border-bottom: none; }
th:first-child, td:first-child { text-align: left; }
.ticker { font-weight: 600; }
.desc { color: var(--muted); font-weight: 400; font-size: .8rem; display: block; }
.note { color: var(--muted); font-size: .85rem; margin-top: .6rem; }
ul.avisos { margin: 0; padding-left: 1.1rem; color: var(--muted); font-size: .87rem; }
ul.avisos li { margin: .2rem 0; }
.legend { color: var(--muted); font-size: .8rem; margin-top: .4rem; }
.legend b { color: var(--ink); font-weight: 600; }
.watch { display: flex; flex-direction: column; gap: .4rem; background: var(--panel);
         border: 1px solid var(--line); border-left: 3px solid var(--accent);
         border-radius: 10px; padding: .85rem 1rem; margin: 0 0 1.5rem; }
.watch .w-title { color: var(--muted); font-size: .72rem; text-transform: uppercase;
                  letter-spacing: .06em; }
.watch p { margin: 0; font-size: .95rem; display: flex; gap: .5rem; align-items: baseline; }
.watch .dot { flex: none; width: .45rem; height: .45rem; border-radius: 50%; background: var(--accent); }
.watch .dot.good { background: var(--pos); }
.watch .dot.data { background: var(--neg); }
footer { color: var(--muted); font-size: .8rem; margin-top: 3rem; border-top: 1px solid var(--line);
         padding-top: 1rem; }

/* --- como viene cada instrumento -------------------------------------- */
.insts { display: grid; gap: .7rem; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr)); }
.inst { background: var(--panel); border: 1px solid var(--line); border-radius: 10px; padding: .85rem .95rem; }
.inst-top { display: flex; justify-content: space-between; align-items: baseline; gap: .5rem; }
.inst-tk { font-weight: 700; font-size: 1.05rem; letter-spacing: -.01em; }
.inst-ret { font-size: 1.15rem; font-weight: 600; font-variant-numeric: tabular-nums; }
.inst-grp { color: var(--muted); font-size: .72rem; text-transform: uppercase; letter-spacing: .05em; }
.inst-bar { position: relative; height: 7px; background: var(--grid); border-radius: 4px; margin: .6rem 0 .5rem; }
.inst-bar::before { content: ""; position: absolute; left: 50%; top: -2px; bottom: -2px; width: 1px; background: var(--line); }
.inst-bar i { position: absolute; top: 0; bottom: 0; border-radius: 4px; }
.inst-foot { display: flex; justify-content: space-between; gap: .5rem; color: var(--muted);
             font-size: .78rem; font-variant-numeric: tabular-nums; }
.spark { display: block; width: 100%; height: auto; margin: .1rem 0 .55rem; }

/* --- mapa de calor: cuando se torcio que --------------------------------- */
.heat { border-collapse: separate; border-spacing: 2px; width: 100%; font-size: .78rem; }
.heat th { border: 0; padding: .2rem .3rem; }
.heat td {
  border: 0; border-radius: 4px; padding: .4rem .3rem; text-align: center;
  font-variant-numeric: tabular-nums; white-space: nowrap;
}
.heat td.tk { text-align: left; font-weight: 600; font-size: .82rem; background: none;
              padding-left: 0; }
.heat td.na { color: var(--muted); opacity: .45; }

/* --- donde pongo la plata --------------------------------------------- */
.money-row { display: flex; flex-wrap: wrap; gap: .5rem; align-items: center; margin-bottom: 1rem; }
.money-row label { color: var(--muted); font-size: .88rem; }
#monto {
  font: 600 1.25rem/1 ui-sans-serif, system-ui, sans-serif; font-variant-numeric: tabular-nums;
  padding: .5rem .7rem; width: 11rem; color: var(--ink); background: var(--bg);
  border: 1px solid var(--line); border-radius: 8px;
}
#monto:focus { outline: 2px solid var(--accent); outline-offset: 1px; }
.chip {
  font: inherit; font-size: .82rem; padding: .38rem .7rem; cursor: pointer; color: var(--muted);
  background: var(--bg); border: 1px solid var(--line); border-radius: 999px;
}
.chip:hover { color: var(--ink); border-color: var(--accent); }
.chip:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.buy { font-weight: 600; color: var(--pos); }
.skip { color: var(--muted); }
.units { font-weight: 600; }

details.tune { margin-top: 1rem; }
details.tune summary { cursor: pointer; color: var(--muted); font-size: .85rem; padding: .3rem 0; }
details.tune summary:hover { color: var(--ink); }
.tune-grid { display: grid; gap: .55rem .9rem; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
             margin-top: .8rem; }
.tune-row { display: grid; grid-template-columns: 3.4rem 1fr 3rem; align-items: center; gap: .6rem; }
.tune-row span:first-child { font-weight: 600; font-size: .85rem; }
.tune-row output { color: var(--muted); font-size: .82rem; text-align: right; font-variant-numeric: tabular-nums; }
.tune-row input[type=range] { width: 100%; accent-color: var(--accent); }
"""


#: El reparto se calcula en el navegador para que puedas tipear un monto y ver
#: el resultado al toque, sin volver a generar el reporte. Es el mismo algoritmo
#: que `plan.allocate()`, portado: cubrir lo que le falta a cada especie para
#: llegar a su peso objetivo, a prorrata si el aporte no alcanza.
ALLOCATOR_JS = """
const fmt = new Intl.NumberFormat("es-AR", {maximumFractionDigits: 0});
const pct = (v) => (v * 100).toFixed(1).replace(".", ",") + "%";
const guardado = "ppi-objetivo-v1";

let objetivo = {};
try {
  objetivo = JSON.parse(localStorage.getItem(guardado)) || {};
} catch (e) { objetivo = {}; }
if (!DATOS.every((d) => typeof objetivo[d.ticker] === "number")) objetivo = {};
if (!Object.keys(objetivo).length) {
  const total = DATOS.reduce((s, d) => s + d.value, 0) || 1;
  DATOS.forEach((d) => { objetivo[d.ticker] = (d.target ?? d.value / total) * 100; });
}

function persistir() {
  try { localStorage.setItem(guardado, JSON.stringify(objetivo)); } catch (e) {}
}

function repartir(monto) {
  const actual = DATOS.reduce((s, d) => s + d.value, 0);
  const final = actual + monto;
  const suma = Object.values(objetivo).reduce((a, b) => a + b, 0) || 1;
  const filas = DATOS.map((d) => {
    const peso = objetivo[d.ticker] / suma;
    return {...d, peso, falta: Math.max(0, peso * final - d.value), monto: 0};
  });
  const necesario = filas.reduce((s, f) => s + f.falta, 0);
  if (monto > 0 && necesario <= monto) {
    const sobra = monto - necesario;
    filas.forEach((f) => { f.monto = f.falta + sobra * f.peso; });
  } else if (monto > 0 && necesario > 0) {
    filas.forEach((f) => { f.monto = monto * f.falta / necesario; });
  }
  filas.forEach((f) => { if (f.monto < 1) f.monto = 0; });
  filas.sort((a, b) => b.monto - a.monto);
  return {filas, actual, final};
}

function leerMonto() {
  const crudo = (document.getElementById("monto").value || "").replace(/[^0-9]/g, "");
  return crudo ? parseInt(crudo, 10) : 0;
}

function pintar() {
  const {filas, actual, final} = repartir(leerMonto());
  document.getElementById("reparto-body").innerHTML = filas.map((f) => {
    const nominales = f.price ? Math.floor(f.monto / f.price) : 0;
    const accion = f.monto > 0
      ? '<span class="buy">$' + fmt.format(Math.round(f.monto)) + "</span>"
      : '<span class="skip">ya esta arriba</span>';
    const cuantos = nominales >= 1
      ? '<span class="units">' + nominales + "</span> x $" + fmt.format(f.price)
      : '<span class="skip">&mdash;</span>';
    return '<tr><td class="ticker">' + f.ticker + "</td>"
      + "<td>" + pct(actual ? f.value / actual : 0) + "</td>"
      + "<td>" + pct(f.peso) + "</td>"
      + "<td>" + accion + "</td>"
      + "<td>" + cuantos + "</td>"
      + "<td>" + pct(final ? (f.value + f.monto) / final : 0) + "</td></tr>";
  }).join("");
  const puesto = filas.reduce((s, f) => s + f.monto, 0);
  document.getElementById("reparto-total").textContent = "$" + fmt.format(Math.round(puesto));
}

// Solo se actualiza el texto del porcentaje, no la fila entera: si se redibuja
// el deslizador mientras lo arrastras, el navegador pierde el arrastre.
function pintarSliders() {
  const suma = Object.values(objetivo).reduce((a, b) => a + b, 0) || 1;
  document.querySelectorAll(".tune-row").forEach((row) => {
    row.querySelector("output").textContent = pct(objetivo[row.dataset.ticker] / suma);
  });
}

document.getElementById("monto").addEventListener("input", (e) => {
  const n = (e.target.value || "").replace(/[^0-9]/g, "");
  e.target.value = n ? fmt.format(parseInt(n, 10)) : "";
  pintar();
});
document.querySelectorAll(".chip[data-amount]").forEach((b) => {
  b.addEventListener("click", () => {
    document.getElementById("monto").value = fmt.format(parseInt(b.dataset.amount, 10));
    pintar();
  });
});
document.querySelectorAll(".tune-row input[type=range]").forEach((slider) => {
  slider.addEventListener("input", () => {
    objetivo[slider.closest(".tune-row").dataset.ticker] = parseFloat(slider.value);
    persistir(); pintarSliders(); pintar();
  });
});
document.getElementById("preset-iguales").addEventListener("click", () => {
  DATOS.forEach((d) => { objetivo[d.ticker] = 100 / DATOS.length; });
  sincronizarSliders();
});
document.getElementById("preset-actual").addEventListener("click", () => {
  const total = DATOS.reduce((s, d) => s + d.value, 0) || 1;
  DATOS.forEach((d) => { objetivo[d.ticker] = (d.value / total) * 100; });
  sincronizarSliders();
});
function sincronizarSliders() {
  document.querySelectorAll(".tune-row input[type=range]").forEach((slider) => {
    slider.value = objetivo[slider.closest(".tune-row").dataset.ticker];
  });
  persistir(); pintarSliders(); pintar();
}
sincronizarSliders();
"""


def _fmt_money(value: float | None, symbol: str = "$", decimals: int = 0) -> str:
    if value is None:
        return "—"
    return f"{symbol}{value:,.{decimals}f}"


def _fmt_pct(value: float | None, decimals: int = 1) -> str:
    if value is None:
        return "—"
    return f"{value * 100:,.{decimals}f}%"


def _cls(value: float | None) -> str:
    if value is None or value == 0:
        return ""
    return "pos" if value > 0 else "neg"


def _num(value: float | None, formatter, decimals=0, symbol="$") -> str:
    text = formatter(value, symbol, decimals) if formatter is _fmt_money else formatter(value, decimals)
    css = _cls(value)
    return f'<span class="{css}">{text}</span>' if css else text


def line_chart(
    points: Sequence[tuple[date, float]],
    *,
    width: int = 1120,
    height: int = 220,
    color: str = "var(--accent)",
    label: str = "",
) -> str:
    """Grafico de linea con area, escalado al rango de la serie."""
    values = [v for _, v in points if v is not None]
    if len(values) < 2:
        return '<p class="note">Sin datos suficientes para graficar.</p>'
    lo, hi = min(values), max(values)
    span = (hi - lo) or (hi or 1)
    pad_l, pad_r, pad_t, pad_b = 8, 8, 12, 22
    inner_w = width - pad_l - pad_r
    inner_h = height - pad_t - pad_b
    n = len(points)

    def x(i: int) -> float:
        return pad_l + (inner_w * i / (n - 1))

    def y(v: float) -> float:
        return pad_t + inner_h - ((v - lo) / span) * inner_h

    path = " ".join(
        f"{'M' if i == 0 else 'L'}{x(i):.1f},{y(v):.1f}" for i, (_, v) in enumerate(points)
    )
    area = f"{path} L{x(n - 1):.1f},{pad_t + inner_h:.1f} L{x(0):.1f},{pad_t + inner_h:.1f} Z"
    first_d, last_d = points[0][0], points[-1][0]
    return f"""<svg viewBox="0 0 {width} {height}" width="100%" role="img"
 style="height:auto;display:block" aria-label="{html.escape(label or 'evolucion')}">
  <path d="{area}" fill="{color}" opacity="0.10"/>
  <path d="{path}" fill="none" stroke="{color}" stroke-width="2" stroke-linejoin="round"/>
  <text x="{pad_l}" y="{height - 6}" font-size="11" fill="var(--muted)">{first_d.isoformat()}</text>
  <text x="{width - pad_r}" y="{height - 6}" font-size="11" fill="var(--muted)"
   text-anchor="end">{last_d.isoformat()}</text>
  <text x="{pad_l}" y="{pad_t + 2}" font-size="11" fill="var(--muted)">max {hi:,.0f}</text>
</svg>"""


def growth_chart(
    value_points: Sequence[tuple[date, float]],
    contributed_points: Sequence[tuple[date, float]],
    *,
    width: int = 1120,
    height: int = 260,
) -> str:
    """Capital aportado contra valuacion: la ganancia es lo que queda arriba.

    La escalera gris es tu plata; la linea es lo que vale la cartera. El relleno
    entre las dos es el rendimiento -- verde cuando estas arriba del capital que
    pusiste, rojo cuando estas abajo. Es la vista del interes compuesto: cada
    aporte sube el escalon y la ganancia se acumula por encima.
    """
    if len(value_points) < 2 or len(contributed_points) != len(value_points):
        return '<p class="note">Sin datos suficientes para graficar.</p>'
    todos = [v for _, v in value_points] + [c for _, c in contributed_points]
    lo, hi = min(min(todos), 0.0), max(todos)
    span = (hi - lo) or (hi or 1)
    pad_l, pad_r, pad_t, pad_b = 8, 8, 16, 24
    inner_w = width - pad_l - pad_r
    inner_h = height - pad_t - pad_b
    n = len(value_points)

    def x(i: int) -> float:
        return pad_l + (inner_w * i / (n - 1))

    def y(v: float) -> float:
        return pad_t + inner_h - ((v - lo) / span) * inner_h

    linea_valor = " ".join(
        f"{'M' if i == 0 else 'L'}{x(i):.1f},{y(v):.1f}" for i, (_, v) in enumerate(value_points)
    )
    # El capital es una escalera: se mantiene plano y salta el dia del aporte.
    pasos: list[str] = []
    for i, (_, c) in enumerate(contributed_points):
        if i == 0:
            pasos.append(f"M{x(i):.1f},{y(c):.1f}")
        else:
            pasos.append(f"L{x(i):.1f},{y(contributed_points[i - 1][1]):.1f}")
            pasos.append(f"L{x(i):.1f},{y(c):.1f}")
    linea_capital = " ".join(pasos)

    base = f"L{x(n - 1):.1f},{pad_t + inner_h:.1f} L{x(0):.1f},{pad_t + inner_h:.1f} Z"
    area_capital = f"{linea_capital} {base}"

    # Franja entre las dos curvas, recortada por arriba y por abajo del capital
    # para pintar ganancia y perdida de colores distintos.
    entre = (
        linea_valor
        + " "
        + " ".join(
            f"L{x(i):.1f},{y(c):.1f}"
            for i, (_, c) in reversed(list(enumerate(contributed_points)))
        )
        + " Z"
    )
    tope_capital = " ".join(pasos) + f" L{x(n - 1):.1f},{pad_t:.1f} L{x(0):.1f},{pad_t:.1f} Z"

    final_valor, final_capital = value_points[-1][1], contributed_points[-1][1]
    return f"""<svg viewBox="0 0 {width} {height}" width="100%" role="img"
 style="height:auto;display:block" aria-label="capital aportado contra valuacion">
  <defs>
    <clipPath id="sobre-capital"><path d="{tope_capital}"/></clipPath>
    <clipPath id="bajo-capital"><path d="{area_capital}"/></clipPath>
  </defs>
  <path d="{entre}" fill="var(--pos)" opacity="0.22" clip-path="url(#sobre-capital)"/>
  <path d="{entre}" fill="var(--neg)" opacity="0.22" clip-path="url(#bajo-capital)"/>
  <path d="{area_capital}" fill="var(--muted)" opacity="0.13"/>
  <path d="{linea_capital}" fill="none" stroke="var(--muted)" stroke-width="1.5"
   stroke-dasharray="5 4"/>
  <path d="{linea_valor}" fill="none" stroke="var(--accent)" stroke-width="2.2"
   stroke-linejoin="round"/>
  <text x="{pad_l}" y="{height - 6}" font-size="11"
   fill="var(--muted)">{value_points[0][0].isoformat()}</text>
  <text x="{width - pad_r}" y="{height - 6}" font-size="11" fill="var(--muted)"
   text-anchor="end">{value_points[-1][0].isoformat()}</text>
  <text x="{width - pad_r}" y="{y(final_valor) - 8:.1f}" font-size="12" font-weight="600"
   fill="var(--accent)" text-anchor="end">vale {final_valor:,.0f}</text>
  <text x="{width - pad_r}" y="{y(final_capital) + 16:.1f}" font-size="12"
   fill="var(--muted)" text-anchor="end">pusiste {final_capital:,.0f}</text>
</svg>"""








def card_sparkline(position, *, width: int = 260, height: int = 48) -> str:
    """El recorrido del precio desde tu primera compra, con tus operaciones.

    Va sobre la serie empalmada por canjes, y las marcas ya vienen llevadas a la
    misma escala: si no, un cambio de ratio dejaria los puntos fuera del grafico.
    """
    points = position.price_series
    if len(points) < 2:
        return ""
    values = [v for _, v in points]
    lo, hi = min(values), max(values)
    span = (hi - lo) or (hi or 1)
    pad = 5
    inner_w, inner_h = width - pad * 2, height - pad * 2
    first, last = points[0][0], points[-1][0]
    days = (last - first).days or 1

    def x(day: date) -> float:
        return pad + inner_w * min(max((day - first).days / days, 0.0), 1.0)

    def y(value: float) -> float:
        return pad + inner_h - ((value - lo) / span) * inner_h

    color = "var(--pos)" if (position.total_return_pct or 0) >= 0 else "var(--neg)"
    path = " ".join(
        f"{'M' if i == 0 else 'L'}{x(d):.1f},{y(v):.1f}" for i, (d, v) in enumerate(points)
    )
    area = f"{path} L{x(last):.1f},{height - pad:.1f} L{x(first):.1f},{height - pad:.1f} Z"
    marcas = "".join(
        f'<circle cx="{x(d):.1f}" cy="{y(v):.1f}" r="3" '
        f'fill="{"var(--pos)" if cat == "BUY" else "var(--neg)"}" '
        f'stroke="var(--panel)" stroke-width="1.5"/>'
        for d, v, cat in position.marks
        if first <= d <= last and lo <= v <= hi
    )
    return f"""<svg class="spark" viewBox="0 0 {width} {height}" role="img"
 aria-label="precio de {html.escape(position.ticker)} desde tu primera compra">
  <path d="{area}" fill="{color}" opacity="0.10"/>
  <path d="{path}" fill="none" stroke="{color}" stroke-width="1.6" stroke-linejoin="round"/>
  {marcas}
</svg>"""


def _instrument_cards(report: PortfolioReport) -> str:
    """Una tarjeta por especie, de la que mas rinde a la que menos."""
    ranked = sorted(
        (p for p in report.positions if p.total_return_pct is not None),
        key=lambda p: -p.total_return_pct,
    )
    if not ranked:
        return ""
    top = max((abs(p.total_return_pct) for p in ranked), default=0) or 1
    total = report.market_value or 1
    out = []
    for p in ranked:
        ret = p.total_return_pct
        ancho = min(abs(ret) / top, 1.0) * 50
        barra = (
            f'<i style="left:50%;width:{ancho:.1f}%;background:var(--pos)"></i>'
            if ret >= 0 else
            f'<i style="right:50%;width:{ancho:.1f}%;background:var(--neg)"></i>'
        )
        out.append(f"""<div class="inst">
  <div class="inst-top">
    <span class="inst-tk">{html.escape(p.ticker)}</span>
    <span class="inst-ret {_cls(ret)}">{_fmt_pct(ret)}</span>
  </div>
  <div class="inst-grp">{html.escape((p.description or '')[:34])}</div>
  <div class="inst-bar">{barra}</div>
  {card_sparkline(p)}
  <div class="inst-foot">
    <span>{_fmt_money(p.market_value_ars)} &middot; {_fmt_pct((p.market_value_ars or 0) / total)}</span>
    <span>{p.quantity:,.0f} nominales</span>
  </div>
  <div class="inst-foot" style="margin-top:.25rem">
    <span>{_num(p.total_return_ars, _fmt_money)}</span>
    <span>desde {p.first_buy.isoformat() if p.first_buy else '-'}</span>
  </div>
</div>""")
    return "\n".join(out)


def _allocator(report: PortfolioReport) -> str:
    """Escribis cuanta plata tenes y te dice a que le apuntas.

    Es HTML fijo: la tabla y los deslizadores los dibuja el JavaScript, porque
    el plan puede incluir especies que agregaste vos y no estan en la cartera.
    """
    posiciones = [p for p in report.positions if p.market_value_ars]
    if not posiciones:
        return ""
    sliders = "\n".join(
        f"""<div class="tune-row" data-ticker="{html.escape(p.ticker)}">
      <span>{html.escape(p.ticker)}</span>
      <input type="range" min="0" max="60" step="0.5" value="0"
             aria-label="objetivo de {html.escape(p.ticker)}">
      <output>0%</output>
    </div>"""
        for p in posiciones
    )
    return f"""<h2>Si tenes plata para poner</h2>
  <div class="panel">
    <div class="money-row">
      <label for="monto">Tengo</label>
      <input type="text" id="monto" inputmode="numeric" value="500.000"
             aria-label="monto a repartir">
      <button class="chip" data-amount="100000">$100 mil</button>
      <button class="chip" data-amount="500000">$500 mil</button>
      <button class="chip" data-amount="1000000">$1 millon</button>
    </div>
    <table>
      <thead><tr>
        <th>Especie</th><th>Tenes</th><th>Objetivo</th><th>Poner aca</th>
        <th>Nominales</th><th>Queda en</th>
      </tr></thead>
      <tbody id="reparto-body"></tbody>
      <tfoot><tr>
        <th colspan="3">Total</th><th id="reparto-total"></th><th></th><th></th>
      </tr></tfoot>
    </table>
    <details class="tune">
      <summary>Ajustar el objetivo &mdash; arrastra para cambiar cuanto queres que pese cada una</summary>
      <div class="money-row" style="margin:.8rem 0 0">
        <button class="chip" id="preset-actual">Como esta hoy</button>
        <button class="chip" id="preset-iguales">Partes iguales</button>
      </div>
      <div class="tune-grid">{sliders}</div>
    </details>
  </div>
  <p class="legend">Esto <b>no dice que instrumento va a subir</b>: reparte la plata nueva hacia
     lo que quedo por debajo de tu objetivo, comprando y sin vender nada. La columna de
     nominales es la accionable, porque compras papeles enteros.</p>
  <p class="legend">Lo que ajustes con los deslizadores queda guardado en este navegador.</p>"""


def _heatmap(report: PortfolioReport) -> str:
    """Grilla especie x mes, coloreada por rendimiento.

    Con ocho especies y ocho meses son sesenta y pico de numeros: leerlos uno
    por uno no es un vistazo. Con color se escanea de un saque y el mes en que
    algo se desplomo salta solo.
    """
    if not report.asset_months or not report.investing:
        return ""
    meses = report.investing.months[1:]
    if len(meses) < 2:
        return ""
    escala = max(
        (abs(v) for fila in report.asset_months for v in fila.returns if v is not None),
        default=0,
    ) or 1

    def celda(valor: float | None) -> str:
        if valor is None:
            return '<td class="na">&mdash;</td>'
        fuerza = min(abs(valor) / escala, 1.0)
        tono = "var(--pos)" if valor >= 0 else "var(--neg)"
        fondo = f"color-mix(in srgb, {tono} {fuerza * 72:.0f}%, transparent)"
        return f'<td style="background:{fondo}">{_fmt_pct(valor)}</td>'

    encabezado = "".join(f"<th>{m.month.strftime('%b')}</th>" for m in meses)
    filas = "\n".join(
        f'<tr><td class="tk">{html.escape(a.ticker)}</td>'
        + "".join(celda(v) for v in a.returns)
        + "</tr>"
        for a in report.asset_months
    )
    return f"""<h2>Cuando se movio cada una</h2>
  <div class="panel">
    <table class="heat">
      <thead><tr><th></th>{encabezado}</tr></thead>
      <tbody>{filas}</tbody>
    </table>
  </div>
  <p class="legend">Rendimiento de cada especie en cada mes. El total de una especie no
     distingue entre caer de a poco y desplomarse un mes puntual, y son cosas distintas.
     El mes en que compraste se mide desde el dia de la compra.</p>"""


def _monthly_rows(report: PortfolioReport) -> str:
    if not report.investing:
        return ""
    return "\n".join(
        f"""<tr>
  <td>{m.month.strftime('%Y-%m')}</td>
  <td>{_fmt_money(m.deposits) if m.deposits else '&mdash;'}</td>
  <td>{_fmt_money(m.contributed)}</td>
  <td>{_fmt_money(m.value)}</td>
  <td class="{_cls(m.gain)}">{_fmt_money(m.gain)}</td>
  <td class="{_cls(m.gain)}">{_fmt_pct(m.gain_pct)}</td>
  <td>{_fmt_money(m.value_usd, "US$") if m.value_usd else '&mdash;'}</td>
</tr>"""
        for m in report.investing.months
    )


def render_html(report: PortfolioReport) -> str:
    nav_ars = [(p.date, p.nav_ars) for p in report.nav_series if p.nav_ars]
    nav_usd = [(p.date, p.nav_usd) for p in report.nav_series if p.nav_usd]
    from .plan import load_targets
    objetivo = {t.ticker: t.weight * 100 for t in load_targets("objetivo.json")}
    instrumentos = _instrument_cards(report)
    allocator = _allocator(report)
    datos_json = json.dumps(
        [
            {
                "ticker": p.ticker,
                "value": round(p.market_value_ars or 0.0, 2),
                "price": round(p.price or 0.0, 2),
                "target": objetivo.get(p.ticker),
            }
            for p in report.positions
            if p.market_value_ars
        ],
        ensure_ascii=False,
    )
    periodo = report.investing
    ventana = [p for p in report.nav_series if periodo and p.date >= periodo.since]
    valor_points = [(p.date, p.nav_ars) for p in ventana]
    capital_points = (
        contributed_series(ventana, report.flows_by_day, periodo.opening) if periodo else []
    )
    avisos = list(report.warnings)
    if report.unclassified_events:
        avisos.append(
            f"{len(report.unclassified_events)} movimientos quedaron sin clasificar; "
            f"revisalos con <code>wallet-tracker movimientos --sin-clasificar</code>."
        )

    since = report.first_activity.isoformat() if report.first_activity else "—"
    usd_card = (
        f'<div class="card"><div class="k">Valuacion en USD</div>'
        f'<div class="v">{_fmt_money(report.total_value / report.ccl, "US$")}</div>'
        f'<div class="n">CCL {_fmt_money(report.ccl, "$", 2)}</div></div>'
        if report.ccl else ""
    )
    capital_card = (
        f'<div class="card"><div class="k">Capital aportado</div>'
        f'<div class="v">{_fmt_money(periodo.contributed)}</div>'
        f'<div class="n">{_fmt_money(periodo.deposits)} aportado'
        + (f' · {_fmt_money(periodo.monthly_average)} por mes' if periodo.monthly_average else '')
        + '</div></div>'
        if periodo else
        f'<div class="card"><div class="k">Aportes netos</div>'
        f'<div class="v">{_fmt_money(report.net_invested)}</div>'
        f'<div class="n">{_fmt_money(report.deposits)} aportado · '
        f'{_fmt_money(report.withdrawals)} retirado</div></div>'
    )
    ganancia_card = (
        f'<div class="card"><div class="k">Ganancia</div>'
        f'<div class="v {_cls(periodo.gain)}">{_fmt_money(periodo.gain)}</div>'
        f'<div class="n">{_fmt_pct(periodo.gain_pct)} sobre lo aportado</div></div>'
        if periodo else
        f'<div class="card"><div class="k">Ganancia total</div>'
        f'<div class="v {_cls(report.total_pnl)}">{_fmt_money(report.total_pnl)}</div>'
        f'<div class="n">{_fmt_pct(report.total_pnl_pct)} sobre lo aportado</div></div>'
    )
    crecimiento_section = (
        f"""<h2>Capital aportado y ganancia</h2>
  <div class="panel">{growth_chart(valor_points, capital_points)}</div>
  <p class="legend">La escalera punteada es tu plata: sube cada vez que aportas y se mantiene
     plana el resto del tiempo. La linea llena es lo que vale la cartera. Todo lo que queda
     <b style="color:var(--pos)">verde</b> por encima de la escalera es rendimiento; lo
     <b style="color:var(--neg)">rojo</b> por debajo es estar abajo de lo que pusiste.</p>

  <h2>Mes a mes</h2>
  <div class="panel">
    <table>
      <thead><tr>
        <th>Mes</th><th>Aportaste</th><th>Capital acumulado</th><th>Valor</th>
        <th>Ganancia</th><th>%</th><th>En USD</th>
      </tr></thead>
      <tbody>{_monthly_rows(report)}</tbody>
    </table>
  </div>
  <p class="legend">Lo que buscas es que la columna de ganancia crezca mas rapido que la de
     capital: eso es el interes compuesto trabajando sobre los aportes anteriores.</p>"""
        if periodo and len(periodo.months) > 1 else
        f'<h2>Evolucion de la cartera en pesos</h2>\n  <div class="panel">{line_chart(nav_ars, label="valuacion en pesos")}</div>'
    )
    sin_costo = report.untracked_value
    nota_valor = (
        f"{_fmt_money(report.market_value)} en especies + "
        f"{_fmt_money(report.cash)} en efectivo"
        if not sin_costo else
        f"{_fmt_money(report.market_value)} en especies + "
        f"{_fmt_money(report.cash)} en efectivo &middot; "
        f"incluye {_fmt_money(sin_costo)} sin historial de costo"
    )
    notas = what_to_watch(report)
    watch_section = (
        '<div class="watch"><span class="w-title">Que mirar hoy</span>'
        + "".join(
            f'<p><span class="dot {"good" if n.is_good else ("data" if n.kind == "dato" else "")}">'
            f"</span><span>{html.escape(n.text)}</span></p>"
            for n in notas
        )
        + "</div>"
        if notas else ""
    )
    avisos_section = (
        '<h2>Avisos</h2><div class="panel"><ul class="avisos">'
        + "".join(f"<li>{a}</li>" for a in avisos[:25])
        + "</ul></div>"
        if avisos else ""
    )
    mes_card = (
        f'<div class="card"><div class="k">Este mes</div>'
        f'<div class="v {_cls(periodo.last_month.month_return)}">'
        f'{_fmt_pct(periodo.last_month.month_return)}</div>'
        f'<div class="n">{periodo.green_months[0]} de {periodo.green_months[1]} meses en verde</div></div>'
        if (periodo and periodo.last_month and periodo.last_month.month_return is not None) else ""
    )
    caida_card = (
        f'<div class="card"><div class="k">Peor momento</div>'
        f'<div class="v {_cls(periodo.max_drawdown)}">{_fmt_pct(periodo.max_drawdown)}</div>'
        f'<div class="n">lo mas abajo que estuviste</div></div>'
        if periodo else ""
    )
    indice_card = (
        f'<div class="card"><div class="k">vs. {html.escape(periodo.benchmark.ticker)}</div>'
        f'<div class="v {_cls(periodo.benchmark.difference)}">'
        f'{periodo.benchmark.difference * 100:+,.1f} pts</div>'
        f'<div class="n">tu cartera {_fmt_pct(periodo.benchmark.portfolio)} &middot; '
        f'el indice {_fmt_pct(periodo.benchmark.ret)}</div></div>'
        if (periodo and periodo.benchmark) else ""
    )

    return f"""<title>Mi cartera PPI</title>
<style>{CSS}</style>
<div class="wrap">
  <h1>Cartera PPI · {report.as_of.isoformat()}</h1>
  <p class="sub">Historial desde {since} · {report.days_invested} dias ·
     {len(report.positions)} posiciones abiertas · {len(report.closed_trades)} operaciones cerradas</p>

  {watch_section}

  <div class="cards">
    <div class="card"><div class="k">Valuacion total</div>
      <div class="v">{_fmt_money(report.total_value)}</div>
      <div class="n">{nota_valor}</div></div>
    {usd_card}
    {capital_card}
    {ganancia_card}
    {mes_card}
    {caida_card}
    {indice_card}
  </div>

  <h2>Como viene cada instrumento</h2>
  <div class="insts">{instrumentos}</div>
  <p class="legend">Ordenadas de la que mas rinde a la que menos. El porcentaje es el retorno
     total: precio mas dividendos, menos costos. En el mini grafico, la linea es el precio
     desde tu primera compra y los puntos son tus operaciones:
     <b style="color:var(--pos)">&#9679;</b> compras &middot;
     <b style="color:var(--neg)">&#9679;</b> ventas.</p>

  {_heatmap(report)}

  {allocator}

  {crecimiento_section}

  <h2>Evolucion medida en dolares (CCL implicito)</h2>
  <div class="panel">{line_chart(nav_usd, color="var(--pos)", label="valuacion en dolares")}</div>
  <p class="legend">La misma cartera convertida al dolar implicito del dia. Es la vista que
     descuenta la devaluacion.</p>

  {avisos_section}

  <script>const DATOS = {datos_json};</script>
  <script>{ALLOCATOR_JS if allocator else ''}</script>

  <footer>
    Generado por wallet-tracker con datos de la API de Portfolio Personal Inversiones.
    Los precios provienen del historico de PPI y pueden diferir del cierre oficial.
    Es una herramienta de seguimiento personal, no asesoramiento de inversion.
  </footer>
</div>"""


def write_report(report: PortfolioReport, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_html(report), encoding="utf-8")
    return path
