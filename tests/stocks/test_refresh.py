"""Test dell'orchestratore `stocks.refresh` con un client Yahoo finto (nessuna rete)."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from stocks.config import StocksSettings, WatchItem, Watchlist
from stocks.costs import net_pnl_eur
from stocks.models import Action, PositionKind, Snapshot
from stocks.refresh import (
    EU_MOVES_ISSUE,
    PLAN_CREATED_REASON,
    main,
    run_refresh,
)
from stocks.signals import CONVERTED_ENTRY_REASON
from stocks.state import State, Trade, load_state, save_state
from tests.stocks.fakes import FakeTicker, FakeYF, make_chain, make_earnings, make_history
from tests.stocks.signal_data import core_pullback_closes, rising

ROME = ZoneInfo("Europe/Rome")
# Giovedì sera: le barre del 12/11 di Milano (17:30) e di New York (16:00 = 22:00 a Roma) sono chiuse.
NOW = datetime(2026, 11, 12, 23, 0, tzinfo=ROME)
EURUSD = 1.17

GOOD_INFO = {
    "currency": "USD",
    "longName": "Azienda di prova",
    "trailingPE": 25.0,
    "returnOnEquity": 0.20,
    "debtToEquity": 40.0,
    "payoutRatio": 0.30,
}


@pytest.fixture(autouse=True)
def no_retry_wait(monkeypatch):
    """Un solo tentativo e nessuna attesa: i test sui fallimenti restano istantanei."""
    monkeypatch.setattr("stocks.data.with_retry", lambda fn, **kw: fn())
    monkeypatch.setattr("stocks.earnings.with_retry", lambda fn, **kw: fn())


@pytest.fixture
def s():
    return StocksSettings()


def days(n: int, end: str = "2026-11-12") -> list[str]:
    return [d.strftime("%Y-%m-%d") for d in pd.bdate_range(end=end, periods=n)]


def history(closes, tz: str = "America/New_York", end: str = "2026-11-12"):
    return make_history(days(len(closes), end), closes, tz=tz)


def fx_ticker() -> FakeTicker:
    # la barra del 12/11 è parziale (il cambio non chiude): vale quella dell'11/11
    return FakeTicker(history=make_history(days(3), [1.16, EURUSD, 1.18], tz="UTC"))


def nvda_ticker(earnings=True) -> FakeTicker:
    """NVDA in salita fino a 100 USD con la trimestrale il 17/11 dopo la chiusura."""
    rows = [("2026-11-17 16:20", 1.25, None, None), ("2026-08-27 16:20", 1.0, 1.1, 10.0)]
    chain = make_chain(calls=[(100.0, 4.0, 5.0, 4.5)], puts=[(100.0, 4.0, 5.0, 4.5)])
    return FakeTicker(
        history=history(rising()),
        info={**GOOD_INFO, "longName": "NVIDIA Corporation"},
        earnings=make_earnings(rows) if earnings else None,
        options=("2026-11-20",),
        chains={"2026-11-20": chain},
    )


def benchmark_ticker() -> FakeTicker:
    return FakeTicker(history=history([6000.0, 6060.0]))


def client(**tickers: FakeTicker) -> FakeYF:
    base = {"EURUSD=X": fx_ticker(), "^GSPC": benchmark_ticker(), "NVDA": nvda_ticker()}
    base.update({k.replace("_", "."): v for k, v in tickers.items()})
    return FakeYF(base)


def watchlist(*tickers: str) -> Watchlist:
    return Watchlist(
        items=[WatchItem(ticker=t, proposed_by="riccardo") for t in tickers],
        benchmarks={"US": "^GSPC"},
    )


def run(tmp_path, s, yf_client, wl=None, now=NOW) -> Snapshot:
    return run_refresh(
        now,
        tmp_path / "state.json",
        tmp_path / "out",
        s,
        wl or watchlist("NVDA"),
        client=yf_client,
    )


def by_ticker(snap: Snapshot):
    return {t.ticker: t for t in snap.tickers}


def buy(ticker, qty, price, currency="EUR", kind=PositionKind.core, day="2026-11-02"):
    return Trade(
        ts=datetime.fromisoformat(f"{day}T10:00:00+01:00"),
        ticker=ticker,
        side="buy",
        qty=qty,
        price=price,
        currency=currency,
        kind=kind,
    )


def test_refresh_writes_snapshot(tmp_path, s):
    snap = run(tmp_path, s, client())

    written = Snapshot.model_validate_json((tmp_path / "out" / "snapshot.json").read_text())
    assert written == snap
    assert snap.generated_at == NOW
    assert snap.run_slot == "manuale"
    assert snap.eurusd == pytest.approx(EURUSD)
    assert snap.benchmarks == {"^GSPC": pytest.approx(0.01)}
    assert snap.issues == []

    nvda = by_ticker(snap)["NVDA"]
    assert nvda.data_ok is True
    assert nvda.name == "NVIDIA Corporation"
    assert nvda.currency == "USD"
    assert nvda.proposed_by == "riccardo"
    assert nvda.asof.isoformat() == "2026-11-12"
    assert nvda.close == pytest.approx(100.0)
    assert nvda.close_eur == pytest.approx(100.0 / EURUSD)
    assert nvda.sma200 is not None and nvda.atr14 is not None and nvda.rsi14 is not None
    assert nvda.drawdown_63d_pct == pytest.approx(0.0)
    assert nvda.high_63d == pytest.approx(100.0)
    assert nvda.signal.action == Action.ATTENDI
    assert nvda.earnings.next_ts.date().isoformat() == "2026-11-17"
    assert nvda.earnings.days_to_next == 5
    # straddle (4,5 + 4,5) / 100
    assert nvda.earnings.expected_move_pct == pytest.approx(0.09)
    assert nvda.earnings.past_moves_pct  # titolo USA: movimenti storici presenti

    state = load_state(tmp_path / "state.json")
    assert [(d.isoformat(), v) for d, v in state.equity_history_eur] == [("2026-11-12", 1000.0)]
    assert snap.portfolio.cash_eur == 1000.0
    assert snap.portfolio.peak_value_eur == 1000.0
    assert snap.portfolio.halted is False


def test_one_ticker_failing_does_not_stop_others(tmp_path, s):
    yf_client = client(XXX=FakeTicker(fail_times=99))

    snap = run(tmp_path, s, yf_client, wl=watchlist("NVDA", "XXX"))

    t = by_ticker(snap)
    assert t["NVDA"].data_ok is True
    assert t["NVDA"].signal.action == Action.ATTENDI
    assert t["XXX"].data_ok is False
    assert t["XXX"].signal.action == Action.DATI_MANCANTI
    assert t["XXX"].data_issues
    assert any(issue.startswith("XXX:") for issue in snap.issues)
    assert not any(issue.startswith("NVDA:") for issue in snap.issues)


def test_empty_history_is_missing_data(tmp_path, s):
    snap = run(tmp_path, s, client(), wl=watchlist("NVDA", "DELISTED"))

    t = by_ticker(snap)["DELISTED"]
    assert t.data_ok is False
    assert t.signal.action == Action.DATI_MANCANTI
    assert any(issue.startswith("DELISTED:") for issue in snap.issues)


def test_holding_outside_watchlist_is_followed(tmp_path, s):
    ko = FakeTicker(history=history(rising()), info={**GOOD_INFO, "longName": "Coca-Cola"})
    save_state(State(trades=[buy("KO", qty=2, price=50.0)]), tmp_path / "state.json")

    snap = run(tmp_path, s, client(KO=ko))

    t = by_ticker(snap)
    assert set(t) == {"NVDA", "KO"}
    ko_snap = t["KO"]
    assert ko_snap.proposed_by is None
    assert ko_snap.holding is not None and ko_snap.holding.qty == 2
    assert ko_snap.data_ok is True
    # posizione in EUR: soglie in euro, nessuna raggiunta
    assert ko_snap.signal.action == Action.TIENI

    value = 2 * 100.0 / EURUSD
    assert ko_snap.pnl_net_eur == pytest.approx(net_pnl_eur(100.0, value, s))
    p = snap.portfolio
    assert p.invested_eur == pytest.approx(100.0)
    assert p.value_eur == pytest.approx(value)
    assert p.cash_eur == pytest.approx(900.0)
    assert p.pnl_net_eur == pytest.approx(ko_snap.pnl_net_eur)
    assert p.peak_value_eur == pytest.approx(900.0 + value)


def test_first_run_empty_state(tmp_path, s):
    msft = FakeTicker(history=history(core_pullback_closes()), info=GOOD_INFO)
    state_path = tmp_path / "nuova" / "state.json"  # né il file né la cartella esistono

    snap = run_refresh(NOW, state_path, tmp_path / "out", s, watchlist("NVDA", "MSFT"),
                       client=client(MSFT=msft))

    assert by_ticker(snap)["MSFT"].signal.action == Action.COMPRA
    assert by_ticker(snap)["MSFT"].signal.plan is not None
    state = load_state(state_path)
    # il piano di un COMPRA si salva solo quando Riccardo registra l'acquisto
    assert state.plans == {}
    assert {(j.ticker, j.action) for j in state.journal} == {
        ("NVDA", Action.ATTENDI),
        ("MSFT", Action.COMPRA),
    }
    assert all(j.source == "engine" for j in state.journal)
    assert len(state.equity_history_eur) == 1


def test_journal_only_on_action_change(tmp_path, s):
    run(tmp_path, s, client())
    run(tmp_path, s, client())
    assert len(load_state(tmp_path / "state.json").journal) == 1

    # la trimestrale sparisce: l'azione cambia e il registro aggiunge una voce
    run(tmp_path, s, client(NVDA=nvda_ticker(earnings=False)))
    journal = load_state(tmp_path / "state.json").journal
    assert [j.action for j in journal] == [Action.ATTENDI, Action.NESSUNA_AZIONE]
    assert journal[-1].close == pytest.approx(100.0)
    assert journal[-1].reasons


def test_failed_ticker_without_prices_is_not_journaled(tmp_path, s):
    run(tmp_path, s, client(XXX=FakeTicker(fail_times=99)), wl=watchlist("NVDA", "XXX"))
    assert [j.ticker for j in load_state(tmp_path / "state.json").journal] == ["NVDA"]


def test_equity_history_one_point_per_day(tmp_path, s):
    run(tmp_path, s, client())
    run(tmp_path, s, client())
    run(tmp_path, s, client(), now=datetime(2026, 11, 13, 23, 0, tzinfo=ROME))
    dates = [d.isoformat() for d, _ in load_state(tmp_path / "state.json").equity_history_eur]
    assert dates == ["2026-11-12", "2026-11-13"]


def test_plan_created_for_new_holding(tmp_path, s):
    ko = FakeTicker(history=history(rising()), info=GOOD_INFO)
    save_state(State(trades=[buy("KO", qty=2, price=50.0)]), tmp_path / "state.json")

    run(tmp_path, s, client(KO=ko))

    state = load_state(tmp_path / "state.json")
    plan = state.plans["KO"]
    assert plan.kind == PositionKind.core
    assert plan.currency == "USD"
    # prezzo medio in EUR su un titolo in USD: ingresso convertito al cambio di oggi
    assert plan.entry_ref == pytest.approx(50.0 * EURUSD)
    assert plan.review_level == pytest.approx(50.0 * EURUSD * 0.85)
    assert CONVERTED_ENTRY_REASON in plan.scenarios
    [rev] = state.revisions
    assert (rev.ticker, rev.source, rev.reason, rev.accepted) == ("KO", "engine", PLAN_CREATED_REASON, True)

    # al run successivo il piano esiste già: nessuna nuova revisione
    run(tmp_path, s, client(KO=FakeTicker(history=history(rising()), info=GOOD_INFO)))
    assert len(load_state(tmp_path / "state.json").revisions) == 1


def test_plan_for_opportunity_uses_prices_up_to_first_buy(tmp_path, s):
    xyz = FakeTicker(history=history(rising()), info=GOOD_INFO)
    trade = buy("XYZ", qty=1, price=90.0, currency="USD", kind=PositionKind.opportunity, day="2026-10-20")
    save_state(State(trades=[trade]), tmp_path / "state.json")

    snap = run(tmp_path, s, client(XYZ=xyz))

    plan = load_state(tmp_path / "state.json").plans["XYZ"]
    assert plan.kind == PositionKind.opportunity
    assert plan.entry_ref == pytest.approx(90.0)
    assert plan.deadline.isoformat() == "2026-11-19"  # 20/10 + 30 giorni
    assert plan.stop is not None and plan.stop < 90.0
    assert by_ticker(snap)["XYZ"].signal.plan == plan


def test_plan_removed_when_position_closed(tmp_path, s):
    ko = FakeTicker(history=history(rising()), info=GOOD_INFO)
    save_state(State(trades=[buy("KO", qty=2, price=50.0)]), tmp_path / "state.json")
    run(tmp_path, s, client(KO=ko))

    state = load_state(tmp_path / "state.json")
    sell = buy("KO", qty=2, price=60.0, day="2026-11-10").model_copy(update={"side": "sell"})
    state.trades.append(sell)
    save_state(state, tmp_path / "state.json")
    snap = run(tmp_path, s, client(KO=FakeTicker(history=history(rising()), info=GOOD_INFO)))

    state = load_state(tmp_path / "state.json")
    assert "KO" not in state.plans
    assert state.revisions[-1].new is None
    assert "KO" not in by_ticker(snap)  # non più in portafoglio né in watchlist


def test_invalid_trades_do_not_stop_others(tmp_path, s):
    ko = FakeTicker(history=history(rising()), info=GOOD_INFO)
    oversell = buy("KO", qty=5, price=60.0).model_copy(update={"side": "sell"})
    save_state(State(trades=[oversell]), tmp_path / "state.json")

    snap = run(tmp_path, s, client(KO=ko))

    t = by_ticker(snap)
    assert t["NVDA"].data_ok is True
    assert t["KO"].data_ok is False
    assert t["KO"].signal.action == Action.DATI_MANCANTI
    assert any(issue.startswith("KO:") for issue in snap.issues)
    # valore del portafoglio non affidabile: nessun punto nella storia
    assert load_state(tmp_path / "state.json").equity_history_eur == []


def test_held_ticker_without_prices_skips_equity_point(tmp_path, s):
    save_state(State(trades=[buy("KO", qty=2, price=50.0)]), tmp_path / "state.json")

    snap = run(tmp_path, s, client(KO=FakeTicker(fail_times=99)))

    assert by_ticker(snap)["KO"].signal.action == Action.DATI_MANCANTI
    assert snap.portfolio.invested_eur == pytest.approx(100.0)
    assert snap.portfolio.halted is False
    assert any("Valore del portafoglio incompleto" in i for i in snap.issues)
    state = load_state(tmp_path / "state.json")
    assert state.equity_history_eur == []
    assert state.plans == {}  # senza prezzi il piano si crea al prossimo run


def test_fundamentals_failure_is_not_fatal_and_currency_falls_back(tmp_path, s):
    yf_client = client(
        NVDA=FakeTicker(history=history(rising()), info_error=RuntimeError("429")),
        ENEL_MI=FakeTicker(history=history(rising(), tz="Europe/Rome"), info_error=RuntimeError("429")),
    )

    snap = run(tmp_path, s, yf_client, wl=watchlist("NVDA", "ENEL.MI"))

    t = by_ticker(snap)
    assert t["NVDA"].currency == "USD"
    assert t["ENEL.MI"].currency == "EUR"
    assert t["ENEL.MI"].close_eur == pytest.approx(t["ENEL.MI"].close)
    for tk in ("NVDA", "ENEL.MI"):
        assert t[tk].data_ok is True
        assert t[tk].name == tk
        assert any("fondamentali" in i for i in t[tk].data_issues)
        assert any("valuta" in i for i in t[tk].data_issues)
        # qualità non verificabile senza fondamentali: nessun acquisto
        assert t[tk].signal.action == Action.NESSUNA_AZIONE


def test_held_position_survives_fundamentals_failure(tmp_path, s):
    save_state(State(trades=[buy("KO", qty=2, price=50.0)]), tmp_path / "state.json")
    ko = FakeTicker(history=history(rising()), info_error=RuntimeError("429"))

    snap = run(tmp_path, s, client(KO=ko))

    assert by_ticker(snap)["KO"].signal.action == Action.TIENI


def test_european_ticker_has_no_past_moves(tmp_path, s):
    rows = [("2026-07-30 07:00", 0.2, 0.25, 5.0), ("2026-05-08 07:00", 0.2, 0.18, -3.0)]
    enel = FakeTicker(
        history=history(rising(), tz="Europe/Rome"),
        info={**GOOD_INFO, "currency": "EUR"},
        earnings=make_earnings(rows, tz="Europe/Rome"),
    )

    snap = run(tmp_path, s, client(ENEL_MI=enel), wl=watchlist("ENEL.MI"))

    e = by_ticker(snap)["ENEL.MI"]
    assert e.earnings.last_ts is not None
    assert e.earnings.past_moves_pct == []
    assert e.earnings.avg_abs_move_pct is None
    assert EU_MOVES_ISSUE in e.data_issues
    assert e.data_ok is True


def test_benchmark_failure_is_an_issue(tmp_path, s):
    yf_client = client()
    del yf_client.tickers["^GSPC"]

    snap = run(tmp_path, s, yf_client)

    assert snap.benchmarks == {}
    assert any("^GSPC" in i for i in snap.issues)
    assert by_ticker(snap)["NVDA"].data_ok is True


def test_halted_portfolio_blocks_buys(tmp_path, s):
    msft = FakeTicker(history=history(core_pullback_closes()), info=GOOD_INFO)
    save_state(
        State(equity_history_eur=[(datetime(2026, 11, 1).date(), 1300.0)]),
        tmp_path / "state.json",
    )

    snap = run(tmp_path, s, client(MSFT=msft), wl=watchlist("MSFT"))

    assert snap.portfolio.halted is True
    assert snap.portfolio.peak_value_eur == 1300.0
    sig = by_ticker(snap)["MSFT"].signal
    assert sig.action == Action.NESSUNA_AZIONE
    assert sig.blocked_by is not None


def test_naive_now_rejected(tmp_path, s):
    with pytest.raises(ValueError):
        run(tmp_path, s, client(), now=datetime(2026, 11, 12, 23, 0))


def cli_args(tmp_path) -> list[str]:
    return ["--state", str(tmp_path / "state.json"), "--out", str(tmp_path / "out"), "--slot", "sera"]


def test_exit_code_2_when_no_data(tmp_path):
    # watchlist reale: nessun titolo ha prezzi, solo il cambio
    code = main(cli_args(tmp_path), client=FakeYF({"EURUSD=X": fx_ticker()}), now=NOW)
    assert code == 2
    snap = Snapshot.model_validate_json((tmp_path / "out" / "snapshot.json").read_text())
    assert snap.run_slot == "sera"
    assert not any(t.data_ok for t in snap.tickers)


def test_exit_code_2_when_fx_unavailable(tmp_path):
    assert main(cli_args(tmp_path), client=FakeYF(), now=NOW) == 2


def test_exit_code_0_when_some_tickers_fail(tmp_path):
    code = main(cli_args(tmp_path), client=client(), now=NOW)
    assert code == 0
    snap = Snapshot.model_validate_json((tmp_path / "out" / "snapshot.json").read_text())
    assert by_ticker(snap)["NVDA"].data_ok is True
    assert snap.issues  # gli altri titoli della watchlist non hanno dati
