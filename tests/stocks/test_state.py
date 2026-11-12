"""Test dello stato persistente (operazioni, piani, registro)."""

from datetime import date, datetime

import pytest

from stocks.models import Action, Plan, PositionKind
from stocks.state import (
    JournalEntry,
    PlanRevision,
    State,
    Trade,
    holdings_from_trades,
    load_state,
    save_state,
)


def trade(side="buy", qty=1.0, price=100.0, ticker="AAPL", currency="EUR", kind=PositionKind.core, day=1):
    return Trade(ts=datetime(2026, 11, day, 10, 0), ticker=ticker, side=side, qty=qty, price=price,
                 currency=currency, kind=kind)


def test_missing_file_gives_empty_state(tmp_path):
    assert load_state(tmp_path / "nope.json") == State()


@pytest.mark.parametrize("content", ["", "  \n\t "])
def test_empty_or_blank_file_gives_empty_state(tmp_path, content):
    p = tmp_path / "state.json"
    p.write_text(content)
    assert load_state(p) == State()


def test_invalid_json_raises(tmp_path):
    p = tmp_path / "state.json"
    p.write_text('{"trades": [')
    with pytest.raises(ValueError):
        load_state(p)


def test_roundtrip_json(tmp_path):
    plan = Plan(kind=PositionKind.core, entry_ref=100.0, review_level=85.0, exit_level=80.0, currency="EUR")
    state = State(
        trades=[trade()],
        plans={"AAPL": plan},
        revisions=[PlanRevision(ts=datetime(2026, 11, 2, 9), ticker="AAPL", field="stop", old=None, new="90",
                                source="riccardo", reason="prova", accepted=True)],
        journal=[JournalEntry(ts=datetime(2026, 11, 3, 9), ticker="AAPL", action=Action.TIENI, close=101.5,
                              reasons=["ok"], source="engine")],
        equity_history_eur=[(date(2026, 11, 1), 1000.0), (date(2026, 11, 2), 1012.5)],
    )
    p = tmp_path / "sub" / "state.json"
    p.parent.mkdir()
    save_state(state, p)
    assert '"2026-11-01"' in p.read_text()
    loaded = load_state(p)
    assert loaded == state
    assert isinstance(loaded.equity_history_eur[0][0], date)
    assert loaded.version == 1
    assert [f.name for f in p.parent.iterdir()] == ["state.json"]  # nessun file temporaneo


def test_save_overwrites_atomically(tmp_path):
    p = tmp_path / "state.json"
    save_state(State(trades=[trade()]), p)
    save_state(State(), p)
    assert load_state(p) == State()


def test_holdings_weighted_average():
    h = holdings_from_trades([trade(qty=2, price=100), trade(qty=1, price=130, day=2)])
    assert h["AAPL"].qty == 3
    assert h["AAPL"].avg_price == pytest.approx(110)
    assert h["AAPL"].currency == "EUR"
    assert h["AAPL"].kind == PositionKind.core


def test_partial_sell_keeps_avg_price():
    h = holdings_from_trades([trade(qty=3, price=110), trade("sell", qty=1, price=150, day=2)])
    assert h["AAPL"].qty == 2
    assert h["AAPL"].avg_price == pytest.approx(110)


def test_full_sell_removes_holding():
    assert holdings_from_trades([trade(qty=2), trade("sell", qty=2, day=2)]) == {}


def test_oversell_raises():
    with pytest.raises(ValueError):
        holdings_from_trades([trade(qty=1), trade("sell", qty=2, day=2)])


def test_trades_sorted_by_ts_before_processing():
    # la vendita è inserita per prima ma è cronologicamente successiva
    h = holdings_from_trades([trade("sell", qty=1, day=5), trade(qty=2, day=1)])
    assert h["AAPL"].qty == 1


def test_mixed_currency_or_kind_raises():
    with pytest.raises(ValueError):
        holdings_from_trades([trade(), trade(currency="USD", day=2)])
    with pytest.raises(ValueError):
        holdings_from_trades([trade(), trade(kind=PositionKind.opportunity, day=2)])
