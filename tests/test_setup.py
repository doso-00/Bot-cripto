"""Test di Fase 0: verificano che lo scheletro del progetto sia coerente col brief."""

import subprocess
from pathlib import Path

import yaml

import bot

ROOT = Path(__file__).resolve().parent.parent


def test_package_importable():
    assert bot.__version__


def test_gitignore_excludes_secrets_and_generated_files():
    lines = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    for entry in [".env", "/data/", "/reports/", "/logs/", "/STOP"]:
        assert entry in lines, f"{entry} manca da .gitignore"


def test_env_example_has_no_secret_values():
    # Le righe *_API_KEY / *_API_SECRET devono restare vuote nel modello versionato
    for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        if line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        if "API_KEY" in name or "API_SECRET" in name:
            assert value.strip() == "", f"{name} non deve avere un valore in .env.example"


def test_env_example_defaults_to_backtest():
    assert "TRADING_MODE=backtest" in (ROOT / ".env.example").read_text(encoding="utf-8")


def test_settings_yaml_has_brief_defaults():
    settings = yaml.safe_load((ROOT / "config" / "settings.yaml").read_text(encoding="utf-8"))
    assert settings["symbols"] == ["BTC/USDT", "ETH/USDT"]
    assert settings["timeframe"] == "1h"
    assert settings["fee_rate"] == 0.001
    assert settings["slippage_rate"] == 0.0005
    assert settings["initial_capital_usdt"] == 1000


def test_live_limits_not_present_yet():
    # config/live_limits.yaml si crea solo in Fase 5 (CLAUDE.md, regola 3)
    assert not (ROOT / "config" / "live_limits.yaml").exists()


def test_source_code_is_not_gitignored():
    # Un pattern come "data/" senza "/" iniziale escluderebbe anche bot/data/
    result = subprocess.run(
        ["git", "check-ignore", "bot/data/db.py", "bot/data/quality.py"],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert result.stdout == "", f"file sorgente ignorati da git: {result.stdout}"
