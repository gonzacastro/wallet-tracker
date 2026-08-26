"""Wrapper delgado sobre el SDK oficial `ppi-client`.

Agrega lo que el SDK no trae y este proyecto necesita: reintentos, troceo de
rangos de fechas largos, deteccion automatica de tipo de instrumento/plazo y
normalizacion de las respuestas a filas listas para SQLite.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Callable, Iterable, Sequence

from .config import Settings
from .db import dumps
from .ledger import clean_ticker

# Plazos que probamos al pedir precios cuando no sabemos cual acepta el ticker.
SETTLEMENTS = ("A-24HS", "INMEDIATA", "A-48HS", "A-72HS")

# Tipos de instrumento validos segun /Configuration/InstrumentTypes.
INSTRUMENT_TYPES = (
    "ACCIONES", "CEDEARS", "BONOS", "LETRAS", "ON", "FCI", "ETF",
    "ACCIONES-USA", "OPCIONES", "FUTUROS", "CAUCIONES", "NOBAC", "LEBAC",
    "FCI-EXTERIOR",
)


class PPIError(RuntimeError):
    pass


def _retry(fn: Callable[[], Any], *, attempts: int = 3, base_delay: float = 1.5) -> Any:
    """Reintenta con backoff. La API corta conexiones seguido en horario pico."""
    last: Exception | None = None
    for i in range(attempts):
        try:
            return fn()
        except Exception as exc:  # el SDK levanta Exception plano
            last = exc
            if i == attempts - 1:
                break
            time.sleep(base_delay * (2**i))
    raise PPIError(str(last)) from last


def _chunks(start: date, end: date, days: int = 365) -> Iterable[tuple[date, date]]:
    """Trocea un rango largo: la API limita la ventana de consulta."""
    cur = start
    while cur <= end:
        stop = min(cur + timedelta(days=days - 1), end)
        yield cur, stop
        cur = stop + timedelta(days=1)


def _as_dt(d: date | datetime) -> datetime:
    return d if isinstance(d, datetime) else datetime(d.year, d.month, d.day)


def _num(value: Any) -> float:
    if value in (None, ""):
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _iso(value: Any) -> str | None:
    """Normaliza fechas de la API (ISO con o sin zona) a 'YYYY-MM-DD'."""
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip().rstrip("Z")
    try:
        return datetime.fromisoformat(text).date().isoformat()
    except ValueError:
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return text[:10] if len(text) >= 10 else None


def movement_uid(account: str, mov: dict[str, Any], seq: int) -> str:
    """Hash estable por movimiento (la API no devuelve id propio).

    `seq` distingue movimientos identicos del mismo dia (ej. dos compras iguales).
    Se calcula por dia, no por lote, para que sea estable entre sincronizaciones.
    """
    parts = [
        account,
        _iso(mov.get("agreementDate")) or "",
        _iso(mov.get("settlementDate")) or "",
        str(mov.get("currency") or ""),
        f"{_num(mov.get('amount')):.6f}",
        f"{_num(mov.get('price')):.6f}",
        str(mov.get("description") or "").strip(),
        str(mov.get("ticker") or "").strip().upper(),
        f"{_num(mov.get('quantity')):.6f}",
        str(seq),
    ]
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()


@dataclass
class InstrumentInfo:
    ticker: str
    description: str = ""
    currency: str = ""
    type: str = ""
    market: str = ""


class PPISession:
    """Sesion autenticada contra la API de PPI."""

    def __init__(self, settings: Settings, *, throttle: float = 0.25) -> None:
        self.settings = settings
        self.throttle = throttle
        self._ppi: Any = None
        self._settlement_cache: dict[str, str] = {}

    # ---------------------------------------------------------------- login
    def login(self) -> "PPISession":
        if self._ppi is not None:
            return self
        if not self.settings.has_credentials:
            raise PPIError(
                "Faltan credenciales. Completa PPI_PUBLIC_KEY y PPI_PRIVATE_KEY en el .env "
                "(se generan en tu cuenta PPI, pestana 'Gestiones' -> servicio API)."
            )
        from ppi_client.ppi import PPI  # import diferido: acelera el arranque del CLI

        ppi = PPI(sandbox=self.settings.sandbox)
        _retry(lambda: ppi.account.login_api(self.settings.public_key, self.settings.private_key))
        self._ppi = ppi
        return self

    @property
    def api(self) -> Any:
        if self._ppi is None:
            self.login()
        return self._ppi

    def _pause(self) -> None:
        if self.throttle:
            time.sleep(self.throttle)

    # -------------------------------------------------------------- cuentas
    def accounts(self) -> list[dict[str, Any]]:
        return _retry(lambda: self.api.account.get_accounts()) or []

    def resolve_account_number(self) -> str:
        if self.settings.account_number:
            return self.settings.account_number
        accounts = self.accounts()
        if not accounts:
            raise PPIError("La API no devolvio ninguna cuenta comitente asociada.")
        if len(accounts) > 1:
            nums = ", ".join(str(a.get("accountNumber")) for a in accounts)
            raise PPIError(
                f"Tenes mas de una cuenta ({nums}). Defini PPI_ACCOUNT_NUMBER en el .env."
            )
        return str(accounts[0]["accountNumber"])

    # ----------------------------------------------------------- movimientos
    def movements(self, account: str, start: date, end: date) -> list[dict[str, Any]]:
        """Movimientos entre fechas, troceando el rango en ventanas de un ano."""
        from ppi_client.models.account_movements import AccountMovements

        out: list[dict[str, Any]] = []
        for chunk_start, chunk_end in _chunks(start, end):
            params = AccountMovements(account, _as_dt(chunk_start), _as_dt(chunk_end), None)
            data = _retry(lambda p=params: self.api.account.get_movements(p)) or []
            out.extend(data)
            self._pause()
        return out

    def orders(self, account: str, start: date, end: date) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for chunk_start, chunk_end in _chunks(start, end, days=90):
            data = _retry(
                lambda a=chunk_start, b=chunk_end: self.api.orders.get_orders(
                    account, date_from=_as_dt(a), date_to=_as_dt(b)
                )
            ) or []
            out.extend(data)
            self._pause()
        return out

    def balances_and_positions(self, account: str) -> dict[str, Any]:
        return _retry(lambda: self.api.account.get_balance_and_positions(account)) or {}

    def available_balance(self, account: str) -> list[dict[str, Any]]:
        return _retry(lambda: self.api.account.get_available_balance(account)) or []

    # -------------------------------------------------------- market data
    def instrument_info(self, ticker: str) -> InstrumentInfo | None:
        data = _retry(lambda: self.api.marketdata.search_instrument(ticker, "", "", "")) or []
        self._pause()
        exact = [i for i in data if str(i.get("ticker", "")).upper() == ticker.upper()]
        chosen = (exact or data or [None])[0]
        if not chosen:
            return None
        return InstrumentInfo(
            ticker=str(chosen.get("ticker", ticker)).upper(),
            description=str(chosen.get("description") or ""),
            currency=str(chosen.get("currency") or ""),
            type=str(chosen.get("type") or ""),
            market=str(chosen.get("market") or ""),
        )

    def price_history(
        self,
        ticker: str,
        instrument_type: str,
        start: date,
        end: date,
        settlement: str | None = None,
    ) -> list[dict[str, Any]]:
        """Serie historica diaria. Prueba plazos hasta encontrar uno con datos.

        Si el instrumento ya no cotiza (bono amortizado, especie vieja) reintenta
        con `search_skip_filter`, que incluye instrumentos vencidos.
        """
        candidates: Sequence[str] = (
            (settlement,) if settlement else (self._settlement_cache.get(ticker), *SETTLEMENTS)
        )
        seen: set[str] = set()
        for stl in candidates:
            if not stl or stl in seen:
                continue
            seen.add(stl)
            for method in ("search", "search_skip_filter"):
                fn = getattr(self.api.marketdata, method, None)
                if fn is None:
                    continue
                try:
                    data = _retry(
                        lambda f=fn, s=stl: f(
                            ticker, instrument_type, s,
                            start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"),
                        ),
                        attempts=2,
                    )
                except PPIError:
                    data = None
                self._pause()
                if data:
                    self._settlement_cache[ticker] = stl
                    return [
                        {
                            "ticker": ticker,
                            "date": _iso(d.get("date")),
                            "settlement": stl,
                            "price": _num(d.get("price")),
                            "volume": _num(d.get("volume")),
                            "opening": _num(d.get("openingPrice")),
                            "max": _num(d.get("max")),
                            "min": _num(d.get("min")),
                        }
                        for d in data
                        if _iso(d.get("date"))
                    ]
        return []

    def current_price(self, ticker: str, instrument_type: str, settlement: str | None = None) -> float | None:
        for stl in ((settlement,) if settlement else (self._settlement_cache.get(ticker), *SETTLEMENTS)):
            if not stl:
                continue
            try:
                data = _retry(
                    lambda s=stl: self.api.marketdata.current(ticker, instrument_type, s), attempts=2
                )
            except PPIError:
                continue
            finally:
                self._pause()
            if data and _num(data.get("price")):
                self._settlement_cache[ticker] = stl
                return _num(data.get("price"))
        return None


# ------------------------------------------------------------ normalizadores

def normalize_movements(account: str, raw: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convierte movimientos crudos en filas de la tabla `movements`."""
    per_day: dict[tuple[str, str], int] = {}
    ordinals: dict[str, int] = {}
    rows: list[dict[str, Any]] = []
    for mov in raw:
        day = _iso(mov.get("agreementDate")) or _iso(mov.get("settlementDate")) or ""
        fingerprint = (
            f"{day}|{mov.get('description')}|{mov.get('ticker')}|"
            f"{_num(mov.get('amount')):.6f}|{_num(mov.get('quantity')):.6f}"
        )
        key = (day, fingerprint)
        seq = per_day.get(key, 0)
        per_day[key] = seq + 1
        # Orden dentro del dia tal como lo devuelve la API: define cual es el
        # saldo de cierre de la jornada.
        ordinal = ordinals.get(day, 0)
        ordinals[day] = ordinal + 1
        rows.append(
            {
                "uid": movement_uid(account, mov, seq),
                "account_number": account,
                "agreement_date": day,
                "settlement_date": _iso(mov.get("settlementDate")),
                "currency": (mov.get("currency") or "").strip() or None,
                "amount": _num(mov.get("amount")),
                "price": _num(mov.get("price")),
                "description": (mov.get("description") or "").strip(),
                # "TICKER NOT FOUND" es el centinela de PPI, no una especie.
                "ticker": clean_ticker(mov.get("ticker")),
                "quantity": _num(mov.get("quantity")),
                "balance": _num(mov.get("balance")),
                "ordinal": ordinal,
                "raw": dumps(mov),
            }
        )
    return rows


def normalize_orders(account: str, raw: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "order_id": int(o.get("id") or 0),
            "account_number": account,
            "instrument_type": o.get("instrumentType"),
            "operation": o.get("operation"),
            "ticker": clean_ticker(o.get("ticker")),
            "status": o.get("status"),
            "date": _iso(o.get("date")),
            "settlement": o.get("settlement"),
            "quantity": _num(o.get("quantity")),
            "order_type": o.get("orderType"),
            "operation_type": o.get("operationType"),
            "price": _num(o.get("price")),
            "currency": o.get("currency"),
            "amount": _num(o.get("amount")),
            "external_id": o.get("externalID"),
            "raw": dumps(o),
        }
        for o in raw
        if o.get("id")
    ]


def normalize_snapshot(account: str, payload: dict[str, Any], ts: str) -> list[dict[str, Any]]:
    """Aplana /BalancesAndPositions a filas de la tabla `snapshots`."""
    rows: list[dict[str, Any]] = []
    for group in payload.get("groupedAvailability") or []:
        for item in group.get("availability") or []:
            rows.append(
                {
                    "ts": ts,
                    "account_number": account,
                    "kind": "cash",
                    "group_name": group.get("currency") or item.get("name"),
                    "ticker": None,
                    "currency": item.get("name") or group.get("currency"),
                    "settlement": item.get("settlement"),
                    "quantity": None,
                    "price": None,
                    "amount": _num(item.get("amount")),
                    "raw": dumps(item),
                }
            )
    for group in payload.get("groupedInstruments") or []:
        for item in group.get("instruments") or []:
            rows.append(
                {
                    "ts": ts,
                    "account_number": account,
                    "kind": "instrument",
                    "group_name": group.get("name"),
                    "ticker": ((item.get("ticker") or "").strip().upper() or None),
                    "currency": item.get("currency"),
                    "settlement": item.get("settlement"),
                    "quantity": _num(item.get("quantity")),
                    "price": _num(item.get("price")),
                    "amount": _num(item.get("amount")),
                    "raw": dumps(item),
                }
            )
    return rows
