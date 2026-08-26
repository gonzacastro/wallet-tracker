"""Clasificacion de movimientos de cuenta en eventos economicos tipados.

PPI no devuelve un campo "tipo de movimiento": solo una descripcion en texto.
Aca traducimos esa descripcion (mas ticker/cantidad/importe) a categorias con
las que se puede calcular costo, resultado y flujos de fondos.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Sequence

# --------------------------------------------------------------- categorias
BUY = "BUY"
SELL = "SELL"
DIVIDEND = "DIVIDEND"
COUPON = "COUPON"                # renta de bonos
AMORTIZATION = "AMORTIZATION"    # devolucion de capital
FEE = "FEE"
TAX = "TAX"
DEPOSIT = "DEPOSIT"              # ingreso de dinero desde afuera
WITHDRAWAL = "WITHDRAWAL"        # egreso de dinero hacia afuera
INTEREST = "INTEREST"            # cauciones, plazos fijos, remuneracion de saldos
ADJUSTMENT = "ADJUSTMENT"        # canjes, splits, ajustes tecnicos
FX_CONVERSION = "FX_CONVERSION"  # pata de una compra de dolar MEP/CCL (ver conversions.py)
RATIO_CHANGE = "RATIO_CHANGE"    # cambio de ratio declarado a mano (ver corporate.py)
OPENING = "OPENING"              # tenencia que ya tenias antes del historial
OTHER = "OTHER"

#: PPI devuelve este literal cuando no supo resolver la especie de un
#: movimiento. Es un centinela, no un ticker: se trata como campo vacio.
TICKER_SENTINELS = frozenset({"TICKER NOT FOUND", "NOT FOUND", "-"})

#: Categorias que representan plata entrando o saliendo del bolsillo del cliente.
#: Son las unicas que cuentan como "flujo externo" para TIR y TWR.
EXTERNAL_FLOWS = frozenset({DEPOSIT, WITHDRAWAL})

#: Categorias que son ingreso generado por la cartera (no aporte de capital).
INCOME = frozenset({DIVIDEND, COUPON, AMORTIZATION, INTEREST})

#: Categorias que mueven plata pero no generan ni consumen posicion. El motor
#: FIFO las saltea antes de tocar cualquier tenencia.
NON_POSITION = frozenset({FX_CONVERSION})

#: Reglas por defecto: (regex sobre la descripcion normalizada, categoria).
#: Se evaluan en orden; la primera que matchea gana.
DEFAULT_RULES: tuple[tuple[str, str], ...] = (
    # Comisiones e impuestos van PRIMERO: sus descripciones suelen incluir la
    # palabra "compra"/"venta" ("derechos de mercado s/compra GGAL") y si no se
    # atajan aca terminarian contando como operaciones.
    (r"\biva\b|i\.v\.a|impuesto|retencion|percepcion|ley 25413|ley 27430|"
     r"debitos y creditos|ganancias|ingresos brutos|sellos", TAX),
    (r"comision|arancel|derecho de mercado|derechos de mercado|gastos|"
     r"custodia|mantenimiento", FEE),
    (r"dividendo", DIVIDEND),
    (r"\brenta\b|\bcupon\b|pago de interes", COUPON),
    (r"amortizacion|devolucion de capital", AMORTIZATION),
    (r"\bcompra\b|\bsuscripcion\b|\bsuscrip\b", BUY),
    (r"\bventa\b|\brescate\b", SELL),
    (r"caucion|plazo fijo|interes|remuneracion", INTEREST),
    (r"transferencia recibida|deposito|acreditacion de fondos|ingreso de fondos|"
     r"credito por transferencia|recepcion de valores", DEPOSIT),
    (r"transferencia enviada|extraccion|retiro|egreso de fondos|"
     r"debito por transferencia|envio de fondos", WITHDRAWAL),
    (r"canje|split|ajuste|rescision|reversion|escision", ADJUSTMENT),
    (r"tenencia inicial", OPENING),
)


#: PPI nombra la especie al final de la descripcion cuando no la manda en el
#: campo `ticker`: "Dividendo en efectivo / JPM". Sin esto los dividendos caen
#: en una bolsa comun y ninguna especie figura cobrando lo que le pagaron.
TICKER_IN_DESCRIPTION = re.compile(r"/\s*([A-Z][A-Z0-9.]{0,11})\s*$")


def ticker_from_description(description: str | None) -> str | None:
    match = TICKER_IN_DESCRIPTION.search((description or "").strip())
    return match.group(1) if match else None


def clean_ticker(value: str | None) -> str | None:
    """Ticker normalizado, o `None` si PPI no supo resolverlo.

    PPI manda el literal "TICKER NOT FOUND" en los movimientos que no pudo
    asociar a una especie (dividendos en efectivo, aranceles, ingresos de
    fondos). Si se toma como si fuera un ticker aparecen posiciones fantasma
    con ese nombre y los importes se atribuyen a una especie inexistente.
    """
    ticker = (value or "").strip().upper()
    return None if (not ticker or ticker in TICKER_SENTINELS) else ticker


def normalize_text(text: str | None) -> str:
    """Minusculas y sin acentos: las descripciones de PPI mezclan ambos."""
    if not text:
        return ""
    stripped = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in stripped if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", stripped).strip().lower()


@dataclass(frozen=True)
class Event:
    """Un movimiento ya interpretado."""

    uid: str
    date: date
    category: str
    description: str
    currency: str
    cash_flow: float          # efecto en el saldo de la cuenta (negativo = sale plata)
    ticker: str | None = None
    quantity: float = 0.0
    price: float = 0.0
    balance: float = 0.0      # saldo de la cuenta informado por PPI tras el movimiento
    ordinal: int = 0          # orden dentro del dia, como lo devuelve la API
    ratio: float = 0.0        # solo en RATIO_CHANGE: nominales nuevos por cada viejo
    matched_rule: str | None = None

    @property
    def is_trade(self) -> bool:
        return self.category in (BUY, SELL)

    @property
    def is_fx_conversion(self) -> bool:
        return self.category == FX_CONVERSION

    @property
    def is_external_flow(self) -> bool:
        return self.category in EXTERNAL_FLOWS

    @property
    def gross(self) -> float:
        """Importe absoluto del movimiento."""
        return abs(self.cash_flow)


@dataclass
class Rules:
    patterns: list[tuple[re.Pattern[str], str]] = field(default_factory=list)

    @classmethod
    def default(cls) -> "Rules":
        return cls([(re.compile(p), c) for p, c in DEFAULT_RULES])

    @classmethod
    def load(cls, path: str | Path | None) -> "Rules":
        """Carga reglas extra desde JSON: [{"pattern": "...", "category": "FEE"}].

        Las reglas del usuario se evaluan ANTES que las default, asi se pueden
        corregir clasificaciones sin tocar el codigo.
        """
        rules = cls.default()
        if not path:
            return rules
        file = Path(path)
        if not file.exists():
            return rules
        data = json.loads(file.read_text(encoding="utf-8"))
        custom = [
            (re.compile(normalize_text(item["pattern"])), item["category"].upper())
            for item in data
            if item.get("pattern") and item.get("category")
        ]
        rules.patterns = custom + rules.patterns
        return rules

    def match(self, description: str) -> tuple[str | None, str | None]:
        text = normalize_text(description)
        for pattern, category in self.patterns:
            if pattern.search(text):
                return category, pattern.pattern
        return None, None


def classify(row: dict[str, Any] | Any, rules: Rules | None = None) -> Event:
    """Traduce una fila de `movements` a un Event."""
    rules = rules or Rules.default()
    get = row.get if isinstance(row, dict) else (lambda k, d=None: row[k] if k in row.keys() else d)

    description = get("description") or ""
    ticker = clean_ticker(get("ticker"))
    quantity = float(get("quantity") or 0.0)
    amount = float(get("amount") or 0.0)
    price = float(get("price") or 0.0)
    category, matched = rules.match(description)

    # Un movimiento con ticker y cantidad es, salvo prueba en contrario, una
    # operacion de especie. El signo del importe define el lado: si sale plata
    # de la cuenta, compraste.
    if ticker and quantity and category not in (
        DIVIDEND, COUPON, AMORTIZATION, ADJUSTMENT, OPENING
    ):
        if category not in (BUY, SELL):
            category = BUY if amount < 0 else SELL
            matched = matched or "signo del importe"
        # La descripcion manda, pero si contradice al signo del importe gana el signo.
        elif (category == BUY and amount > 0) or (category == SELL and amount < 0):
            category = BUY if amount < 0 else SELL
            matched = "signo del importe (corrige descripcion)"

    # Sin ticker y sin cantidad no hay operacion posible, por mas que la
    # descripcion diga "compra": lo dejamos sin clasificar para revisarlo.
    if category in (BUY, SELL) and not (ticker and quantity):
        category, matched = OTHER, None

    if category is None:
        category = OTHER

    # Un dividendo o una renta sin especie: PPI la nombra en la descripcion.
    if not ticker and category in INCOME:
        ticker = ticker_from_description(description)

    raw_date = get("agreement_date") or get("settlement_date")
    event_date = date.fromisoformat(raw_date) if isinstance(raw_date, str) else raw_date

    return Event(
        uid=str(get("uid") or ""),
        date=event_date,
        category=category,
        description=description,
        currency=(get("currency") or "ARS"),
        cash_flow=amount,
        ticker=ticker,
        quantity=abs(quantity),
        price=price,
        balance=float(get("balance") or 0.0),
        ordinal=int(get("ordinal") or 0),
        matched_rule=matched,
    )


def build_ledger(rows: Iterable[dict[str, Any] | Any], rules: Rules | None = None) -> list[Event]:
    rules = rules or Rules.default()
    events = [classify(row, rules) for row in rows]
    events.sort(key=lambda e: (e.date, e.category != BUY))
    return events


def unclassified(events: Sequence[Event]) -> list[Event]:
    """Movimientos que ninguna regla supo interpretar: sirven para afinar reglas."""
    return [e for e in events if e.category == OTHER]
