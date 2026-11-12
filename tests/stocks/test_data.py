from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from stocks.data import (
    dividend_years_without_cut,
    drop_incomplete_bar,
    fetch_fundamentals,
    fetch_history,
    to_eur,
    with_retry,
)
from tests.stocks.fakes import FakeTicker, FakeYF, make_dividends, make_history

ROME = ZoneInfo("Europe/Rome")


def test_retry_succeeds_on_third_attempt():
    calls = []
    sleeps = []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise RuntimeError("429")
        return "ok"

    assert with_retry(flaky, sleep=sleeps.append) == "ok"
    assert len(calls) == 3
    assert sleeps == [2.0, 4.0]


def test_retry_raises_after_four():
    calls = []
    sleeps = []

    def always_fails():
        calls.append(1)
        raise RuntimeError(f"errore {len(calls)}")

    with pytest.raises(RuntimeError, match="errore 4"):
        with_retry(always_fails, sleep=sleeps.append)
    assert len(calls) == 4
    assert sleeps == [2.0, 4.0, 8.0]


def test_history_normalized_columns_and_date_index():
    hist = make_history(["2026-11-09", "2026-11-10"], [100.0, 101.0])
    client = FakeYF({"NVDA": FakeTicker(history=hist)})
    now = datetime(2026, 11, 12, 23, 30, tzinfo=ROME)

    df = fetch_history("NVDA", 5, now, client=client)

    assert list(df.columns) == ["open", "high", "low", "close", "adj_close", "volume"]
    assert df.index.name == "date"
    assert list(df.index) == [date(2026, 11, 9), date(2026, 11, 10)]
    assert df["close"].iloc[-1] == 101.0
    call = client.tickers["NVDA"].history_calls[0]
    assert call["period"] == "5y"
    assert call["interval"] == "1d"
    assert call["auto_adjust"] is False


def test_history_empty_when_yahoo_returns_nothing():
    client = FakeYF({"DEAD": FakeTicker()})
    now = datetime(2026, 11, 12, 23, 30, tzinfo=ROME)

    df = fetch_history("DEAD", 5, now, client=client)

    assert df.empty
    assert list(df.columns) == ["open", "high", "low", "close", "adj_close", "volume"]


def test_history_drops_rows_with_nan_close():
    hist = make_history(["2026-11-10", "2026-11-11"], [100.0, float("nan")])
    client = FakeYF({"NVDA": FakeTicker(history=hist)})
    now = datetime(2026, 11, 12, 23, 30, tzinfo=ROME)

    df = fetch_history("NVDA", 5, now, client=client)

    assert list(df.index) == [date(2026, 11, 10)]


def test_drop_incomplete_bar_us_during_session():
    # 18:00 a Roma = 12:00 a New York: la barra del 12/11 è ancora aperta
    hist = make_history(["2026-11-11", "2026-11-12"], [100.0, 101.0])
    client = FakeYF({"NVDA": FakeTicker(history=hist)})
    now = datetime(2026, 11, 12, 18, 0, tzinfo=ROME)

    df = fetch_history("NVDA", 5, now, client=client)

    assert list(df.index) == [date(2026, 11, 11)]


def test_keep_bar_after_us_close():
    # 23:15 a Roma = 17:15 a New York: oltre 16:00 + 20 minuti
    hist = make_history(["2026-11-11", "2026-11-12"], [100.0, 101.0])
    client = FakeYF({"NVDA": FakeTicker(history=hist)})
    now = datetime(2026, 11, 12, 23, 15, tzinfo=ROME)

    df = fetch_history("NVDA", 5, now, client=client)

    assert list(df.index) == [date(2026, 11, 11), date(2026, 11, 12)]


def test_keep_bar_milan_after_close():
    hist = make_history(["2026-11-11", "2026-11-12"], [6.0, 6.1], tz="Europe/Rome")
    client = FakeYF({"ENEL.MI": FakeTicker(history=hist)})
    now = datetime(2026, 11, 12, 18, 0, tzinfo=ROME)

    df = fetch_history("ENEL.MI", 5, now, client=client)

    assert list(df.index) == [date(2026, 11, 11), date(2026, 11, 12)]


def test_drop_incomplete_bar_milan_before_close_margin():
    # 17:40 è dopo la chiusura (17:30) ma dentro il margine di 20 minuti
    df = pd.DataFrame(
        {"close": [6.0, 6.1]},
        index=pd.Index([date(2026, 11, 11), date(2026, 11, 12)], name="date"),
    )
    now = datetime(2026, 11, 12, 17, 40, tzinfo=ROME)

    out = drop_incomplete_bar(df, now, "Europe/Rome", time(17, 30))

    assert list(out.index) == [date(2026, 11, 11)]


def test_fundamentals_missing_fields_are_none():
    client = FakeYF(
        {"XYZ": FakeTicker(info={"longName": "Xyz Inc", "currency": "USD"})}
    )

    f = fetch_fundamentals("XYZ", client=client)

    assert f.name == "Xyz Inc"
    assert f.currency == "USD"
    assert f.trailing_pe is None
    assert f.dividend_yield_pct is None
    assert f.roe is None
    assert f.ex_dividend_date is None


def test_dividend_yield_kept_as_percent():
    info = {
        "longName": "NVIDIA",
        "currency": "USD",
        "trailingPE": 45.0,
        "forwardPE": 30.0,
        "dividendYield": 0.58,
        "payoutRatio": 0.0354,
        "debtToEquity": 16.971,
        "returnOnEquity": 1.17,
        "marketCap": 4.5e12,
    }
    cal = {"Ex-Dividend Date": date(2026, 12, 4)}
    client = FakeYF({"NVDA": FakeTicker(info=info, calendar=cal)})

    f = fetch_fundamentals("NVDA", client=client)

    assert f.dividend_yield_pct == 0.58  # già in percentuale: nessuna conversione
    assert f.payout_ratio == 0.0354
    assert f.debt_to_equity == 16.971
    assert f.roe == 1.17
    assert f.market_cap == 4.5e12
    assert f.ex_dividend_date == date(2026, 12, 4)


def test_dividend_years_without_cut():
    divs = make_dividends(
        {
            "2020-06-01": 1.0,
            "2021-06-01": 1.1,
            "2022-06-01": 0.9,
            "2023-06-01": 1.0,
            "2024-06-01": 1.2,
            "2025-06-01": 1.3,
        }
    )
    assert dividend_years_without_cut(divs, date(2026, 11, 12)) == 3


def test_dividend_years_empty_series_is_zero():
    assert dividend_years_without_cut(pd.Series(dtype=float), date(2026, 11, 12)) == 0


def test_to_eur_usd():
    assert to_eur(111.0, "USD", 1.11) == pytest.approx(100.0)
    assert to_eur(50.0, "EUR", 1.11) == 50.0


def test_to_eur_rejects_gbp():
    with pytest.raises(ValueError):
        to_eur(10.0, "GBP", 1.11)
