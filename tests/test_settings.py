import pytest
import yaml
from pydantic import ValidationError

from bot.config import load_settings
from bot.config.settings import DEFAULT_SETTINGS_PATH


def test_project_settings_load(tmp_path):
    s = load_settings(root=tmp_path)
    assert s.timeframes == ["1h", "4h"]
    assert s.paths.database == tmp_path / "data" / "market.duckdb"


def _write(tmp_path, **changes):
    raw = yaml.safe_load(DEFAULT_SETTINGS_PATH.read_text(encoding="utf-8"))
    raw.update(changes)
    path = tmp_path / "settings.yaml"
    path.write_text(yaml.safe_dump(raw))
    return path


def test_unknown_key_is_rejected(tmp_path):
    with pytest.raises(ValidationError):
        load_settings(_write(tmp_path, fee_rat=0.001), root=tmp_path)


def test_incoherent_split_dates_are_rejected(tmp_path):
    with pytest.raises(ValueError, match="date incoerenti"):
        load_settings(_write(tmp_path, train_end="2025-06-30"), root=tmp_path)


def test_symbol_must_be_base_quote(tmp_path):
    with pytest.raises(ValidationError):
        load_settings(_write(tmp_path, symbols=["BTCUSDT"]), root=tmp_path)
