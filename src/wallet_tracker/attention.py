"""Que mirar hoy: lo unico que hace falta leer al abrir el reporte.

El panel tiene decenas de numeros y casi ningun dia cambian todos. Este modulo
revisa un puñado de condiciones y devuelve las pocas que ameritan que hagas
algo, ordenadas por importancia. Si no hay nada, no dice nada -- que es la
respuesta correcta la mayoria de los dias.

La regla para agregar una condicion nueva: tiene que corresponder a una accion
concreta o a un hecho que cambio. "Tu cartera vale $9 millones" no va: eso ya
esta en la tarjeta de arriba y no cambia nada.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from .analysis import PortfolioReport

#: Meses sin aportar a partir de los cuales vale la pena mencionarlo.
STALE_CONTRIBUTION_MONTHS = 2

#: Caida a partir de la cual una especie merece que la mires.
LAGGARD_THRESHOLD = -0.15

#: Efectivo ocioso: minimo como fraccion de la cartera para mencionarlo.
IDLE_CASH_SHARE = 0.03

#: Cuantas cosas por hacer mostrar como maximo. Mas que esto deja de ser un
#: vistazo. La buena noticia, si la hay, va aparte: no compite por el lugar.
MAX_NOTES = 3


@dataclass(frozen=True)
class Note:
    """Una cosa que mirar, con su prioridad."""

    text: str
    kind: str          # "dato" mal, "accion" pendiente, "logro" alcanzado
    priority: int      # menor primero

    @property
    def is_good(self) -> bool:
        return self.kind == "logro"


def _months_since_last_contribution(report: "PortfolioReport") -> int | None:
    period = report.investing
    if not period or not period.months:
        return None
    con_aporte = [i for i, m in enumerate(period.months) if m.deposits > 0]
    if not con_aporte:
        return None
    return len(period.months) - 1 - con_aporte[-1]


def what_to_watch(report: "PortfolioReport") -> list[Note]:
    """Las pocas cosas que ameritan atencion, de la mas a la menos urgente."""
    notes: list[Note] = []
    period = report.investing

    # 1. Los numeros no cierran contra el broker: nada de lo demas es confiable.
    #    Se saltean las especies que ya se sabe que no tienen historial de compra:
    #    para esas hay un aviso mas preciso mas abajo y decirlo dos veces sobra.
    sin_historial = {p.ticker for p in report.positions if p.cost_unknown}
    for warning in report.warnings:
        if "el broker informa" in warning:
            ticker = warning.split(":")[0]
            if ticker in sin_historial:
                continue
            notes.append(Note(
                f"{ticker} no concilia con lo que informa el broker: revisa el aviso de abajo",
                "dato", 0,
            ))

    # 2. Precios viejos: la valuacion de esas especies no es de hoy.
    stale = [p.ticker for p in report.positions if p.stale_price]
    if stale:
        notes.append(Note(
            f"Precio desactualizado en {', '.join(stale)}: la valuacion puede no ser de hoy",
            "dato", 1,
        ))

    # 3. Hace rato que no aportas. Si el plan es aportar seguido, esto es *la*
    #    accion pendiente, y es la que mas facil se pasa por alto.
    meses = _months_since_last_contribution(report)
    if meses is not None and meses >= STALE_CONTRIBUTION_MONTHS:
        notes.append(Note(
            f"Hace {meses} meses que no aportas",
            "accion", 2,
        ))

    # 4. La especie que viene mal, si viene bastante mal.
    rezagadas = [
        p for p in report.positions
        if p.total_return_pct is not None and p.total_return_pct <= LAGGARD_THRESHOLD
    ]
    if rezagadas:
        peor = min(rezagadas, key=lambda p: p.total_return_pct)
        extra = f" (y {len(rezagadas) - 1} mas)" if len(rezagadas) > 1 else ""
        notes.append(Note(
            f"{peor.ticker} viene {peor.total_return_pct * 100:,.1f}%{extra}",
            "accion", 3,
        ))

    # 5. Plata parada en la cuenta que podria estar comprando algo.
    if report.market_value and report.cash > 0:
        share = report.cash / (report.market_value + report.cash)
        precios = [p.price for p in report.positions if p.price]
        if share >= IDLE_CASH_SHARE and precios and report.cash >= min(precios):
            notes.append(Note(
                f"Tenes ${report.cash:,.0f} sin invertir ({share * 100:,.1f}% de la cartera)",
                "accion", 4,
            ))

    # 6. Tenencias que el broker informa y el historial no explica: el valor
    #    esta bien, pero no se puede calcular cuanto ganaste con ellas.
    sin_costo = [p.ticker for p in report.positions if p.cost_unknown]
    if sin_costo:
        notes.append(Note(
            f"Sin historial de compra en {', '.join(sin_costo)}: se ve el valor, no la ganancia",
            "dato", 1,
        ))

    # 7. Novedades buenas: que la cartera este en su mejor momento, o que acabe
    #    de cruzar a ganancia, es lo unico "positivo" que amerita un renglon.
    if period and period.months:
        usd = [m.value_usd for m in period.months if m.value_usd]
        if len(usd) > 1 and usd[-1] == max(usd):
            notes.append(Note("Estas en tu mejor momento medido en dolares", "logro", 5))
        elif len(period.months) > 1:
            hoy, antes = period.months[-1], period.months[-2]
            if hoy.gain > 0 >= antes.gain:
                notes.append(Note("Cruzaste a ganancia este mes", "logro", 5))

    notes.sort(key=lambda n: n.priority)
    pendientes = [n for n in notes if not n.is_good][:MAX_NOTES]
    logros = [n for n in notes if n.is_good][:1]
    return pendientes + logros
