"""Catena completa refresh → apply_review → render con il client Yahoo finto (nessuna rete)."""

import json
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from stocks import apply_review, render
from stocks.config import StocksSettings, WatchItem, Watchlist
from stocks.refresh import run_refresh
from stocks.review import ClaudeReview, Proposal, TickerReview
from tests.stocks.fakes import FakeTicker, FakeYF, make_chain, make_earnings, make_history
from tests.stocks.signal_data import rising

ROME = ZoneInfo("Europe/Rome")
NOW = datetime(2026, 11, 12, 23, 17, tzinfo=ROME)
PROMPT = Path(__file__).resolve().parents[2] / "stocks" / "PROMPT_REFRESH.md"
EVIL = "<b>Risultati</b> & attese"


def _days(n: int, end: str) -> list[str]:
    return [d.strftime("%Y-%m-%d") for d in pd.bdate_range(end=end, periods=n)]


def _client() -> FakeYF:
    closes = rising()
    nvda = FakeTicker(
        history=make_history(_days(len(closes), "2026-11-12"), closes),
        info={
            "currency": "USD", "longName": "NVIDIA Corporation", "trailingPE": 25.0,
            "returnOnEquity": 0.20, "debtToEquity": 40.0, "payoutRatio": 0.30,
        },
        earnings=make_earnings(
            [("2026-11-17 16:20", 1.25, None, None), ("2026-08-27 16:20", 1.0, 1.1, 10.0)]
        ),
        options=("2026-11-20",),
        chains={"2026-11-20": make_chain([(100.0, 4.0, 5.0, 4.5)], [(100.0, 4.0, 5.0, 4.5)])},
    )
    return FakeYF(
        {
            "EURUSD=X": FakeTicker(history=make_history(_days(3, "2026-11-12"), [1.16, 1.17, 1.18], tz="UTC")),
            "^GSPC": FakeTicker(history=make_history(_days(2, "2026-11-12"), [6000.0, 6060.0])),
            "NVDA": nvda,
        }
    )


def test_refresh_review_render(tmp_path):
    out, state_path = tmp_path / "out", tmp_path / "out" / "state.json"
    watchlist = Watchlist(
        items=[WatchItem(ticker="NVDA", proposed_by="riccardo")], benchmarks={"US": "^GSPC"}
    )
    snap = run_refresh(NOW, state_path, out, StocksSettings(), watchlist, client=_client(), run_slot="sera")
    assert snap.tickers[0].signal.action.value == "ATTENDI"

    review = ClaudeReview(
        generated_at=NOW + timedelta(minutes=5),
        tickers=[
            TickerReview(
                ticker="NVDA",
                event_class="macro",
                summary=EVIL,
                explanation="Spiegazione per un principiante.",
                proposals=[Proposal(type="watch", reason="trimestrale vicina", reason_kind="evento")],
            )
        ],
    )
    (out / "claude_review.json").write_text(review.model_dump_json(), encoding="utf-8")
    later = NOW + timedelta(minutes=10)
    args = ["--state", str(state_path), "--out", str(out)]

    assert apply_review.main(args, now=later) == 0
    assert render.main(args, now=later) == 0

    page = (out / "dashboard.html").read_text(encoding="utf-8")
    assert "NVDA" in page
    assert ">ATTENDI<" in page
    assert "&lt;b&gt;Risultati&lt;/b&gt; &amp; attese" in page
    assert EVIL not in page
    assert "Revisione notizie non disponibile" not in page
    assert "da guardare" in page
    final = json.loads((out / "final.json").read_text(encoding="utf-8"))
    assert final["review_available"] is True and final["watched"] == ["NVDA"]


def _push_script() -> str:
    """Script Python della sezione 8 di PROMPT_REFRESH.md (avviso sul telefono)."""
    section = PROMPT.read_text(encoding="utf-8").split("## 8.")[1].split("## 9.")[0]
    return re.search(r"<<'EOF'\n(.*?)\nEOF", section, flags=re.DOTALL).group(1)


def test_push_script_accepts_naive_timestamps(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    run_ts = "2026-11-18T23:17:00+01:00"
    (out / "snapshot.json").write_text(json.dumps({"generated_at": run_ts}), encoding="utf-8")
    journal = [
        {"ts": run_ts, "ticker": "NVDA", "action": "COMPRA", "source": "engine"},
        # riga senza fuso: vale come ora italiana, non deve far fallire lo script
        {"ts": "2026-11-16T23:20:00", "ticker": "NVDA", "action": "ATTENDI", "source": "claude",
         "event_class": "trimestrale_sopra_attese"},
    ]
    (out / "state.json").write_text(json.dumps({"journal": journal}), encoding="utf-8")
    view = {"reviews": {"NVDA": {"event_class": "trimestrale_sopra_attese"}}}
    (out / "final.json").write_text(json.dumps(view), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, "-c", _push_script()], cwd=tmp_path, capture_output=True, text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["NVDA COMPRA azione nuova"]  # risultati già annunciati
