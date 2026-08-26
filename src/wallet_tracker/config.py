"""Configuracion leida de variables de entorno / .env."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from dotenv import load_dotenv


#: Contenido del `.env` que crea `wallet-tracker init`. Es una copia literal de
#: `.env.example`; el test `test_config.py` verifica que no se separen, porque
#: son dos archivos que dicen lo mismo y es facil actualizar uno solo.
ENV_TEMPLATE = """# Credenciales de la API de PPI.
# Se generan desde tu cuenta PPI -> pestana "Gestiones" -> activar servicio API.
# NUNCA commitees este archivo con valores reales (.env esta en .gitignore).

PPI_PUBLIC_KEY=tu_api_key
PPI_PRIVATE_KEY=tu_api_secret

# Numero de cuenta comitente. Si lo dejas vacio se autodetecta al primer sync.
PPI_ACCOUNT_NUMBER=

# true = ambiente sandbox de PPI (datos de prueba). false = tu cuenta real.
PPI_SANDBOX=false

# Desde que fecha traer el historial de movimientos (YYYY-MM-DD).
PPI_HISTORY_START=2019-01-01

# Cotizacion del dolar con la que se mide toda la cartera.
#
# No hay una cotizacion oficial util, asi que se calcula: se toma el precio del
# mismo bono en pesos y en dolares, y el cociente es el dolar que el mercado
# esta pagando de verdad. Con esto se valua en dolares, se convierten los
# saldos en moneda extranjera y se calcula la TIR en dolares.
#
# GD30/GD30C da el CCL. Para medir al MEP, poné PPI_CCL_TICKER_USD=GD30D.
PPI_CCL_TICKER_ARS=GD30
PPI_CCL_TICKER_USD=GD30C
PPI_CCL_INSTRUMENT_TYPE=BONOS
PPI_CCL_SETTLEMENT=A-24HS

# Especie contra la que compararse en el panel (tiene que ser una que operaste).
PPI_BENCHMARK=SPY

# Binance (opcional). Crea las claves con permisos de SOLO LECTURA: desactiva
# "Enable Withdrawals" y "Enable Spot Trading". La herramienta solo lee.
# Sin claves, la cartera de Binance simplemente no aparece.
BINANCE_API_KEY=
BINANCE_API_SECRET=

# Ruta de la base local (default: ./data/ppi.db)
PPI_DB_PATH=data/ppi.db
"""


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "si", "sí"}


def _as_date(value: str | None, default: date) -> date:
    if not value:
        return default
    return datetime.strptime(value.strip(), "%Y-%m-%d").date()


@dataclass(frozen=True)
class Settings:
    public_key: str
    private_key: str
    account_number: str
    sandbox: bool
    history_start: date
    db_path: Path
    ccl_ticker_ars: str
    ccl_ticker_usd: str
    ccl_instrument_type: str
    ccl_settlement: str
    #: Especie contra la que se compara el panel. Va ultima y con default
    #: porque es opcional: sin ella el panel simplemente no muestra la fila.
    benchmark: str = "SPY"
    binance_key: str = ""
    binance_secret: str = ""

    @property
    def has_credentials(self) -> bool:
        return bool(self.public_key and self.private_key)

    @property
    def has_binance(self) -> bool:
        return bool(self.binance_key and self.binance_secret)


def load_settings(env_file: str | os.PathLike[str] | None = None) -> Settings:
    """Carga la configuracion. Busca un .env en el cwd salvo que se indique otro."""
    if env_file is not None:
        load_dotenv(env_file, override=False)
    else:
        load_dotenv(override=False)

    db_path = Path(os.getenv("PPI_DB_PATH") or "data/ppi.db").expanduser()

    return Settings(
        public_key=os.getenv("PPI_PUBLIC_KEY", "").strip(),
        private_key=os.getenv("PPI_PRIVATE_KEY", "").strip(),
        account_number=os.getenv("PPI_ACCOUNT_NUMBER", "").strip(),
        sandbox=_as_bool(os.getenv("PPI_SANDBOX"), default=False),
        history_start=_as_date(os.getenv("PPI_HISTORY_START"), date(2019, 1, 1)),
        db_path=db_path,
        ccl_ticker_ars=os.getenv("PPI_CCL_TICKER_ARS", "GD30").strip().upper(),
        ccl_ticker_usd=os.getenv("PPI_CCL_TICKER_USD", "GD30C").strip().upper(),
        ccl_instrument_type=os.getenv("PPI_CCL_INSTRUMENT_TYPE", "BONOS").strip(),
        ccl_settlement=os.getenv("PPI_CCL_SETTLEMENT", "A-24HS").strip(),
        benchmark=os.getenv("PPI_BENCHMARK", "SPY").strip().upper(),
        binance_key=os.getenv("BINANCE_API_KEY", "").strip(),
        binance_secret=os.getenv("BINANCE_API_SECRET", "").strip(),
    )
