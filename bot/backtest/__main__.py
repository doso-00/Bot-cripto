"""Esegue un backtest e genera il report HTML.

Uso:
    uv run python -m bot.backtest --strategy sma_cross
    uv run python -m bot.backtest --strategy buy_and_hold --symbol ETH/USDT --timeframe 4h
    uv run python -m bot.backtest --strategy sma_cross --period validation

Il periodo di default è il training. Il periodo di test si usa una sola volta per
strategia, alla fine (brief §6.2): richiede --confirm-test-period.
"""

import argparse
import logging
import sys
from datetime import datetime, timezone

from bot.backtest.periods import PERIOD_NAMES, get_period
from bot.backtest.runner import DataQualityError, available_strategies, run_full_backtest
from bot.backtest.storage import previous_test_runs
from bot.config import load_settings
from bot.config.risk import load_risk_limits
from bot.data.db import connect
from bot.logging_setup import setup_logging

log = logging.getLogger("bot.backtest")


def main(argv: list[str] | None = None) -> int:
    settings = load_settings()
    parser = argparse.ArgumentParser(description="Backtest di una strategia con report HTML")
    parser.add_argument("--strategy", required=True, choices=available_strategies())
    parser.add_argument("--symbol", default=settings.symbols[0], choices=settings.symbols)
    parser.add_argument("--timeframe", default=settings.timeframe, choices=settings.timeframes)
    parser.add_argument("--period", default="train", choices=PERIOD_NAMES)
    parser.add_argument("--confirm-test-period", action="store_true",
                        help="obbligatorio per --period test: il test si guarda una sola volta")
    parser.add_argument("--allow-bad-data", action="store_true", help="esegue anche se il controllo qualità fallisce")
    parser.add_argument("--no-save", action="store_true", help="non salva il run nel database")
    args = parser.parse_args(argv)

    setup_logging(settings.paths.logs, "backtest")
    if not settings.paths.database.exists():
        log.error("database assente: eseguire prima uv run python -m bot.data.download")
        return 1
    con = connect(settings.paths.database)
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    if args.period == "test":
        if not args.confirm_test_period:
            log.error("il periodo di test si usa una sola volta per strategia: aggiungere --confirm-test-period")
            return 1
        for run_id, started in previous_test_runs(con, args.strategy, args.symbol, args.timeframe):
            log.warning("ATTENZIONE: periodo di test già usato per questa strategia il %s (run %s)", started, run_id)

    try:
        run = run_full_backtest(
            con, settings, load_risk_limits(), args.strategy, args.symbol, args.timeframe,
            get_period(settings, args.period, now), now,
            allow_bad_data=args.allow_bad_data, save=not args.no_save, report_dir=settings.paths.reports,
        )
    except DataQualityError as exc:
        log.error("%s", exc)
        return 1
    finally:
        con.close()

    ctx = run.context
    c = ctx.bh_check
    log.info("verifica buy & hold: motore %.4f, calcolo manuale %.4f, differenza %+.5f%%",
             c["engine"], c["manual"], c["diff"] * 100)
    for v in ctx.variants:
        m = v.metrics
        log.info("%-30s rendimento %+8.2f%%  max drawdown %6.2f%%  Sharpe %5.2f  operazioni %4d",
                 v.label, m["total_return"] * 100, m["max_drawdown"] * 100, m["sharpe"], m["n_trades"])
    for w in ctx.warnings:
        log.warning("%s", w)
    log.info("report: %s%s", run.report_path, "" if run.saved else " (run non salvato)")
    return 0 if abs(c["diff"]) < 0.0001 else 1


if __name__ == "__main__":
    sys.exit(main())
