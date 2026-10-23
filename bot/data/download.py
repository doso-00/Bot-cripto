"""Scarica/aggiorna le candele storiche e i filtri dell'exchange.

Uso:
    uv run python -m bot.data.download
    uv run python -m bot.data.download --symbols BTC/USDT --timeframes 1h

Strategia (brief §4.2):
1. bulk: file mensili dell'archivio data.binance.vision, dal mese di `history_start`
   all'ultimo mese concluso (non consumano rate limit dell'API);
2. incrementale: endpoint pubblico klines dall'ultima candela salvata fino all'ultima
   candela chiusa (copre il mese in corso e quello appena concluso se non ancora pubblicato);
3. filtri: exchangeInfo, salvati solo se cambiati rispetto all'ultima versione.
L'operazione è idempotente: rilanciarla non duplica nulla.
"""

import argparse
import logging
import sys
import urllib.error
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import ccxt
import duckdb

from bot.config import Settings, load_settings
from bot.data import archive
from bot.data.db import UpsertStats, connect, last_open_time, upsert_candles
from bot.data.filters import store_filters
from bot.data.klines import drop_unclosed, epoch_ms, klines_to_frame, timeframe_ms
from bot.exchange.client import BinancePublicClient
from bot.logging_setup import setup_logging

log = logging.getLogger(__name__)


def to_epoch_ms(dt: datetime) -> int:
    """datetime senza fuso (convenzione UTC del database) -> millisecondi epoch."""
    return int(dt.replace(tzinfo=timezone.utc).timestamp() * 1000)


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def update_bulk(
    con: duckdb.DuckDBPyConnection,
    symbol: str,
    timeframe: str,
    history_start: date,
    today: date,
    raw_dir: Path,
    fetch: archive.Fetcher = archive.http_get,
) -> UpsertStats:
    stats = UpsertStats()
    last_month = archive.last_complete_month(today)
    for year, month in archive.iter_months(history_start, last_month):
        try:
            path = archive.download_month(symbol, timeframe, year, month, raw_dir, fetch)
        except archive.NotPublishedError:
            if (year, month) == (last_month.year, last_month.month):
                log.info("%s %s %d-%02d non ancora pubblicato: lo copre l'API", symbol, timeframe, year, month)
            else:
                # Un mese intermedio mancante non è atteso: emergerà come buco nel report qualità
                log.warning("%s %s %d-%02d assente nell'archivio", symbol, timeframe, year, month)
            continue
        frame = archive.read_month(path, symbol, timeframe)
        frame = frame[frame["open_time"] >= datetime.combine(history_start, datetime.min.time())]
        stats += upsert_candles(con, frame, utc_now())
    return stats


def update_api(
    con: duckdb.DuckDBPyConnection,
    client: BinancePublicClient,
    symbol: str,
    timeframe: str,
    history_start: date,
    now_ms: int,
) -> UpsertStats:
    stats = UpsertStats()
    step = timeframe_ms(timeframe)
    last = last_open_time(con, symbol, timeframe)
    next_ms = to_epoch_ms(last) + step if last else to_epoch_ms(datetime.combine(history_start, datetime.min.time()))

    while next_ms + step <= now_ms:  # esiste almeno una candela chiusa da scaricare
        rows = client.fetch_klines(symbol, timeframe, next_ms)
        closed = drop_unclosed(rows, now_ms)
        if not closed:
            break
        frame = klines_to_frame(closed, symbol, timeframe, source="api")
        stats += upsert_candles(con, frame, utc_now())

        last_ms = max(epoch_ms(r[0]) for r in closed)
        if last_ms < next_ms:
            raise RuntimeError(f"{symbol} {timeframe}: l'API ha restituito candele precedenti a quelle richieste")
        next_ms = last_ms + step
    return stats


def update_filters(
    con: duckdb.DuckDBPyConnection, client: BinancePublicClient, symbols: list[str], now: datetime
) -> list[str]:
    """Aggiorna exchange_filters; restituisce i simboli per cui i filtri sono cambiati."""
    info = client.fetch_exchange_info(symbols)
    by_symbol = {s["symbol"]: s for s in info["symbols"]}
    changed = []
    for symbol in symbols:
        if store_filters(con, symbol, by_symbol[symbol.replace("/", "")], now):
            changed.append(symbol)
    return changed


def summary(con: duckdb.DuckDBPyConnection) -> list[tuple]:
    return con.execute(
        """
        SELECT symbol, timeframe, count(*) AS n, min(open_time) AS first, max(open_time) AS last
        FROM candles GROUP BY ALL ORDER BY ALL
        """
    ).fetchall()


def main(argv: list[str] | None = None) -> int:
    settings = load_settings()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--symbols", nargs="+", default=settings.symbols)
    parser.add_argument("--timeframes", nargs="+", default=settings.timeframes)
    parser.add_argument("--skip-bulk", action="store_true", help="non usare l'archivio mensile")
    parser.add_argument("--skip-api", action="store_true", help="non usare l'API per l'aggiornamento incrementale")
    parser.add_argument("--skip-filters", action="store_true", help="non aggiornare exchange_filters")
    args = parser.parse_args(argv)

    log_file = setup_logging(settings.paths.logs, "download")
    con = connect(settings.paths.database)
    try:
        run(con, args, settings)
    except (urllib.error.URLError, ccxt.NetworkError) as exc:
        log.error("Binance non raggiungibile dopo i tentativi previsti (%s). I dati già salvati restano validi: "
                  "rilanciare il comando più tardi riprende da dove si era fermato.", exc)
        return 2
    finally:
        con.close()

    log.info("log completo in %s. Prossimo passo: uv run python -m bot.data.quality", log_file)
    return 0


def run(con: duckdb.DuckDBPyConnection, args: argparse.Namespace, settings: Settings) -> None:
    client = BinancePublicClient()
    now = utc_now()

    for symbol in args.symbols:
        for timeframe in args.timeframes:
            if not args.skip_bulk:
                s = update_bulk(con, symbol, timeframe, settings.history_start, now.date(), settings.paths.raw_data)
                log.info("%s %s bulk: %d nuove, %d modificate, %d invariate", symbol, timeframe, s.inserted, s.changed, s.unchanged)
            if not args.skip_api:
                s = update_api(con, client, symbol, timeframe, settings.history_start, to_epoch_ms(now))
                log.info("%s %s api: %d nuove, %d modificate, %d invariate", symbol, timeframe, s.inserted, s.changed, s.unchanged)

    if not args.skip_filters:
        changed = update_filters(con, client, args.symbols, now)
        log.info("exchange_filters: %s", f"nuova versione per {changed}" if changed else "nessun cambiamento")

    for symbol, timeframe, n, first, last in summary(con):
        log.info("%-9s %-3s %7d candele  %s -> %s", symbol, timeframe, n, first, last)


if __name__ == "__main__":
    sys.exit(main())
