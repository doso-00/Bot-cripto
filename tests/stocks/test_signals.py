"""Test del motore delle regole: un test per regola più i blocchi e i piani."""

from datetime import date, timedelta

import pytest

from stocks.config import StocksSettings
from stocks.models import (
    Action,
    Fundamentals,
    Holding,
    Plan,
    PositionKind,
    SizingContext,
)
from stocks.signals import build_plan, evaluate, quality_check
from tests.stocks.signal_data import (
    ASOF,
    EURUSD,
    bad_fundamentals,
    core_pullback_closes,
    earnings_in,
    free_ctx,
    good_fundamentals,
    make_prices,
    no_earnings,
    opportunity_closes,
    rising,
    rr_rejected_closes,
)

A = Action
K = PositionKind


@pytest.fixture
def s():
    return StocksSettings()


def run(
    prices, s, f=None, e=None, holding=None, plan=None, ctx=None, asof=ASOF, div_years=0
):
    return evaluate(
        prices,
        f or good_fundamentals(),
        div_years,
        e or no_earnings(),
        holding,
        plan,
        asof,
        ctx or free_ctx(),
        EURUSD,
        s,
    )


def core_holding(avg: float = 100.0) -> Holding:
    return Holding(ticker="XYZ", kind=K.core, qty=3, avg_price=avg, currency="USD")


# --- quality_check -----------------------------------------------------------


def test_quality_check_thresholds(s):
    ok, reasons = quality_check(good_fundamentals(), 6, s)
    assert ok is True
    assert any("6 anni" in r for r in reasons)  # i dividendi sono un plus nei motivi

    ok, _ = quality_check(good_fundamentals(), 0, s)  # senza dividendi resta ok
    assert ok is True

    # debito e payout assenti non bloccano
    ok, _ = quality_check(Fundamentals(trailing_pe=10.0, roe=0.08), 0, s)
    assert ok is True

    for f in (
        Fundamentals(trailing_pe=None, roe=0.2),
        Fundamentals(trailing_pe=-3.0, roe=0.2),
        Fundamentals(trailing_pe=10.0, roe=0.079),
        Fundamentals(trailing_pe=10.0, roe=None),
        Fundamentals(trailing_pe=10.0, roe=0.2, debt_to_equity=150.1),
        Fundamentals(trailing_pe=10.0, roe=0.2, payout_ratio=0.81),
    ):
        ok, reasons = quality_check(f, 10, s)
        assert ok is False, f
        assert reasons


# --- Regola 1 ----------------------------------------------------------------


def test_rule1_empty_or_stale_prices_give_missing_data(s):
    empty = make_prices([])
    assert run(empty, s).action == A.DATI_MANCANTI

    # ultima barra 6 giorni prima di asof (venerdì 6/11 vs giovedì 12/11): dati vecchi
    stale = make_prices(core_pullback_closes(), end=date(2026, 11, 6))
    sig = run(stale, s, holding=core_holding())
    assert sig.action == A.DATI_MANCANTI
    assert sig.reasons
    assert sig.plan is None and sig.amount_eur is None

    # 5 giorni esatti sono ancora accettati
    fresh = make_prices(core_pullback_closes(), end=date(2026, 11, 6))
    assert run(fresh, s, asof=date(2026, 11, 11)).action == A.COMPRA

    # valuta sconosciuta: impossibile convertire i livelli in euro
    no_ccy = run(
        make_prices(core_pullback_closes()), s, f=good_fundamentals(currency=None)
    )
    assert no_ccy.action == A.DATI_MANCANTI


# --- Regola 2 ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("last_close", "expected"),
    [
        (79.0, A.VENDI),
        (80.0, A.VENDI),
        (84.0, A.RIVEDI),
        (85.0, A.RIVEDI),
        (85.5, A.TIENI),
    ],
)
def test_rule2_core_holding_thresholds(s, last_close, expected):
    prices = make_prices(rising(240, 90.0, 110.0) + [last_close])
    sig = run(prices, s, holding=core_holding(100.0))
    assert sig.action == expected
    assert sig.kind == K.core
    assert sig.amount_eur is None


def test_rule2_core_hold_mentions_upcoming_earnings(s):
    prices = make_prices(rising(240, 90.0, 110.0))
    plain = run(prices, s, holding=core_holding(100.0))
    soon = run(prices, s, holding=core_holding(100.0), e=earnings_in(3))
    # posseduto: la regola 2 vince sulla regola 4 (niente ATTENDI)
    assert plain.action == soon.action == A.TIENI
    assert len(soon.reasons) == len(plain.reasons) + 1
    assert "trimestrale" in soon.reasons[-1].lower()


# --- Regola 3 ----------------------------------------------------------------


def _opp_plan(deadline: date) -> Plan:
    return Plan(
        kind=K.opportunity,
        entry_ref=100.0,
        stop=92.0,
        target=120.0,
        deadline=deadline,
        currency="USD",
        stop_order_on_tr=True,
    )


@pytest.mark.parametrize(
    ("last_close", "deadline", "expected", "phrase"),
    [
        (91.0, ASOF + timedelta(days=10), A.VENDI, "stop raggiunto"),
        (92.0, ASOF + timedelta(days=10), A.VENDI, "stop raggiunto"),
        (121.0, ASOF + timedelta(days=10), A.VENDI, "obiettivo raggiunto"),
        (100.0, ASOF - timedelta(days=1), A.VENDI, "scadenza"),
        (100.0, ASOF, A.TIENI, None),  # il giorno della scadenza si tiene ancora
    ],
)
def test_rule3_opportunity_holding_exits(s, last_close, deadline, expected, phrase):
    holding = Holding(
        ticker="XYZ", kind=K.opportunity, qty=2, avg_price=100.0, currency="USD"
    )
    plan = _opp_plan(deadline)
    sig = run(
        make_prices(rising(240, 90.0, 110.0) + [last_close]),
        s,
        holding=holding,
        plan=plan,
    )
    assert sig.action == expected
    assert sig.kind == K.opportunity
    assert sig.plan == plan
    if phrase:
        assert phrase in sig.reasons[0].lower()


def test_rule3_event_holding_uses_same_rule(s):
    holding = Holding(
        ticker="XYZ", kind=K.event, qty=1, avg_price=100.0, currency="USD"
    )
    plan = _opp_plan(ASOF + timedelta(days=10)).model_copy(update={"kind": K.event})
    sig = run(
        make_prices(rising(240, 90.0, 110.0) + [91.0]), s, holding=holding, plan=plan
    )
    assert sig.action == A.VENDI


# --- Regola 4 ----------------------------------------------------------------


@pytest.mark.parametrize("days", [5, 3, 0, -1])
def test_rule4_earnings_soon_gives_wait(s, days):
    # anche con un ingresso core valido: nessun acquisto prima dei risultati
    sig = run(make_prices(core_pullback_closes()), s, e=earnings_in(days))
    assert sig.action == A.ATTENDI
    assert sig.kind == K.event
    assert sig.amount_eur is None
    joined = " ".join(sig.reasons)
    assert "9,5%" in joined and "4,0%" in joined


def test_rule4_not_applied_outside_window(s):
    sig = run(make_prices(core_pullback_closes()), s, e=earnings_in(6))
    assert sig.action == A.COMPRA


def test_event_plan_has_two_scenarios(s):
    sig = run(make_prices(core_pullback_closes()), s, e=earnings_in(3))
    plan = sig.plan
    assert plan is not None
    assert plan.kind == K.event
    assert plan.stop is None and plan.target is None
    assert plan.stop_eur is None and plan.target_eur is None
    assert len(plan.scenarios) == 2
    up, down = plan.scenarios
    assert up.startswith("Se i risultati battono le attese e il titolo regge:")
    assert down.startswith("Se deludono:")
    for text in plan.scenarios:
        assert "9,5%" in text and "4,0%" in text

    missing = run(
        make_prices(core_pullback_closes()),
        s,
        e=earnings_in(3, expected=None, avg=None),
    )
    for text in missing.plan.scenarios:
        assert "non disponibil" in text


# --- Regola 5 ----------------------------------------------------------------


def test_rule5_core_entry(s):
    sig = run(make_prices(core_pullback_closes()), s, div_years=6)
    assert sig.action == A.COMPRA
    assert sig.kind == K.core
    assert sig.amount_eur == pytest.approx(300.0)  # 30% di 1000 €
    plan = sig.plan
    assert plan.kind == K.core
    assert plan.entry_ref == pytest.approx(92.0)
    assert plan.review_level == pytest.approx(92.0 * 0.85)
    assert plan.exit_level == pytest.approx(92.0 * 0.80)
    assert plan.deadline is None
    assert plan.stop_order_on_tr is False
    joined = " ".join(sig.reasons)
    assert "sceso dell'8,0%" in joined
    assert "6–24 mesi" in joined
    assert "6 anni" in joined


def test_rule5_requires_quality(s):
    sig = run(make_prices(core_pullback_closes()), s, f=bad_fundamentals())
    assert sig.action == A.NESSUNA_AZIONE


# --- Regola 6 ----------------------------------------------------------------


def test_rule6_opportunity_entry(s):
    sig = run(make_prices(opportunity_closes(), spread=3.0), s)
    assert sig.action == A.COMPRA
    assert sig.kind == K.opportunity
    assert sig.amount_eur == pytest.approx(200.0)  # 20% del budget
    plan = sig.plan
    # ATR = 6 (high-low costante); stop = max(80 - 12, 80 * 0.92) = 73.6
    assert plan.stop == pytest.approx(73.6)
    # obiettivo = min(80 + 24, massimo a 63 giorni 100) = 100
    assert plan.target == pytest.approx(100.0)
    assert plan.deadline == ASOF + timedelta(days=30)
    assert plan.stop_order_on_tr is True
    assert plan.review_level is None and plan.exit_level is None
    # rapporto (100 - 80 - 0,8) / 6,4 = 3,0
    assert any("3,0" in r for r in sig.reasons)


def test_opportunity_rejected_when_reward_risk_below_2(s):
    # close 89: stop = max(77, 81.88) = 81.88; obiettivo = 100;
    # costi = 2 € × 89 / 200 € = 0,89 per azione -> (11 - 0,89) / 7,12 = 1,42 < 2
    sig = run(make_prices(rr_rejected_closes(), spread=3.0), s, f=bad_fundamentals())
    assert sig.action == A.NESSUNA_AZIONE
    assert sig.plan is None
    assert any("obiettivo/rischio" in r and "1,4" in r for r in sig.reasons)


def test_opportunity_costs_use_allowed_order_amount(s):
    # Occasioni già a 100 €: restano 100 € -> costi per azione 2 € × 80 / 100 € = 1,6
    # (il doppio che con 200 €) -> rapporto (20 - 1,6) / 6,4 = 2,875.
    ctx = SizingContext(holdings_eur={K.opportunity: 100.0}, open_opps=1, halted=False)
    sig = run(make_prices(opportunity_closes(), spread=3.0), s, ctx=ctx)
    assert sig.action == A.COMPRA
    assert sig.amount_eur == pytest.approx(100.0)
    assert any("2,9" in r for r in sig.reasons)


# --- Regola 7 ----------------------------------------------------------------


def test_rule7_no_action_with_main_reason(s):
    at_high = run(make_prices(rising()), s)
    assert at_high.action == A.NESSUNA_AZIONE
    assert at_high.plan is None and at_high.amount_eur is None
    assert any("massimo" in r for r in at_high.reasons)

    falling = run(make_prices(rising(240, 150.0, 100.0) + [95.0]), s)
    assert falling.action == A.NESSUNA_AZIONE
    assert any("200 giorni" in r for r in falling.reasons)

    short = run(make_prices(rising(50, 50.0, 100.0) + [92.0]), s)
    assert short.action == A.NESSUNA_AZIONE
    assert any("200 giorni" in r for r in short.reasons)


# --- Blocchi dopo le regole --------------------------------------------------


def test_halt_blocks_buy(s):
    ctx = SizingContext(holdings_eur={}, open_opps=0, halted=True)
    sig = run(make_prices(core_pullback_closes()), s, ctx=ctx)
    assert sig.action == A.NESSUNA_AZIONE
    assert sig.blocked_by == "portafoglio in calo del 15%: nuovi acquisti bloccati"
    assert sig.amount_eur is None and sig.plan is None


def test_min_order_blocks_buy(s):
    # base già a 750 €: restano 50 € sotto l'80% -> sotto il minimo di 100 €
    ctx = SizingContext(holdings_eur={K.core: 750.0}, open_opps=0, halted=False)
    sig = run(make_prices(core_pullback_closes()), s, ctx=ctx)
    assert sig.action == A.NESSUNA_AZIONE
    assert sig.blocked_by == "importo sotto il minimo di 100 €"
    assert sig.amount_eur is None and sig.plan is None

    ctx = SizingContext(holdings_eur={}, open_opps=2, halted=False)
    sig = run(make_prices(opportunity_closes(), spread=3.0), s, ctx=ctx)
    assert sig.action == A.NESSUNA_AZIONE
    assert sig.blocked_by == "già 2 occasioni aperte (massimo 2)"


def test_buy_never_validated_in_phase_a(s):
    core = run(make_prices(core_pullback_closes()), s)
    opp = run(make_prices(opportunity_closes(), spread=3.0), s)
    assert core.action == opp.action == A.COMPRA
    assert core.validated is False and opp.validated is False


def test_stop_eur_rounded_down(s):
    sig = run(make_prices(opportunity_closes(), spread=3.0), s)
    # 73,6 / 1,17 = 62,9059... -> 62,90 (per difetto); 100 / 1,17 = 85,4700... -> 85,48 (per eccesso)
    assert sig.plan.stop_eur == 62.90
    assert sig.plan.target_eur == 85.48

    # in EUR un livello già a 2 decimali non perde un centesimo per errori di virgola mobile
    eur = run(
        make_prices(opportunity_closes(), spread=3.0), s, f=good_fundamentals("EUR")
    )
    assert eur.plan.stop == pytest.approx(73.6)
    assert eur.plan.stop_eur == 73.60
    assert eur.plan.target_eur == 100.00


# --- build_plan --------------------------------------------------------------


def test_build_plan_core_levels(s):
    plan = build_plan(K.core, 200.0, make_prices(rising()), ASOF, "USD", EURUSD, s)
    assert plan.kind == K.core
    assert plan.entry_ref == 200.0
    assert plan.review_level == pytest.approx(170.0)
    assert plan.exit_level == pytest.approx(160.0)
    assert plan.stop is None and plan.target is None and plan.deadline is None
    assert plan.stop_order_on_tr is False
    assert plan.currency == "USD"


def test_build_plan_opportunity_levels(s):
    flat = make_prices([100.0] * 80, spread=1.0)  # ATR = 2, massimo 63g = 100
    plan = build_plan(K.opportunity, 95.0, flat, ASOF, "EUR", EURUSD, s)
    assert plan.stop == pytest.approx(91.0)  # max(95 - 4, 95 × 0.92 = 87.4)
    assert plan.target == pytest.approx(100.0)  # min(95 + 8, 100)
    assert plan.deadline == ASOF + timedelta(days=30)
    assert plan.stop_order_on_tr is True
    assert plan.stop_eur == 91.00 and plan.target_eur == 100.00

    wide = make_prices([100.0] * 80, spread=5.0)  # ATR = 10: lo stop è limitato a −8%
    event = build_plan(K.event, 95.0, wide, ASOF, "EUR", EURUSD, s)
    assert event.kind == K.event
    assert event.stop == pytest.approx(87.4)
    assert event.target == pytest.approx(100.0)
    assert event.stop_order_on_tr is True

    # le barre dopo asof non entrano nel calcolo
    later = make_prices(
        [100.0] * 80 + [200.0], end=ASOF + timedelta(days=1), spread=1.0
    )
    assert build_plan(K.opportunity, 95.0, later, ASOF, "EUR", EURUSD, s) == plan

    with pytest.raises(ValueError):
        build_plan(
            K.opportunity, 95.0, make_prices([100.0] * 5), ASOF, "EUR", EURUSD, s
        )
