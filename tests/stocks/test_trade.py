"""Test della CLI `stocks.trade` (registrazione delle operazioni nello stato locale)."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from stocks.models import Action, PositionKind
from stocks.state import JournalEntry, State, Trade, load_state, save_state
from stocks.trade import main

ROME = ZoneInfo("Europe/Rome")
NOW = datetime(2026, 11, 18, 16, 5, tzinfo=ROME)


def add(path, *extra: str, ticker="NVDA", side="buy", qty="1.3", price="140.50",
        currency="EUR", kind="opportunity") -> int:
    argv = ["add", "--ticker", ticker, "--side", side, "--qty", qty, "--price", price,
            "--currency", currency, "--state", str(path)]
    if kind is not None:
        argv += ["--kind", kind]
    return main([*argv, *extra], now=NOW)


def test_add_buy_saves_state(tmp_path, capsys):
    path = tmp_path / "state.json"
    assert add(path, "--date", "2026-11-18T15:40", "--note", "occasione dopo i risultati") == 0

    [t] = load_state(path).trades
    assert (t.ticker, t.side, t.qty, t.price, t.currency, t.kind) == (
        "NVDA", "buy", 1.3, 140.5, "EUR", PositionKind.opportunity,
    )
    assert t.fee_eur == 1.0
    assert t.note == "occasione dopo i risultati"
    # data senza fuso: ora italiana
    assert t.ts == datetime(2026, 11, 18, 15, 40, tzinfo=ROME)
    assert t.ts.utcoffset() is not None
    out = capsys.readouterr().out
    assert "NVDA" in out and "1,3" in out and "140,50 EUR" in out


def test_date_defaults_to_now_and_keeps_explicit_offset(tmp_path):
    path = tmp_path / "state.json"
    assert add(path) == 0
    assert add(path, "--date", "2026-11-18T09:40:00-05:00", "--fee", "0") == 0
    first, second = load_state(path).trades
    assert first.ts == NOW
    assert second.ts.utcoffset().total_seconds() == -5 * 3600
    assert second.fee_eur == 0.0


def test_ticker_is_uppercased(tmp_path):
    path = tmp_path / "state.json"
    assert add(path, ticker=" enel.mi ", currency="EUR", kind="core") == 0
    assert load_state(path).trades[0].ticker == "ENEL.MI"


@pytest.mark.parametrize(
    ("field", "value"),
    [("qty", "0"), ("qty", "-1"), ("price", "0"), ("price", "nan"), ("fee", "-1")],
)
def test_invalid_numbers_rejected(tmp_path, capsys, field, value):
    path = tmp_path / "state.json"
    extra = ["--fee", value] if field == "fee" else []
    kwargs = {field: value} if field != "fee" else {}
    assert add(path, *extra, **kwargs) == 2
    assert not path.exists()
    assert "non registrata" in capsys.readouterr().err


def test_kind_and_currency_are_required_choices(tmp_path):
    path = tmp_path / "state.json"
    with pytest.raises(SystemExit) as exc:
        add(path, kind=None)
    assert exc.value.code == 2
    with pytest.raises(SystemExit):
        add(path, currency="GBP")
    with pytest.raises(SystemExit):
        add(path, kind="margin")
    assert not path.exists()


def test_sell_beyond_holdings_rejected(tmp_path):
    path = tmp_path / "state.json"
    assert add(path, qty="1.3") == 0
    before = path.read_text(encoding="utf-8")
    assert add(path, side="sell", qty="2", price="150") == 2
    assert path.read_text(encoding="utf-8") == before
    # vendita della quantità posseduta: ammessa
    assert add(path, side="sell", qty="1.3", price="150") == 0
    assert [t.side for t in load_state(path).trades] == ["buy", "sell"]


def test_sell_without_position_rejected(tmp_path):
    assert add(tmp_path / "state.json", side="sell") == 2


def test_currency_or_kind_mismatch_rejected(tmp_path, capsys):
    path = tmp_path / "state.json"
    assert add(path) == 0
    assert add(path, currency="USD") == 2
    assert add(path, kind="core") == 2
    assert len(load_state(path).trades) == 1
    assert "valuta o tipo" in capsys.readouterr().err


def test_rest_of_state_is_preserved(tmp_path):
    path = tmp_path / "state.json"
    entry = JournalEntry(ts=NOW, ticker="KO", action=Action.TIENI, reasons=["x"], source="engine")
    save_state(State(journal=[entry], equity_history_eur=[(NOW.date(), 1000.0)]), path)

    assert add(path) == 0

    state = load_state(path)
    assert state.journal == [entry]
    assert state.equity_history_eur == [(NOW.date(), 1000.0)]
    assert len(state.trades) == 1


def test_invalid_state_file_exits_2(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{non json", encoding="utf-8")
    assert add(path) == 2
    assert path.read_text(encoding="utf-8") == "{non json"


def test_list_prints_trades_and_positions(tmp_path, capsys):
    path = tmp_path / "state.json"
    trade = Trade(ts=NOW, ticker="KO", side="buy", qty=2, price=50.0, currency="EUR",
                  kind=PositionKind.core)
    save_state(State(trades=[trade]), path)

    assert main(["list", "--state", str(path)], now=NOW) == 0

    out = capsys.readouterr().out
    assert "KO" in out and "acquisto" in out and "50,00 EUR" in out
    assert "Posizioni aperte" in out


def test_list_empty_state(tmp_path, capsys):
    assert main(["list", "--state", str(tmp_path / "state.json")], now=NOW) == 0
    assert "Nessuna operazione registrata" in capsys.readouterr().out
