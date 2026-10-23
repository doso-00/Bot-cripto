"""Flusso di download con archivio e API simulati (nessuna rete)."""

from datetime import date, datetime

from bot.data import archive
from bot.data.db import load_candles
from bot.data.download import to_epoch_ms, update_api, update_bulk, update_filters
from bot.data.filters import latest_filters
from bot.data.quality import run_quality
from tests.conftest import raw_klines
from tests.test_archive import FakeArchive, archive_month


class FakeClient:
    """Simula l'endpoint klines: restituisce al massimo `limit` candele da startTime,
    inclusa quella ancora aperta, come l'API reale."""

    def __init__(self, rows: list[list], limit: int = 1000, exchange_info: dict | None = None):
        self.rows, self.limit, self.exchange_info = rows, limit, exchange_info
        self.requests: list[int] = []

    def fetch_klines(self, symbol, timeframe, start_ms, limit=1000):
        self.requests.append(start_ms)
        return [r for r in self.rows if r[0] >= start_ms][: self.limit]

    def fetch_exchange_info(self, symbols):
        return self.exchange_info


def test_bulk_then_api_gives_continuous_series(con, settings):
    # Storico dal 2020-01-01 al 2020-03-10 10:30 UTC; archivio pubblicato fino a gennaio,
    # febbraio non ancora pubblicato: lo deve coprire l'API.
    now = datetime(2020, 3, 10, 10, 30)
    all_rows = raw_klines(datetime(2020, 1, 1), (31 + 29 + 9) * 24 + 11, "1h")  # fino alla 10:00 aperta
    jan = [r for r in all_rows if r[0] < to_epoch_ms(datetime(2020, 2, 1))]
    fake = FakeArchive(archive_month("BTC/USDT", "1h", 2020, 1, jan))

    bulk = update_bulk(con, "BTC/USDT", "1h", date(2020, 1, 1), now.date(), settings.paths.raw_data, fetch=fake)
    assert bulk.inserted == 31 * 24

    client = FakeClient(all_rows, limit=500)  # limite basso per verificare la paginazione
    api = update_api(con, client, "BTC/USDT", "1h", date(2020, 1, 1), to_epoch_ms(now))
    assert len(client.requests) > 1
    assert client.requests[0] == to_epoch_ms(datetime(2020, 2, 1))  # riparte dopo l'ultima salvata

    stored = load_candles(con, "BTC/USDT", "1h")
    assert bulk.inserted + api.inserted == len(stored) == len(all_rows) - 1  # esclusa la candela aperta
    assert stored["open_time"].iat[-1] == datetime(2020, 3, 10, 9)
    assert set(stored["source"]) == {"bulk", "api"}

    # Rilanciare non duplica nulla e non chiede candele già presenti
    again = update_api(con, FakeClient(all_rows), "BTC/USDT", "1h", date(2020, 1, 1), to_epoch_ms(now))
    assert again.inserted == 0

    report = run_quality(con, settings, symbols=["BTC/USDT"], timeframes=["1h"], now=now)
    assert report.ok, report.errors + report.unexplained_gaps


def test_api_skips_when_no_closed_candle_is_new(con):
    now = datetime(2020, 1, 1, 0, 30)  # la prima candela è ancora aperta
    client = FakeClient(raw_klines(datetime(2020, 1, 1), 1, "1h"))
    stats = update_api(con, client, "BTC/USDT", "1h", date(2020, 1, 1), to_epoch_ms(now))
    assert stats.inserted == 0 and client.requests == []


def test_unpublished_middle_month_is_skipped_not_fatal(con, settings):
    rows = raw_klines(datetime(2020, 1, 1), 31 * 24, "1h")
    fake = FakeArchive(archive_month("BTC/USDT", "1h", 2020, 1, rows))  # febbraio assente, marzo concluso
    stats = update_bulk(con, "BTC/USDT", "1h", date(2020, 1, 1), date(2020, 4, 15), settings.paths.raw_data, fetch=fake)
    assert stats.inserted == 31 * 24
    assert any(url.endswith("2020-03.zip") for url in fake.calls)


def _exchange_info(step_size="0.00001000", min_notional="5.00000000"):
    return {
        "symbols": [
            {
                "symbol": "BTCUSDT",
                "filters": [
                    {"filterType": "PRICE_FILTER", "minPrice": "0.01", "maxPrice": "1000000", "tickSize": "0.01000000"},
                    {"filterType": "LOT_SIZE", "minQty": "0.00001000", "maxQty": "9000.00000000", "stepSize": step_size},
                    {"filterType": "NOTIONAL", "minNotional": min_notional, "applyMinToMarket": True,
                     "maxNotional": "9000000.00000000", "applyMaxToMarket": False, "avgPriceMins": 5},
                ],
            }
        ]
    }


def test_exchange_filters_are_versioned_only_on_change(con):
    t1, t2, t3 = datetime(2026, 1, 1), datetime(2026, 2, 1), datetime(2026, 3, 1)
    assert update_filters(con, FakeClient([], exchange_info=_exchange_info()), ["BTC/USDT"], t1) == ["BTC/USDT"]
    assert update_filters(con, FakeClient([], exchange_info=_exchange_info()), ["BTC/USDT"], t2) == []
    changed = _exchange_info(min_notional="10.00000000")
    assert update_filters(con, FakeClient([], exchange_info=changed), ["BTC/USDT"], t3) == ["BTC/USDT"]

    assert con.execute("SELECT count(*) FROM exchange_filters").fetchall()[0][0] == 2
    assert str(latest_filters(con, "BTC/USDT", at=t2)["min_notional"]) == "5.00000000"
    assert str(latest_filters(con, "BTC/USDT")["min_notional"]) == "10.00000000"
    assert str(latest_filters(con, "BTC/USDT")["step_size"]) == "0.00001000"
