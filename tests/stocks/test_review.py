"""Test della revisione di Claude e del validatore (`stocks.review`, `stocks.apply_review`)."""

import json
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from stocks.apply_review import apply, main
from stocks.models import (
    Action,
    EarningsInfo,
    Fundamentals,
    Holding,
    Plan,
    PortfolioSummary,
    PositionKind,
    Signal,
    Snapshot,
    TickerSnapshot,
)
from stocks.review import ClaudeReview, Proposal, TickerReview
from stocks.signals import (
    CONVERTED_ENTRY_REASON,
    open_position_stop_line,
    opportunity_scenarios,
)
from stocks.state import State, load_state, save_state

ROME = ZoneInfo("Europe/Rome")
NOW = datetime(2026, 11, 12, 9, 0, tzinfo=ROME)
TODAY = date(2026, 11, 12)
DEADLINE = date(2026, 12, 10)


def make_plan(stop: float | None = 90.0, deadline: date | None = DEADLINE) -> Plan:
    return Plan(
        kind=PositionKind.opportunity,
        entry_ref=100.0,
        stop=stop,
        target=120.0,
        deadline=deadline,
        currency="USD",
        stop_eur=76.0,
        target_eur=100.0,
    )


def make_ticker(
    ticker: str,
    action: Action = Action.TIENI,
    held: bool = False,
    close: float | None = 100.0,
    plan: Plan | None = None,
) -> TickerSnapshot:
    return TickerSnapshot(
        ticker=ticker,
        name=ticker,
        currency="USD",
        proposed_by="riccardo",
        asof=TODAY,
        close=close,
        close_eur=None if close is None else close / 1.25,
        fundamentals=Fundamentals(),
        earnings=EarningsInfo(),
        holding=Holding(
            ticker=ticker,
            kind=PositionKind.opportunity,
            qty=1,
            avg_price=100.0,
            currency="USD",
        )
        if held
        else None,
        signal=Signal(action=action, plan=plan),
        data_ok=close is not None,
    )


def make_snapshot(*tickers: TickerSnapshot) -> Snapshot:
    return Snapshot(
        generated_at=NOW,
        run_slot="mattina",
        eurusd=1.25,
        benchmarks={},
        tickers=list(tickers),
        portfolio=PortfolioSummary(
            invested_eur=0,
            value_eur=0,
            pnl_net_eur=0,
            cash_eur=1000,
            halted=False,
            peak_value_eur=1000,
        ),
    )


def held_setup(stop: float | None = 90.0, deadline: date | None = DEADLINE):
    """Snapshot e stato con AAA posseduto e un piano aperto."""
    plan = make_plan(stop, deadline)
    snap = make_snapshot(make_ticker("AAA", held=True, plan=plan))
    state = State(plans={"AAA": plan.model_copy()})
    return snap, state


def prop(
    type_: str, value: str | None = None, kind: str = "notizia", reason: str = "motivo"
):
    return Proposal(type=type_, value=value, reason=reason, reason_kind=kind)


def review_for(
    ticker: str, *proposals: Proposal, event_class: str = "nessuno"
) -> ClaudeReview:
    return ClaudeReview(
        generated_at=NOW - timedelta(hours=1),
        tickers=[
            TickerReview(
                ticker=ticker,
                event_class=event_class,
                summary="sintesi",
                explanation="spiegazione",
                news=[],
                proposals=list(proposals),
            )
        ],
        glossary=[],
        market_note="nota",
    )


# --- tighten_stop -------------------------------------------------------------


def test_tighten_stop_accepted():
    snap, state = held_setup()
    view = apply(snap, review_for("AAA", prop("tighten_stop", "95.5")), state, NOW)
    assert [r.field for r in view.applied] == ["stop"]
    assert state.plans["AAA"].stop == 95.5
    assert view.snapshot.tickers[0].signal.plan.stop == 95.5
    assert view.snapshot.tickers[0].signal.plan.stop_eur == 76.4  # 95.5 / 1.25
    rev = view.applied[0]
    assert (rev.source, rev.old, rev.new, rev.accepted) == (
        "claude",
        "90.0",
        "95.5",
        True,
    )
    assert rev.reason == "motivo"


@pytest.mark.parametrize("value", ["90", "85", "100", "120"])
def test_tighten_stop_rejected_when_not_between_stop_and_close(value):
    snap, state = held_setup()
    view = apply(snap, review_for("AAA", prop("tighten_stop", value)), state, NOW)
    assert not view.applied and len(view.rejected) == 1
    assert state.plans["AAA"].stop == 90.0


def test_cannot_lower_stop():
    snap, state = held_setup()
    view = apply(snap, review_for("AAA", prop("tighten_stop", "80")), state, NOW)
    assert state.plans["AAA"].stop == 90.0
    assert view.snapshot.tickers[0].signal.plan.stop == 90.0
    assert not view.rejected[0].accepted


def test_tighten_stop_rejected_without_current_stop():
    snap, state = held_setup(stop=None)
    view = apply(snap, review_for("AAA", prop("tighten_stop", "95")), state, NOW)
    assert len(view.rejected) == 1 and state.plans["AAA"].stop is None


def test_tighten_stop_rejected_when_value_unparseable():
    snap, state = held_setup()
    view = apply(snap, review_for("AAA", prop("tighten_stop", "alto")), state, NOW)
    assert len(view.rejected) == 1 and state.plans["AAA"].stop == 90.0


def test_proposal_rejected_without_open_position():
    snap = make_snapshot(make_ticker("AAA", plan=make_plan()))
    state = State(
        plans={"AAA": make_plan()}
    )  # piano ma nessuna posizione nello snapshot
    view = apply(snap, review_for("AAA", prop("tighten_stop", "95")), state, NOW)
    assert "nessuna posizione aperta" in view.rejected[0].reason
    assert state.plans["AAA"].stop == 90.0


# --- shorten_deadline -----------------------------------------------------------


def test_shorten_deadline_accepted():
    snap, state = held_setup()
    view = apply(
        snap, review_for("AAA", prop("shorten_deadline", "2026-11-30")), state, NOW
    )
    assert state.plans["AAA"].deadline == date(2026, 11, 30)
    assert view.snapshot.tickers[0].signal.plan.deadline == date(2026, 11, 30)
    assert view.applied[0].field == "deadline"


def test_shorten_deadline_today_accepted():
    snap, state = held_setup()
    view = apply(
        snap, review_for("AAA", prop("shorten_deadline", "2026-11-12")), state, NOW
    )
    assert state.plans["AAA"].deadline == TODAY and len(view.applied) == 1


@pytest.mark.parametrize("value", ["2026-11-11", "2026-12-10", "2027-01-01", "domani"])
def test_shorten_deadline_rejected(value):
    snap, state = held_setup()
    view = apply(snap, review_for("AAA", prop("shorten_deadline", value)), state, NOW)
    assert len(view.rejected) == 1 and state.plans["AAA"].deadline == DEADLINE


# --- extend_deadline ------------------------------------------------------------


def test_extend_deadline_accepted_with_event_up_to_30_days():
    snap, state = held_setup()
    new = (DEADLINE + timedelta(days=30)).isoformat()
    view = apply(
        snap, review_for("AAA", prop("extend_deadline", new, kind="evento")), state, NOW
    )
    assert state.plans["AAA"].deadline == DEADLINE + timedelta(days=30)
    assert len(view.applied) == 1


def test_extend_deadline_rejected_without_event_reason():
    snap, state = held_setup()
    new = (DEADLINE + timedelta(days=5)).isoformat()
    view = apply(
        snap,
        review_for("AAA", prop("extend_deadline", new, kind="notizia")),
        state,
        NOW,
    )
    assert len(view.rejected) == 1 and state.plans["AAA"].deadline == DEADLINE


def test_extend_deadline_rejected_beyond_30_days():
    snap, state = held_setup()
    new = (DEADLINE + timedelta(days=31)).isoformat()
    view = apply(
        snap, review_for("AAA", prop("extend_deadline", new, kind="evento")), state, NOW
    )
    assert len(view.rejected) == 1 and state.plans["AAA"].deadline == DEADLINE


def test_extend_deadline_rejected_when_not_later():
    snap, state = held_setup()
    view = apply(
        snap,
        review_for("AAA", prop("extend_deadline", DEADLINE.isoformat(), kind="evento")),
        state,
        NOW,
    )
    assert len(view.rejected) == 1


# --- request_review, downgrade_buy, watch -----------------------------------------


def test_request_review_sets_rivedi():
    snap, state = held_setup()
    view = apply(
        snap, review_for("AAA", prop("request_review", reason="causa legale aperta")), state, NOW
    )
    assert view.snapshot.tickers[0].signal.action == Action.RIVEDI
    assert view.applied[0].field == "action"
    assert view.snapshot.tickers[0].signal.reasons[-1] == (
        "Revisione richiesta dalla lettura delle notizie: causa legale aperta"
    )


def test_request_review_rejected_without_position():
    snap = make_snapshot(make_ticker("AAA"))
    view = apply(snap, review_for("AAA", prop("request_review")), State(), NOW)
    assert "nessuna posizione aperta" in view.rejected[0].reason
    assert view.snapshot.tickers[0].signal.action == Action.TIENI


@pytest.mark.parametrize("action", [Action.COMPRA, Action.ACCUMULA])
def test_downgrade_buy_accepted(action):
    snap = make_snapshot(make_ticker("AAA", action=action))
    snap.tickers[0].signal.amount_eur = 150.0
    view = apply(
        snap,
        review_for("AAA", prop("downgrade_buy", reason="causa legale")),
        State(),
        NOW,
    )
    sig = view.snapshot.tickers[0].signal
    assert sig.action == Action.NESSUNA_AZIONE and sig.amount_eur is None
    assert "causa legale" in sig.reasons[-1]
    assert len(view.applied) == 1


def test_downgrade_buy_rejected_on_other_action():
    snap = make_snapshot(make_ticker("AAA", action=Action.TIENI))
    view = apply(snap, review_for("AAA", prop("downgrade_buy")), State(), NOW)
    assert len(view.rejected) == 1
    assert view.snapshot.tickers[0].signal.action == Action.TIENI


def test_watch_marks_ticker_without_changing_signal():
    snap = make_snapshot(make_ticker("AAA", action=Action.ATTENDI))
    view = apply(snap, review_for("AAA", prop("watch")), State(), NOW)
    assert view.watched == ["AAA"]
    assert view.snapshot.tickers[0].signal.action == Action.ATTENDI
    assert len(view.applied) == 1


def test_cannot_create_buy():
    snap = make_snapshot(
        make_ticker("AAA", action=Action.ATTENDI),
        make_ticker("BBB", action=Action.TIENI, held=True, plan=make_plan()),
    )
    state = State(plans={"BBB": make_plan()})
    review = review_for(
        "AAA", prop("watch"), prop("request_review"), prop("downgrade_buy")
    )
    review.tickers.append(
        review_for(
            "BBB", prop("tighten_stop", "95"), prop("request_review"), prop("watch")
        ).tickers[0]
    )
    view = apply(snap, review, state, NOW)
    assert not any(
        t.signal.action in (Action.COMPRA, Action.ACCUMULA)
        for t in view.snapshot.tickers
    )
    assert all(t.signal.amount_eur is None for t in view.snapshot.tickers)


# --- ticker sconosciuto, copia, registro -------------------------------------------


def test_unknown_ticker_rejected():
    snap, state = held_setup()
    view = apply(
        snap, review_for("ZZZ", prop("watch"), prop("tighten_stop", "95")), state, NOW
    )
    assert not view.applied and len(view.rejected) == 2
    assert "ZZZ" not in view.reviews
    assert view.watched == []


def test_apply_does_not_mutate_input_snapshot():
    snap, state = held_setup()
    before = snap.model_dump_json()
    view = apply(snap, review_for("AAA", prop("tighten_stop", "95")), state, NOW)
    assert snap.model_dump_json() == before
    assert view.snapshot is not snap


def test_rejected_are_logged():
    snap, state = held_setup()
    review = review_for("AAA", prop("tighten_stop", "80"), prop("tighten_stop", "95"))
    view = apply(snap, review, state, NOW)
    assert [r.accepted for r in state.revisions] == [False, True]
    assert len(view.applied) == 1 and len(view.rejected) == 1
    assert "motivo" in view.rejected[0].reason and len(view.rejected[0].reason) > len(
        "motivo"
    )


def test_events_become_journal_entries():
    snap, state = held_setup()
    review = review_for("AAA", event_class="guidance_giu")
    apply(snap, review, state, NOW)
    entries = [j for j in state.journal if j.source == "claude"]
    assert len(entries) == 1 and entries[0].event_class == "guidance_giu"
    apply(snap, review_for("AAA", event_class="nessuno"), state, NOW)
    assert len([j for j in state.journal if j.source == "claude"]) == 1


def test_stale_review_is_ignored():
    snap, state = held_setup()
    review = review_for("AAA", prop("tighten_stop", "95"))
    review.generated_at = NOW - timedelta(hours=13)
    view = apply(snap, review, state, NOW)
    assert not view.review_available and view.reviews == {}
    assert state.plans["AAA"].stop == 90.0 and state.revisions == []


# --- schema e CLI ----------------------------------------------------------------------


def test_extra_fields_rejected():
    with pytest.raises(ValidationError):
        Proposal(type="watch", value=None, reason="x", reason_kind="notizia", importo=5)
    with pytest.raises(ValidationError):
        Proposal(type="compra", value=None, reason="x", reason_kind="notizia")


def _write_cli_inputs(tmp_path, review_text: str | None):
    snap, state = held_setup()
    (tmp_path / "snapshot.json").write_text(snap.model_dump_json(), encoding="utf-8")
    save_state(state, tmp_path / "state.json")
    if review_text is not None:
        (tmp_path / "claude_review.json").write_text(review_text, encoding="utf-8")


def _run_cli(tmp_path):
    return main(
        ["--out", str(tmp_path), "--state", str(tmp_path / "state.json")], now=NOW
    )


def test_malformed_review_falls_back(tmp_path):
    _write_cli_inputs(tmp_path, '{"generated_at": "ieri", "tickers": ')
    assert _run_cli(tmp_path) == 0
    final = json.loads((tmp_path / "final.json").read_text(encoding="utf-8"))
    assert final["review_available"] is False and final["reviews"] == {}


def test_missing_review_falls_back(tmp_path):
    _write_cli_inputs(tmp_path, None)
    assert _run_cli(tmp_path) == 0
    final = json.loads((tmp_path / "final.json").read_text(encoding="utf-8"))
    assert final["review_available"] is False
    assert final["snapshot"]["tickers"][0]["ticker"] == "AAA"


def test_cli_applies_valid_review_and_saves_state(tmp_path):
    review = review_for("AAA", prop("tighten_stop", "95"))
    _write_cli_inputs(tmp_path, review.model_dump_json())
    assert _run_cli(tmp_path) == 0
    final = json.loads((tmp_path / "final.json").read_text(encoding="utf-8"))
    assert final["review_available"] is True and len(final["applied"]) == 1
    assert load_state(tmp_path / "state.json").plans["AAA"].stop == 95.0


def test_cli_invalid_snapshot_exits_2(tmp_path):
    _write_cli_inputs(tmp_path, None)
    (tmp_path / "snapshot.json").write_text("{rotto", encoding="utf-8")
    assert _run_cli(tmp_path) == 2
    assert not (tmp_path / "final.json").exists()


# --- correzioni del primo giro di revisione -------------------------------------------


def test_tighten_stop_never_lowers_eur_stop_with_fx_move():
    # Piano creato con un cambio più favorevole (stop 90 USD = 76 EUR a 1.18);
    # oggi il cambio è 1.25: 91 USD varrebbe 72.8 EUR, ma lo stop in EUR non può scendere.
    snap, state = held_setup()
    state.plans["AAA"].stop_eur = 76.27
    snap.eurusd = 1.25
    view = apply(snap, review_for("AAA", prop("tighten_stop", "91")), state, NOW)
    assert len(view.applied) == 1
    assert state.plans["AAA"].stop == 91.0
    assert state.plans["AAA"].stop_eur == 76.27


def test_tighten_stop_keeps_existing_eur_stop_when_close_eur_missing():
    snap, state = held_setup()
    snap.tickers[0].close_eur = None
    apply(snap, review_for("AAA", prop("tighten_stop", "95")), state, NOW)
    assert state.plans["AAA"].stop_eur == 76.0  # 95 / 1.25 = 76.0, mai None


def test_scenarios_updated_after_tighten_and_shorten():
    snap, state = held_setup()
    state.plans["AAA"].scenarios = opportunity_scenarios(90.0, 76.0, 120.0, 100.0, DEADLINE, "USD")
    review = review_for(
        "AAA", prop("tighten_stop", "95.5"), prop("shorten_deadline", "2026-11-30")
    )
    view = apply(snap, review, state, NOW)
    text = " ".join(state.plans["AAA"].scenarios)
    assert "76,4" in text.replace(".", ",") and "30/11/2026" in text
    assert "Subito dopo l'acquisto" not in text and "10/12/2026" not in text
    assert len(state.plans["AAA"].scenarios) == 3
    assert (
        view.snapshot.tickers[0].signal.plan.scenarios == state.plans["AAA"].scenarios
    )


def test_view_plan_matches_state_plan():
    snap, state = held_setup()
    snap.tickers[0].signal.plan = make_plan(stop=80.0)  # copia disallineata dallo stato
    view = apply(
        snap, review_for("AAA", prop("tighten_stop", "95"), prop("watch")), state, NOW
    )
    assert view.snapshot.tickers[0].signal.plan == state.plans["AAA"]
    assert view.snapshot.tickers[0].signal.plan is not state.plans["AAA"]
    degraded = apply(snap, None, state, NOW)
    assert degraded.snapshot.tickers[0].signal.plan == state.plans["AAA"]


def test_extend_cumulative_cap_30_days():
    snap, state = held_setup()
    first = (DEADLINE + timedelta(days=20)).isoformat()
    second = (DEADLINE + timedelta(days=31)).isoformat()
    third = (DEADLINE + timedelta(days=30)).isoformat()
    view = apply(
        snap,
        review_for(
            "AAA",
            prop("extend_deadline", first, kind="evento"),
            prop("extend_deadline", second, kind="evento"),
            prop("extend_deadline", third, kind="evento"),
        ),
        state,
        NOW,
    )
    assert [r.accepted for r in state.revisions] == [True, False, True]
    assert state.plans["AAA"].original_deadline == DEADLINE
    assert state.plans["AAA"].deadline == DEADLINE + timedelta(days=30)
    assert len(view.rejected) == 1


@pytest.mark.parametrize("action", [Action.VENDI, Action.RIDUCI, Action.DATI_MANCANTI])
def test_request_review_does_not_override_stronger_actions(action):
    snap, state = held_setup()
    snap.tickers[0].signal.action = action
    view = apply(snap, review_for("AAA", prop("request_review")), state, NOW)
    assert len(view.rejected) == 1 and view.snapshot.tickers[0].signal.action == action


def test_request_review_clears_amount():
    snap, state = held_setup()
    snap.tickers[0].signal.amount_eur = 100.0
    view = apply(snap, review_for("AAA", prop("request_review")), state, NOW)
    assert view.snapshot.tickers[0].signal.amount_eur is None


def test_downgrade_buy_clears_kind_and_plan():
    snap = make_snapshot(make_ticker("AAA", action=Action.COMPRA, plan=make_plan()))
    snap.tickers[0].signal.kind = PositionKind.opportunity
    view = apply(snap, review_for("AAA", prop("downgrade_buy")), State(), NOW)
    sig = view.snapshot.tickers[0].signal
    assert sig.kind is None and sig.plan is None


@pytest.mark.parametrize("kind", ["shorten_deadline", "extend_deadline"])
def test_deadline_proposals_rejected_when_plan_has_no_deadline(kind):
    snap, state = held_setup(deadline=None)
    view = apply(
        snap, review_for("AAA", prop(kind, "2026-12-01", kind="evento")), state, NOW
    )
    assert len(view.rejected) == 1 and state.plans["AAA"].deadline is None


def test_future_review_is_ignored():
    snap, state = held_setup()
    review = review_for("AAA", prop("tighten_stop", "95"))
    review.generated_at = NOW + timedelta(minutes=11)
    assert not apply(snap, review, state, NOW).review_available
    review.generated_at = NOW + timedelta(minutes=5)
    assert apply(snap, review, state, NOW).review_available


def test_duplicate_ticker_keeps_first():
    snap, state = held_setup()
    review = review_for("AAA", prop("tighten_stop", "95"))
    review.tickers.append(review_for("AAA", prop("tighten_stop", "97")).tickers[0])
    view = apply(snap, review, state, NOW)
    assert state.plans["AAA"].stop == 95.0
    assert len(view.applied) == 1 and len(view.rejected) == 1
    assert "ticker duplicato" in view.rejected[0].reason


def test_journal_close_is_none_when_close_missing():
    snap = make_snapshot(make_ticker("AAA", close=None))
    state = State()
    apply(snap, review_for("AAA", event_class="legale"), state, NOW)
    assert state.journal[0].close is None


def test_scenarios_move_order_for_open_position():
    """I4a: per una posizione aperta lo stop alzato si sposta, non si "inserisce dopo l'acquisto"."""
    snap, state = held_setup()
    state.plans["AAA"].scenarios = [open_position_stop_line(76.0)]
    apply(snap, review_for("AAA", prop("tighten_stop", "95.5")), state, NOW)
    first = state.plans["AAA"].scenarios[0]
    # 95,5 USD / 1,25 = 76,40 € (per difetto)
    assert first.startswith("Sposta il tuo ordine stop-loss su Trade Republic da 76,00 € a 76,40 €")
    assert not any("Subito dopo l'acquisto" in x for x in state.plans["AAA"].scenarios)

    # una data limite più vicina non tocca lo stop: la riga dello stop resta quella di prima
    later = NOW + timedelta(hours=12)
    review = review_for("AAA", prop("shorten_deadline", "2026-11-30")).model_copy(
        update={"generated_at": later - timedelta(hours=1)}
    )
    apply(snap, review, state, later)
    assert state.plans["AAA"].scenarios[0] == first

    # piano vecchio con il testo per un nuovo acquisto: rigenerato come "lascia l'ordine"
    snap, state = held_setup()
    state.plans["AAA"].scenarios = opportunity_scenarios(90.0, 76.0, 120.0, 100.0, DEADLINE, "USD")
    apply(snap, review_for("AAA", prop("shorten_deadline", "2026-11-30")), state, NOW)
    assert state.plans["AAA"].scenarios[0].startswith(
        "Lascia il tuo ordine stop-loss su Trade Republic a 76,00 €"
    )


def test_fresh_scenarios_keep_extra_notes():
    snap, state = held_setup()
    state.plans["AAA"].scenarios = [
        *opportunity_scenarios(90.0, 76.0, 120.0, 100.0, DEADLINE, "USD"),
        CONVERTED_ENTRY_REASON,
    ]
    review = review_for("AAA", prop("extend_deadline", "2026-12-20", kind="evento"))
    view = apply(snap, review, state, NOW)
    scenarios = state.plans["AAA"].scenarios
    assert scenarios.count(CONVERTED_ENTRY_REASON) == 1
    assert scenarios[-1] == CONVERTED_ENTRY_REASON
    assert any("20/12/2026" in x for x in scenarios)
    assert not any("10/12/2026" in x for x in scenarios)
    assert view.snapshot.tickers[0].signal.plan.scenarios == scenarios
