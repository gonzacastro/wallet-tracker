"""Cliente de Binance: saldos, operaciones y precios.

Se usa la API publica de Binance directo por HTTP, sin SDK: son tres endpoints
y firmarlos es un HMAC. Asi no se agrega una dependencia al proyecto.

El historial de precios (`klines`) es **publico**: no hace falta ninguna clave.
Solo saber cuanto tenes y que operaste requiere autenticacion, y con permisos
de *solo lectura*: la herramienta nunca opera ni retira.

Los saldos en stablecoins se tratan como efectivo en dolares, no como una
tenencia: un USDT parado en la cuenta es plata esperando, igual que los pesos
en la cuenta de PPI.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Iterator

from .db import dumps

BASE_URL = "https://api.binance.com"

#: Monedas que son efectivo, no inversion. Un USDT no "cotiza": es un dolar.
STABLECOINS = frozenset({"USDT", "USDC", "BUSD", "FDUSD", "TUSD", "DAI", "USDP"})

#: Contra que par se busca el precio de cada activo, en orden de preferencia.
QUOTE_ASSETS = ("USDT", "USDC", "BUSD")

#: Saldos por debajo de esto son polvo de operaciones, no una tenencia.
DUST = 1e-8

TIMEOUT = 20

#: Binance corta con HTTP 429 si se le encadenan consultas. Se reintenta con
#: espera creciente en vez de abandonar: un barrido largo cruza varias ventanas
#: y perderlo entero por un pico de trafico seria absurdo.
RETRY_STATUS = (429, 418)
RETRIES = 4


class BinanceError(RuntimeError):
    pass


@dataclass(frozen=True)
class Balance:
    asset: str
    free: float
    locked: float

    @property
    def total(self) -> float:
        return self.free + self.locked

    @property
    def is_cash(self) -> bool:
        return self.asset in STABLECOINS


def _request(path: str, params: dict[str, Any] | None = None, *,
             api_key: str = "", api_secret: str = "") -> Any:
    """GET a la API. Si hay secreto, firma la consulta (endpoints privados)."""
    params = dict(params or {})
    if api_secret:
        params["timestamp"] = int(time.time() * 1000)
        params["recvWindow"] = 60_000
        query = urllib.parse.urlencode(params)
        firma = hmac.new(api_secret.encode(), query.encode(), hashlib.sha256).hexdigest()
        query = f"{query}&signature={firma}"
    else:
        query = urllib.parse.urlencode(params)

    url = f"{BASE_URL}{path}?{query}" if query else f"{BASE_URL}{path}"
    request = urllib.request.Request(url, headers={"X-MBX-APIKEY": api_key} if api_key else {})
    for intento in range(RETRIES):
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detalle = exc.read().decode("utf-8", "replace")[:200]
            if exc.code in RETRY_STATUS and intento < RETRIES - 1:
                time.sleep(2 ** intento * 3)
                continue
            raise BinanceError(f"{path}: HTTP {exc.code} {detalle}") from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            if intento < RETRIES - 1:
                time.sleep(2 ** intento)
                continue
            raise BinanceError(f"{path}: {exc}") from exc
    raise BinanceError(f"{path}: sin respuesta tras {RETRIES} intentos")


def price_history(symbol: str, start: date, end: date | None = None) -> list[dict[str, Any]]:
    """Precio de cierre diario. Endpoint publico: no necesita claves.

    Binance devuelve como maximo 1000 velas por llamada, asi que se pagina
    hacia adelante hasta llegar a hoy.
    """
    end = end or date.today()
    desde = int(datetime(start.year, start.month, start.day, tzinfo=timezone.utc).timestamp() * 1000)
    hasta = int(datetime(end.year, end.month, end.day, 23, 59, tzinfo=timezone.utc).timestamp() * 1000)
    filas: list[dict[str, Any]] = []
    while desde < hasta:
        velas = _request("/api/v3/klines", {
            "symbol": symbol, "interval": "1d", "startTime": desde,
            "endTime": hasta, "limit": 1000,
        })
        if not velas:
            break
        for vela in velas:
            dia = datetime.fromtimestamp(vela[0] / 1000, tz=timezone.utc).date()
            filas.append({
                "ticker": symbol_base(symbol),
                "date": dia.isoformat(),
                "settlement": "SPOT",
                "price": float(vela[4]),          # cierre
                "volume": float(vela[5]),
                "opening": float(vela[1]),
                "max": float(vela[2]),
                "min": float(vela[3]),
            })
        siguiente = velas[-1][0] + 86_400_000
        if siguiente <= desde:
            break
        desde = siguiente
    return filas


def _epoch(day: date) -> int:
    return int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp() * 1000)


def symbol_base(symbol: str) -> str:
    """`BTCUSDT` -> `BTC`."""
    for quote in QUOTE_ASSETS:
        if symbol.endswith(quote):
            return symbol[: -len(quote)]
    return symbol


class BinanceSession:
    """Lo poco que hace falta de la cuenta: saldos y operaciones."""

    def __init__(self, api_key: str, api_secret: str) -> None:
        self.api_key = api_key
        self.api_secret = api_secret

    @property
    def has_credentials(self) -> bool:
        return bool(self.api_key and self.api_secret)

    def _signed(self, path: str, params: dict[str, Any] | None = None) -> Any:
        if not self.has_credentials:
            raise BinanceError("faltan las claves de Binance")
        return _request(path, params, api_key=self.api_key, api_secret=self.api_secret)

    def balances(self) -> list[Balance]:
        """Lo que tenes en la cuenta spot, sin el polvo."""
        data = self._signed("/api/v3/account")
        return [
            Balance(b["asset"], float(b["free"]), float(b["locked"]))
            for b in data.get("balances", [])
            if float(b["free"]) + float(b["locked"]) > DUST
        ]

    def trades(self, symbol: str) -> list[dict[str, Any]]:
        """Tus compras y ventas de un par."""
        return self._signed("/api/v3/myTrades", {"symbol": symbol, "limit": 1000}) or []

    def converts(self, start: date, end: date | None = None) -> Iterator[tuple[date, list[dict[str, Any]]]]:
        """Historial del boton *Convert*, ventana por ventana.

        Se entrega de a tramos para que quien lo consume pueda guardar el avance:
        un barrido largo puede chocar con el limite de consultas de Binance, y
        perder lo ya bajado obligaria a empezar de cero la proxima vez.

        No aparece en `myTrades`: para Binance una conversion no es una
        operacion de mercado. Si solo se miraran las operaciones spot, comprar
        con Convert cambiaria el saldo sin dejar rastro del costo.

        El endpoint acepta ventanas de 30 dias como maximo, asi que se pagina.
        """
        end = end or date.today()
        desde = start
        while desde < end:
            hasta = min(desde + timedelta(days=30), end)
            data = self._signed("/sapi/v1/convert/tradeFlow", {
                "startTime": _epoch(desde), "endTime": _epoch(hasta), "limit": 1000,
            }) or {}
            filas = data.get("list", []) if isinstance(data, dict) else data
            yield hasta, [c for c in filas if c.get("orderStatus") == "SUCCESS"]
            desde = hasta
            time.sleep(0.6)

    def deposits(self, start: date, end: date | None = None) -> list[dict[str, Any]]:
        """Depositos de cripto. Ventanas de 90 dias como maximo."""
        end = end or date.today()
        salida: list[dict[str, Any]] = []
        desde = start
        while desde < end:
            hasta = min(desde + timedelta(days=90), end)
            salida += self._signed("/sapi/v1/capital/deposit/hisrec", {
                "startTime": _epoch(desde), "endTime": _epoch(hasta), "limit": 1000,
            }) or []
            desde = hasta
            time.sleep(0.6)
        return [d for d in salida if d.get("status") == 1]      # 1 = acreditado

    def find_symbol(self, asset: str) -> str | None:
        """Contra que stablecoin cotiza este activo."""
        for quote in QUOTE_ASSETS:
            symbol = f"{asset}{quote}"
            try:
                _request("/api/v3/ticker/price", {"symbol": symbol})
                return symbol
            except BinanceError:
                continue
        return None


# ------------------------------------------------------------ normalizacion

def trade_uid(account: str, trade: dict[str, Any]) -> str:
    crudo = f"{account}|{trade.get('symbol')}|{trade.get('id')}"
    return hashlib.sha1(crudo.encode("utf-8")).hexdigest()


def normalize_trades(account: str, symbol: str,
                     raw: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Operaciones de Binance como filas de `movements`.

    Se escriben con la misma forma que las de PPI -- ticker, cantidad, importe y
    moneda -- para que el clasificador, el motor FIFO y la valuacion las traten
    igual sin saber de donde vienen.
    """
    base = symbol_base(symbol)
    quote = symbol[len(base):] or "USDT"
    filas: list[dict[str, Any]] = []
    for i, trade in enumerate(raw):
        dia = datetime.fromtimestamp(trade["time"] / 1000, tz=timezone.utc).date().isoformat()
        cantidad = float(trade["qty"])
        bruto = float(trade["quoteQty"])
        compra = bool(trade.get("isBuyer"))
        filas.append({
            "uid": trade_uid(account, trade),
            "account_number": account,
            "agreement_date": dia,
            "settlement_date": dia,
            "currency": quote,
            "amount": -bruto if compra else bruto,
            "price": float(trade["price"]),
            "description": f"{'COMPRA' if compra else 'VENTA'} {base}",
            "ticker": base,
            "quantity": cantidad,
            "balance": 0.0,
            "ordinal": i,
            "raw": dumps(trade),
        })
    return filas


def normalize_converts(account: str, raw: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Conversiones como movimientos de compra o venta.

    Cambiar USDT por BTC es comprar BTC; cambiar BTC por USDT es venderlo. Una
    conversion entre dos criptos es las dos cosas a la vez y se saltea: mezclaria
    el costo de las dos puntas y es preferible no adivinar.
    """
    filas: list[dict[str, Any]] = []
    for i, c in enumerate(raw):
        origen, destino = c.get("fromAsset", ""), c.get("toAsset", "")
        compra = origen in STABLECOINS and destino not in STABLECOINS
        venta = destino in STABLECOINS and origen not in STABLECOINS
        if not (compra or venta):
            continue
        ticker = destino if compra else origen
        cantidad = float(c["toAmount"] if compra else c["fromAmount"])
        bruto = float(c["fromAmount"] if compra else c["toAmount"])
        moneda = origen if compra else destino
        dia = datetime.fromtimestamp(int(c["createTime"]) / 1000, tz=timezone.utc).date()
        filas.append({
            "uid": hashlib.sha1(f"{account}|convert|{c['orderId']}".encode()).hexdigest(),
            "account_number": account,
            "agreement_date": dia.isoformat(),
            "settlement_date": dia.isoformat(),
            "currency": moneda,
            "amount": -bruto if compra else bruto,
            "price": bruto / cantidad if cantidad else 0.0,
            "description": f"{'COMPRA' if compra else 'VENTA'} {ticker}",
            "ticker": ticker,
            "quantity": cantidad,
            "balance": 0.0,
            "ordinal": i,
            "raw": dumps(c),
        })
    return filas


def normalize_deposits(account: str, raw: Iterable[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    """Depositos como ingresos de fondos.

    Un deposito de stablecoin es plata que entra: cuenta como aporte. Uno de
    cripto es un activo que llega con un costo que Binance no informa -- eso no
    se puede inventar, asi que se avisa y no se carga.
    """
    filas: list[dict[str, Any]] = []
    avisos: list[str] = []
    for i, dep in enumerate(raw):
        moneda = dep.get("coin", "")
        cantidad = float(dep.get("amount") or 0)
        dia = datetime.fromtimestamp(dep["insertTime"] / 1000, tz=timezone.utc).date()
        if moneda not in STABLECOINS:
            avisos.append(
                f"{dia} Binance: entraron {cantidad:,.8f} {moneda} por transferencia; "
                f"no se conoce a que precio los compraste (cargalo a mano si lo sabes)."
            )
            continue
        filas.append({
            "uid": hashlib.sha1(f"{account}|deposit|{dep.get('txId')}".encode()).hexdigest(),
            "account_number": account,
            "agreement_date": dia.isoformat(),
            "settlement_date": dia.isoformat(),
            "currency": moneda,
            "amount": cantidad,
            "price": 0.0,
            "description": "Ingreso de Fondos",
            "ticker": None,
            "quantity": 0.0,
            "balance": 0.0,
            "ordinal": i,
            "raw": dumps(dep),
        })
    return filas, avisos


def normalize_balances(account: str, balances: Iterable[Balance], prices: dict[str, float],
                       ts: str) -> list[dict[str, Any]]:
    """Saldos como filas de `snapshots`, para conciliar contra lo calculado."""
    filas: list[dict[str, Any]] = []
    for balance in balances:
        es_cash = balance.is_cash
        precio = 1.0 if es_cash else prices.get(balance.asset, 0.0)
        filas.append({
            "ts": ts,
            "account_number": account,
            "kind": "cash" if es_cash else "instrument",
            "group_name": "CRIPTO",
            "ticker": None if es_cash else balance.asset,
            "currency": balance.asset if es_cash else "USDT",
            "settlement": "SPOT",
            "quantity": None if es_cash else balance.total,
            "price": None if es_cash else precio,
            "amount": balance.total * precio,
            "raw": dumps({"asset": balance.asset, "free": balance.free, "locked": balance.locked}),
        })
    return filas
