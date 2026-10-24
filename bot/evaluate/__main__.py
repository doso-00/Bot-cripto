"""Test finale della Fase 3: una sola volta, sulle strategie promosse dalla validazione.

Uso:
    uv run python -m bot.evaluate --confirm-test-period

Usa i parametri congelati da `uv run python -m bot.optimize`. Le strategie già testate
vengono escluse (il test si guarda una sola volta); --repeat-test le riesegue lasciando
traccia del fatto che il test è stato ripetuto.
"""

import argparse
import logging
import sys

from bot.backtest.periods import get_period
from bot.backtest.runner import DataQualityError
from bot.config import load_settings
from bot.config.risk import load_risk_limits
from bot.data.db import connect
from bot.evaluate.final import choose_selections, run_final_test
from bot.logging_setup import setup_logging
from bot.optimize.phase3 import utc_now
from bot.report.final_html import render_final_report
from bot.strategies import CANDIDATES

log = logging.getLogger("bot.evaluate")


def main(argv: list[str] | None = None) -> int:
    settings = load_settings()
    parser = argparse.ArgumentParser(description="Test finale della Fase 3 (una sola volta)")
    parser.add_argument("--confirm-test-period", action="store_true", required=True,
                        help="conferma di voler usare il periodo di test, che si guarda una sola volta")
    parser.add_argument("--strategy", choices=CANDIDATES, action="append",
                        help="testa questa strategia anche se non promossa dalla validazione (ripetibile)")
    parser.add_argument("--repeat-test", action="store_true", help="riesegue anche strategie già testate")
    parser.add_argument("--allow-bad-data", action="store_true")
    args = parser.parse_args(argv)

    setup_logging(settings.paths.logs, "evaluate")
    if not settings.paths.database.exists():
        log.error("database assente: eseguire prima uv run python -m bot.data.download")
        return 1
    con = connect(settings.paths.database)
    now = utc_now()
    try:
        selections, messages = choose_selections(con, args.strategy, args.repeat_test)
        for m in messages:
            log.warning("%s", m)
        if not selections:
            log.info("nessuna strategia da testare: il periodo di test resta intatto")
            return 0
        period = get_period(settings, "test", now)
        tests = run_final_test(con, settings, load_risk_limits(), selections, period, now,
                               repeat=args.repeat_test, allow_bad_data=args.allow_bad_data)
    except DataQualityError as exc:
        log.error("%s", exc)
        return 1
    finally:
        con.close()

    settings.paths.reports.mkdir(parents=True, exist_ok=True)
    out = settings.paths.reports / f"fase3_test_finale_{now:%Y%m%d_%H%M%S}.html"
    out.write_text(render_final_report(tests, period, messages, now, settings.optimization.confirm_symbol), encoding="utf-8")
    log.info("report: %s", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
