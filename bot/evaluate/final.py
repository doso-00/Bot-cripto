"""Test finale della Fase 3 sul periodo di test (brief §6.2 punto 1, §9).

Regole:
- si usano SOLO i parametri congelati in param_selections (scelti sul training);
- si testano solo le strategie promosse dalla validazione, salvo richiesta esplicita;
- ogni strategia si testa una sola volta: un secondo test viene rifiutato, a meno di una
  richiesta esplicita che resta registrata come "ripetuto" nel report e nel database.
"""

import json
import logging
from dataclasses import dataclass
from datetime import datetime

import duckdb
import pandas as pd

from bot.backtest.periods import Period
from bot.backtest.runner import DataQualityError, backtest_config, earliest_filters
from bot.config import Settings
from bot.config.risk import RiskLimits
from bot.data.db import load_candles
from bot.data.quality import run_quality
from bot.evaluate.criteria import Criterion, phase3_criteria
from bot.metrics import compute_metrics
from bot.metrics.regimes import label_days, regime_performance
from bot.optimize.core import Evaluator
from bot.optimize.phase3 import SLIPPAGE_STRESS, _json
from bot.strategies import get_strategy

log = logging.getLogger(__name__)


@dataclass
class Selection:
    selection_id: str
    strategy: str
    params: dict
    created_at: datetime
    passed_validation: bool


@dataclass
class FinalTest:
    selection: Selection
    repeated: bool
    metrics: dict[str, dict]            # valutazione, riavvio, buy & hold, slippage 0,1% (simbolo principale)
    confirm_metrics: dict[str, dict]    # valutazione, buy & hold, slippage 0,1% (simbolo di conferma)
    curves: dict[str, pd.DataFrame]
    regimes: pd.DataFrame
    criteria: list[Criterion]

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.criteria)


def latest_selections(con: duckdb.DuckDBPyConnection) -> list[Selection]:
    rows = con.execute(
        """
        SELECT selection_id, strategy, params_json, created_at, passed_validation
        FROM param_selections
        QUALIFY row_number() OVER (PARTITION BY strategy ORDER BY created_at DESC) = 1
        ORDER BY strategy
        """
    ).fetchall()
    return [Selection(r[0], r[1], json.loads(r[2]), r[3], r[4]) for r in rows]


def previous_tests(con: duckdb.DuckDBPyConnection, strategy: str) -> list[tuple]:
    return con.execute("SELECT selection_id, run_at FROM final_tests WHERE strategy = ? ORDER BY run_at",
                       [strategy]).fetchall()


def choose_selections(con: duckdb.DuckDBPyConnection, requested: list[str] | None, repeat: bool) -> tuple[list[Selection], list[str]]:
    """Selezioni da testare e messaggi per l'utente (motivi di esclusione)."""
    latest = {s.strategy: s for s in latest_selections(con)}
    messages, chosen = [], []
    names = requested if requested else [n for n, s in latest.items() if s.passed_validation]
    for name in names:
        sel = latest.get(name)
        if sel is None:
            messages.append(f"{name}: nessun parametro congelato, eseguire prima uv run python -m bot.optimize")
            continue
        if not sel.passed_validation:
            messages.append(f"{name}: ATTENZIONE, testata su richiesta esplicita pur non essendo promossa dalla validazione")
        done = previous_tests(con, name)
        if done and not repeat:
            messages.append(f"{name}: già testata il {done[0][1]}: il test si guarda una sola volta, esclusa")
            continue
        chosen.append(sel)
    return chosen, messages


def run_final_test(con: duckdb.DuckDBPyConnection, settings: Settings, limits: RiskLimits, selections: list[Selection],
                   period: Period, now: datetime, repeat: bool = False, allow_bad_data: bool = False) -> list[FinalTest]:
    symbol, tf, confirm = settings.symbols[0], settings.timeframe, settings.optimization.confirm_symbol
    symbols = list(dict.fromkeys([symbol, confirm]))
    quality = run_quality(con, settings, symbols=symbols, now=now)
    if not quality.ok and not allow_bad_data:
        raise DataQualityError("controllo qualità dati NON OK: eseguire uv run python -m bot.data.quality")

    histories = {s: load_candles(con, s, tf, end=period.end) for s in symbols}
    filters = {s: earliest_filters(con, s) for s in symbols}
    cfg, cfg_stress = backtest_config(settings), backtest_config(settings, SLIPPAGE_STRESS)
    labels = label_days(histories[symbol], settings.regimes.lookback_days, settings.regimes.threshold)

    out = []
    for sel in selections:
        spec = get_strategy(sel.strategy)
        params = spec.params_model.model_validate(sel.params)
        ev = Evaluator(spec, histories[symbol], cfg, filters[symbol], limits, tf)
        r_main = ev.run(params, period.start, period.end, "off")
        r_bh = ev.run_benchmark(period.start, period.end)
        metrics = {
            "valutazione": compute_metrics(r_main, tf),
            "riavvio": compute_metrics(ev.run(params, period.start, period.end, "restart"), tf),
            "buy & hold": compute_metrics(r_bh, tf),
            "slippage 0,1%": compute_metrics(ev.run(params, period.start, period.end, "off", cfg_stress), tf),
        }
        ev_c = Evaluator(spec, histories[confirm], cfg, filters[confirm], limits, tf)
        confirm_metrics = {
            "valutazione": compute_metrics(ev_c.run(params, period.start, period.end, "off"), tf),
            "buy & hold": compute_metrics(ev_c.run_benchmark(period.start, period.end), tf),
            "slippage 0,1%": compute_metrics(ev_c.run(params, period.start, period.end, "off", cfg_stress), tf),
        }
        criteria = phase3_criteria(metrics["valutazione"], metrics["buy & hold"], metrics["slippage 0,1%"],
                                   confirm_metrics["valutazione"], confirm_metrics["buy & hold"],
                                   confirm_metrics["slippage 0,1%"], confirm)
        test = FinalTest(sel, repeat and bool(previous_tests(con, sel.strategy)), metrics, confirm_metrics,
                         {"strategia": r_main.equity, "buy & hold": r_bh.equity},
                         regime_performance({"strategia": r_main.equity, "buy & hold": r_bh.equity}, labels), criteria)
        con.execute(
            "INSERT INTO final_tests VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [sel.selection_id, sel.strategy, now, period.start, period.end, test.passed, test.repeated,
             _json({"criteria": {c.name: {"passed": c.passed, "detail": c.detail} for c in criteria},
                    "metrics": {k: {m: v[m] for m in ("total_return", "max_drawdown", "sharpe", "n_trades")}
                                for k, v in metrics.items()}})],
        )
        log.info("%s: %s (%d/4 criteri)", sel.strategy, "SUPERA" if test.passed else "non supera",
                 sum(c.passed for c in criteria))
        out.append(test)
    return out
