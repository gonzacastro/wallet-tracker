"""La configuracion: que el ejemplo, la plantilla y el codigo no se separen."""

import pathlib
import re

from wallet_tracker.config import ENV_TEMPLATE, Settings, load_settings

RAIZ = pathlib.Path(__file__).resolve().parent.parent


def variables(texto):
    return set(re.findall(r"^([A-Z][A-Z0-9_]*)=", texto, re.M))


def test_el_ejemplo_y_la_plantilla_declaran_lo_mismo():
    """Son dos copias del mismo archivo: es facil actualizar una sola."""
    ejemplo = variables((RAIZ / ".env.example").read_text(encoding="utf-8"))
    assert ejemplo == variables(ENV_TEMPLATE)


def test_no_hay_variables_de_mas_ni_de_menos():
    """Cada variable del ejemplo se lee, y cada una que se lee esta en el ejemplo."""
    leidas = set(re.findall(
        r'os\.getenv\("([A-Z_]+)"',
        (RAIZ / "src" / "wallet_tracker" / "config.py").read_text(encoding="utf-8"),
    ))
    assert variables(ENV_TEMPLATE) == leidas


def test_todo_lo_que_se_lee_termina_en_un_campo_de_settings():
    """Una variable leida y tirada seria configuracion que no hace nada."""
    campos = set(Settings.__dataclass_fields__)
    assert len(campos) == len(variables(ENV_TEMPLATE))


def test_los_valores_por_defecto_funcionan_sin_ningun_env(monkeypatch):
    for var in variables(ENV_TEMPLATE):
        monkeypatch.delenv(var, raising=False)
    settings = load_settings(RAIZ / "no-existe.env")
    assert not settings.has_credentials
    assert not settings.has_binance
    assert settings.benchmark == "SPY"
    assert settings.db_path.name == "ppi.db"
