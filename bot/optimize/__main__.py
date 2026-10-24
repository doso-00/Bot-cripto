"""Fase 3: ottimizzazione walk-forward sul training e confronto delle strategie sulla validazione.

Uso:
    uv run python -m bot.optimize --all
    uv run python -m bot.optimize --strategy sma_cross

Non legge mai il periodo di test. Salva i parametri scelti (tabella param_selections) e
scrive il report comparativo in reports/fase3_confronto_<data>.html.
"""

import argparse
import logging
import sys

from bot.backtest.runner import DataQualityError
from bot.config import load_settings
from bot.config.risk import load_risk_limits
from bot.data.db import connect
from bot.logging_setup import setup_logging
from bot.optimize.phase3 import run_phase3, utc_now
from bot.report.phase3_html import render_phase3_report
from bot.strategies import CANDIDATES

log = logging.getLogger("bot.optimize")


def main(argv: list[str] | None = None) -> int:
    settings = load_settings()
    parser = argparse.ArgumentParser(description="Ottimizzazione e confronto delle strategie (Fase 3)")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--strategy", choices=CANDIDATES, action="append", help="ripetibile")
    group.add_argument("--all", action="store_true", help="tutte le strategie candidate")
    parser.add_argument("--allow-bad-data", action="store_true")
    parser.add_argument("--no-save", action="store_true", help="non salva i parametri scelti nel database")
    args = parser.parse_args(argv)

    setup_logging(settings.paths.logs, "optimize")
    if not settings.paths.database.exists():
        log.error("database assente: eseguire prima uv run python -m bot.data.download")
        return 1
    strategies = CANDIDATES if args.all else args.strategy
    con = connect(settings.paths.database)
    now = utc_now()
    try:
        result = run_phase3(con, settings, load_risk_limits(), strategies, now,
                            allow_bad_data=args.allow_bad_data, save=not args.no_save)
    except DataQualityError as exc:
        log.error("%s", exc)
        return 1
    finally:
        con.close()

    settings.paths.reports.mkdir(parents=True, exist_ok=True)
    out = settings.paths.reports / f"fase3_confronto_{now:%Y%m%d_%H%M%S}.html"
    out.write_text(render_phase3_report(result, settings.optimization.model_dump()), encoding="utf-8")
    promoted = [s.name for s in result.strategies if s.passed_validation]
    log.info("combinazioni provate in totale: %d", result.total_combinations)
    log.info("promosse al test: %s", ", ".join(promoted) if promoted else "nessuna")
    log.info("report: %s", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
