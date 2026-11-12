import pytest
import yaml
from pydantic import ValidationError

from stocks.config import DEFAULT_SETTINGS_PATH, load_settings, load_watchlist
from stocks.models import Action, PositionKind


def test_load_settings_defaults():
    s = load_settings()
    assert s.core_min_positions == 3
    assert s.core_max_positions == 5
    assert s.core_max_weight == 0.30
    assert s.opp_max_open == 2
    assert s.event_max_eur == 100
    assert s.opp_max_stop_pct == 0.08
    assert s.opp_min_reward_risk == 2.0
    assert s.core_review_drawdown == 0.15
    assert s.core_exit_drawdown == 0.20
    assert s.portfolio_halt_drawdown == 0.15
    assert s.history_years == 5
    assert s.quality_min_roe == 0.08
    assert s.opp_rsi_max == 35


def test_settings_rejects_negative_budget(tmp_path):
    raw = yaml.safe_load(DEFAULT_SETTINGS_PATH.read_text(encoding="utf-8"))
    raw["budget_eur"] = -5
    path = tmp_path / "settings.yaml"
    path.write_text(yaml.safe_dump(raw))
    with pytest.raises(ValidationError):
        load_settings(path)


def test_watchlist_has_nvda_and_benchmarks():
    w = load_watchlist()
    by_ticker = {i.ticker: i for i in w.items}
    assert by_ticker["NVDA"].proposed_by == "riccardo"
    for t in ("MSFT", "JNJ", "KO", "ENEL.MI", "ISP.MI"):
        assert by_ticker[t].proposed_by == "claude"
        assert by_ticker[t].note == "esempio da confermare"
    assert w.benchmarks == {"US": "^GSPC", "EU": "^STOXX50E"}


def test_settings_yaml_matches_spec_values():
    s = load_settings()
    assert s.budget_eur == 1000
    assert s.min_order_eur == 100
    assert s.fee_per_order_eur == 1.0
    assert s.capital_gains_tax == 0.26
    assert s.core_share == 0.80
    assert s.opp_max_total_share == 0.20


def test_enums_values():
    assert [a.value for a in Action] == [
        "COMPRA", "ACCUMULA", "TIENI", "ATTENDI", "RIVEDI", "RIDUCI",
        "VENDI", "NESSUNA_AZIONE", "DATI_MANCANTI",
    ]
    assert [k.value for k in PositionKind] == ["core", "opportunity", "event"]
