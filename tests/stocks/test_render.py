"""Test della pagina HTML (stocks.render). Nessuna rete: viste costruite a mano."""

import re
from datetime import UTC, date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from stocks.apply_review import FinalView
from stocks.config import StocksSettings
from stocks.models import (
    Action,
    EarningsInfo,
    Fundamentals,
    Plan,
    PortfolioSummary,
    PositionKind,
    Signal,
    Snapshot,
    TickerSnapshot,
)
from stocks.render import main, render_html
from stocks.review import GlossaryItem, NewsItem, TickerReview
from stocks.state import PlanRevision, State

ROME = ZoneInfo("Europe/Rome")
NOW = datetime(2026, 11, 12, 23, 10, tzinfo=ROME)
TODAY = date(2026, 11, 12)
SETTINGS = StocksSettings()
DISCLAIMER = "Strumento informativo, non consulenza finanziaria. Gli ordini li decidi e li esegui tu."


def make_ticker(
    ticker: str = "AAA",
    action: Action = Action.NESSUNA_AZIONE,
    **kw,
) -> TickerSnapshot:
    fields = {
        "ticker": ticker,
        "name": ticker,
        "currency": "USD",
        "proposed_by": "riccardo",
        "asof": TODAY,
        "close": 100.0,
        "close_eur": 80.0,
        "change_1d_pct": 0.012,
        "drawdown_63d_pct": 0.08,
        "fundamentals": Fundamentals(),
        "earnings": EarningsInfo(),
        "signal": Signal(action=action, reasons=["motivo del codice"]),
        "data_ok": True,
    }
    fields.update(kw)
    return TickerSnapshot(**fields)


def make_view(
    *tickers: TickerSnapshot,
    generated_at: datetime = NOW - timedelta(minutes=5),
    review_available: bool = True,
    **kw,
) -> FinalView:
    snapshot = Snapshot(
        generated_at=generated_at,
        run_slot="sera",
        eurusd=1.25,
        benchmarks={"^GSPC": 0.004},
        tickers=list(tickers) or [make_ticker()],
        portfolio=PortfolioSummary(
            invested_eur=0,
            value_eur=0,
            pnl_net_eur=0,
            cash_eur=1000,
            halted=False,
            peak_value_eur=1000,
        ),
    )
    return FinalView(snapshot=snapshot, review_available=review_available, **kw)


def render(view: FinalView, state: State | None = None) -> str:
    return render_html(view, state or State(), SETTINGS, now=NOW)


def opportunity_plan(stop_eur: float = 76.0) -> Plan:
    return Plan(
        kind=PositionKind.opportunity,
        entry_ref=100.0,
        stop=95.0,
        target=120.0,
        deadline=date(2026, 12, 12),
        original_deadline=date(2026, 12, 12),
        currency="USD",
        stop_eur=stop_eur,
        target_eur=96.0,
        scenarios=["scenario uno"],
        stop_order_on_tr=True,
    )


def test_render_contains_disclaimer():
    html = render(make_view())
    assert DISCLAIMER in html
    assert "<title>Dashboard azioni</title>" in html
    # Il guscio della pagina lo aggiunge la pubblicazione dell'Artifact.
    assert not re.search(r"<(!doctype|html|head|body)[\s>]", html, re.IGNORECASE)


def test_buy_has_not_validated_label():
    buy = make_ticker(
        "MSFT",
        Action.COMPRA,
        signal=Signal(
            action=Action.COMPRA,
            kind=PositionKind.core,
            amount_eur=150.0,
            reasons=["ritracciamento"],
        ),
    )
    html = render(make_view(buy))
    assert "regola non ancora validata" in html
    assert "150,00 €" in html

    no_buy = render(make_view(make_ticker("KO", Action.TIENI)))
    assert "regola non ancora validata" not in no_buy


def test_claude_text_is_escaped():
    evil = "<script>alert(1)</script>"
    review = TickerReview(
        ticker="AAA",
        event_class="macro",
        summary=evil,
        explanation=f"spiegazione {evil}",
        news=[NewsItem(title=evil, url="https://example.com/n", source=evil)],
    )
    rejected = PlanRevision(
        ts=NOW,
        ticker="AAA",
        field="stop",
        old=None,
        new=evil,
        source="claude",
        reason=f"motivo {evil}",
        accepted=False,
    )
    view = make_view(
        make_ticker(
            "AAA", name=evil, signal=Signal(action=Action.TIENI, reasons=[evil])
        ),
        reviews={"AAA": review},
        rejected=[rejected],
        glossary=[GlossaryItem(term=evil, definition=evil)],
        market_note=evil,
    )
    html = render(view, State(revisions=[rejected]))
    assert "&lt;script&gt;" in html
    assert "<script" not in html


def test_non_https_links_dropped():
    review = TickerReview(
        ticker="AAA",
        event_class="nessuno",
        summary="sintesi",
        explanation="spiegazione",
        news=[
            NewsItem(title="Buona", url="https://example.com/ok", source="Reuters"),
            NewsItem(title="In chiaro", url="http://example.com/plain", source="X"),
            NewsItem(title="Trappola", url="javascript:alert(1)", source="Y"),
        ],
    )
    html = render(make_view(make_ticker("AAA"), reviews={"AAA": review}))
    assert 'href="https://example.com/ok"' in html
    assert "http://example.com/plain" not in html
    assert "javascript:" not in html
    # Il titolo resta, senza link.
    assert "In chiaro" in html and "Trappola" in html


def test_review_unavailable_banner():
    assert "Revisione notizie non disponibile" in render(
        make_view(review_available=False)
    )
    assert "Revisione notizie non disponibile" not in render(make_view())


def test_data_missing_banner():
    missing = make_ticker(
        "ZZZ",
        Action.DATI_MANCANTI,
        currency=None,
        close=None,
        close_eur=None,
        change_1d_pct=None,
        drawdown_63d_pct=None,
        signal=Signal(action=Action.DATI_MANCANTI, reasons=["Yahoo non risponde"]),
        data_ok=False,
        data_issues=["HTTP 429"],
    )
    html = render(make_view(make_ticker("AAA"), missing))
    assert "Dati mancanti" in html
    assert "ZZZ" in html
    assert "n.d." in html
    assert "Dati mancanti" not in render(make_view(make_ticker("AAA")))


def test_stop_order_hint_for_opportunity():
    buy = make_ticker(
        "NVDA",
        Action.COMPRA,
        signal=Signal(
            action=Action.COMPRA,
            kind=PositionKind.opportunity,
            amount_eur=100.0,
            plan=opportunity_plan(76.0),
            reasons=["occasione"],
        ),
    )
    html = render(make_view(buy))
    assert "Inserisci su Trade Republic un ordine stop-loss a 76,00 €" in html


def test_times_shown_in_rome_tz():
    utc_ts = datetime(2026, 11, 12, 22, 4, tzinfo=UTC)
    earnings = EarningsInfo(
        next_ts=datetime(2026, 11, 17, 16, 0, tzinfo=timezone(timedelta(hours=-5))),
        days_to_next=5,
    )
    html = render(
        make_view(make_ticker("NVDA", earnings=earnings), generated_at=utc_ts)
    )
    assert "12/11/2026 23:04" in html
    assert "22:04" not in html
    # Trimestrale alle 16:00 di New York = 22:00 a Roma.
    assert "17/11/2026" in html and "22:00" in html


def test_age_and_stale_banner():
    fresh = render(make_view(generated_at=NOW - timedelta(hours=3)))
    assert "aggiornata 3 ore fa" in fresh
    assert "Dati non aggiornati" not in fresh
    stale = render(make_view(generated_at=NOW - timedelta(hours=19)))
    assert "Dati non aggiornati" in stale


def test_blocked_buy_shows_reason_first_and_is_not_a_buy():
    blocked = make_ticker(
        "MSFT",
        signal=Signal(
            action=Action.NESSUNA_AZIONE,
            kind=PositionKind.core,
            reasons=["MOTIVO_ACQUISTO"],
            blocked_by="BLOCCO_RISCHIO",
        ),
    )
    html = render(make_view(blocked))
    assert html.index("BLOCCO_RISCHIO") < html.index("MOTIVO_ACQUISTO")
    assert "regola non ancora validata" not in html
    assert ">COMPRA<" not in html


def test_claude_proposals_marked_to_confirm():
    html = render(make_view(make_ticker("KO", proposed_by="claude")))
    assert "proposto da Claude, da confermare" in html


def test_calendar_sorted_by_date():
    late = make_ticker(
        "LATE",
        earnings=EarningsInfo(
            next_ts=datetime(2027, 1, 27, 16, 0, tzinfo=ROME), days_to_next=76
        ),
    )
    early = make_ticker(
        "EARLY", fundamentals=Fundamentals(ex_dividend_date=date(2026, 11, 20))
    )
    html = render(make_view(late, early))
    cal = html[html.index('id="calendario"') :]
    assert cal.index("20/11/2026") < cal.index("27/01/2027")
    assert "stacco dividendo" in cal.lower()


def test_cli_writes_dashboard(tmp_path):
    (tmp_path / "final.json").write_text(
        make_view().model_dump_json(), encoding="utf-8"
    )
    assert main(["--out", str(tmp_path), "--state", str(tmp_path / "state.json")]) == 0
    assert DISCLAIMER in (tmp_path / "dashboard.html").read_text(encoding="utf-8")


def test_cli_fails_without_final_view(tmp_path):
    assert main(["--out", str(tmp_path), "--state", str(tmp_path / "state.json")]) == 2


@pytest.mark.parametrize("value", [None, 0.0])
def test_none_and_zero_values_do_not_crash(value):
    t = make_ticker(
        "AAA",
        close=value,
        close_eur=value,
        change_1d_pct=value,
        drawdown_63d_pct=value,
        sma200=value,
        atr14=value,
        rsi14=value,
    )
    assert DISCLAIMER in render(make_view(t))
