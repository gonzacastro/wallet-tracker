"""Metricas financieras: TIR (XIRR), TWR, CAGR, volatilidad y drawdown.

Todo en Python puro para no arrastrar dependencias pesadas.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from typing import Iterable, Sequence

DAYS_IN_YEAR = 365.0



@dataclass(frozen=True)
class CashFlow:
    """Flujo desde la optica del inversor: negativo = plata que pusiste."""

    date: date
    amount: float


def npv(rate: float, flows: Sequence[CashFlow], base: date | None = None) -> float:
    base = base or flows[0].date
    total = 0.0
    for flow in flows:
        years = (flow.date - base).days / DAYS_IN_YEAR
        total += flow.amount / ((1.0 + rate) ** years)
    return total


def xirr(flows: Sequence[CashFlow], *, guess: float = 0.1) -> float | None:
    """Tasa interna de retorno con flujos en fechas irregulares (anualizada).

    Es la rentabilidad que tuvo *tu plata*: pondera cuanto pusiste y cuando.
    Devuelve None si los flujos no tienen solucion (ej. todos del mismo signo).
    """
    flows = sorted((f for f in flows if f.amount), key=lambda f: f.date)
    if len(flows) < 2:
        return None
    if not (any(f.amount > 0 for f in flows) and any(f.amount < 0 for f in flows)):
        return None

    base = flows[0].date

    # Newton-Raphson, rapido cuando converge.
    rate = guess
    for _ in range(60):
        try:
            value = npv(rate, flows, base)
            derivative = 0.0
            for flow in flows:
                years = (flow.date - base).days / DAYS_IN_YEAR
                if years:
                    derivative -= years * flow.amount / ((1.0 + rate) ** (years + 1))
            if abs(derivative) < 1e-12:
                break
            step = value / derivative
            new_rate = rate - step
            if new_rate <= -0.9999999:
                break
            if abs(new_rate - rate) < 1e-9:
                return new_rate
            rate = new_rate
        except (OverflowError, ZeroDivisionError, ValueError):
            break

    # Biseccion como red de seguridad: lenta pero no falla si hay cambio de signo.
    low, high = -0.9999, 100.0
    try:
        f_low, f_high = npv(low, flows, base), npv(high, flows, base)
    except (OverflowError, ValueError):
        return None
    if f_low * f_high > 0:
        return None
    for _ in range(400):
        mid = (low + high) / 2
        try:
            f_mid = npv(mid, flows, base)
        except (OverflowError, ValueError):
            return None
        if abs(f_mid) < 1e-9:
            return mid
        if f_low * f_mid < 0:
            high, f_high = mid, f_mid
        else:
            low, f_low = mid, f_mid
    return (low + high) / 2


def cagr(start_value: float, end_value: float, days: int) -> float | None:
    """Tasa anual equivalente entre dos valores."""
    if start_value <= 0 or end_value <= 0 or days <= 0:
        return None
    return (end_value / start_value) ** (DAYS_IN_YEAR / days) - 1.0


def annualize(total_return: float, days: int) -> float | None:
    """Anualiza un retorno acumulado (0.35 = +35%)."""
    if days <= 0 or total_return <= -1:
        return None
    return (1.0 + total_return) ** (DAYS_IN_YEAR / days) - 1.0


def period_returns(
    nav_series: Sequence[tuple[date, float]], flows_by_date: dict[date, float] | None = None
) -> list[tuple[date, float]]:
    """Retorno de cada periodo, descontando el aporte o retiro de ese dia.

    Es la base de todo lo que mide rendimiento neutro a los flujos: TWR,
    volatilidad y peor caida salen de aca.

    El aporte del dia se suma a la base y el retiro se le devuelve al cierre.
    Eso es lo que pasa de verdad -- la plata que entra se puede invertir ese
    mismo dia, la que sale se va de lo que ya habia -- y ademas es lo unico
    robusto cuando el flujo es mucho mas grande que la cartera: descontar el
    aporte del cierre y dividir por la base chica amplifica cualquier
    diferencia. Un dia que entraron $6.000.000 sobre una cartera de $1.348.174,
    el 0,75% que se fue en comisiones se leia como una caida del 4,07%.

    Un periodo sin base positiva no aporta informacion y se saltea: sin eso,
    meter plata en una cuenta vacia se leeria como un rendimiento infinito.

    De ahi sale, gratis, la garantia que necesita la cadena del TWR: como base
    y cierre son positivos en todo periodo que sobrevive al filtro,
    `cierre/base - 1` es siempre mayor que -100%. Ningun dia puede dar vuelta
    el signo del producto acumulado, que es de donde salian los TWR de -122% o
    de millones por ciento.
    """
    flows_by_date = flows_by_date or {}
    clean = [(d, v) for d, v in nav_series if v is not None]
    out: list[tuple[date, float]] = []
    if len(clean) < 2:
        return out
    previous = clean[0][1]
    for day, value in clean[1:]:
        flow = flows_by_date.get(day, 0.0)
        base = previous + max(flow, 0.0)
        close = value - min(flow, 0.0)
        if base <= 0 or close <= 0:
            previous = value
            continue
        out.append((day, close / base - 1.0))
        previous = value
    return out


def return_index(
    nav_series: Sequence[tuple[date, float]], flows_by_date: dict[date, float] | None = None
) -> list[tuple[date, float]]:
    """Valor acumulado de un peso invertido al inicio, neutro a aportes y retiros.

    Sobre esta serie -- y no sobre la valuacion -- hay que medir la peor caida:
    en la valuacion cruda, retirar tu propia plata se lee como una perdida del
    100%, que es justo lo que no queres medir.
    """
    clean = [(d, v) for d, v in nav_series if v is not None]
    if not clean:
        return []
    index = [(clean[0][0], 1.0)]
    value = 1.0
    for day, ret in period_returns(nav_series, flows_by_date):
        value *= 1.0 + ret
        index.append((day, value))
    return index


def twr(nav_series: Sequence[tuple[date, float]], flows_by_date: dict[date, float]) -> float | None:
    """Time-Weighted Return: rendimiento de la *estrategia*, neutro a aportes.

    `nav_series` son valuaciones diarias de la cartera y `flows_by_date` los
    aportes/retiros externos de cada dia (positivo = aporte). Se descuenta el
    flujo del dia para que meter plata no se lea como ganancia.
    """
    clean = [(d, v) for d, v in nav_series if v is not None]
    if len(clean) < 2:
        return None
    factor = 1.0
    for _, ret in period_returns(nav_series, flows_by_date):
        factor *= 1.0 + ret
    return factor - 1.0


def daily_returns(nav_series: Sequence[tuple[date, float]], flows_by_date: dict[date, float] | None = None) -> list[float]:
    return [r for _, r in period_returns(nav_series, flows_by_date)]


def volatility(returns: Sequence[float], *, annualized: bool = True, periods: int = 252) -> float | None:
    """Desvio estandar de los retornos diarios."""
    if len(returns) < 2:
        return None
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    sigma = math.sqrt(variance)
    return sigma * math.sqrt(periods) if annualized else sigma


def max_drawdown(nav_series: Sequence[tuple[date, float]]) -> tuple[float, date | None, date | None]:
    """Peor caida desde un maximo. Devuelve (drawdown, fecha_pico, fecha_piso)."""
    peak = float("-inf")
    peak_date: date | None = None
    worst = 0.0
    worst_peak: date | None = None
    worst_trough: date | None = None
    for day, value in nav_series:
        if value is None or value <= 0:
            continue
        if value > peak:
            peak, peak_date = value, day
        drawdown = value / peak - 1.0
        if drawdown < worst:
            worst, worst_peak, worst_trough = drawdown, peak_date, day
    return worst, worst_peak, worst_trough


def flows_from_events(events: Iterable, *, terminal_value: float | None = None,
                      terminal_date: date | None = None) -> list[CashFlow]:
    """Arma los flujos para la TIR de la cartera completa.

    Solo cuentan los movimientos externos (aportes y retiros); todo lo que pasa
    dentro de la cuenta (compras, ventas, dividendos) es reinversion interna.
    """
    flows = [
        CashFlow(event.date, -event.cash_flow)
        for event in events
        if getattr(event, "is_external_flow", False)
    ]
    if terminal_value is not None and terminal_date is not None:
        flows.append(CashFlow(terminal_date, terminal_value))
    return flows
