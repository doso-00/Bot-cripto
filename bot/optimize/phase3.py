"""Fase 3: per ogni strategia candidata, ottimizzazione sul training, walk-forward,
validazione, robustezza e anteprima dei criteri. Non legge mai il periodo di test.

Flusso (docs/DECISIONI.md, 2026-10-24):
1. griglia sul training (BTC/USDT 1h) e scelta robusta dei parametri;
2. walk-forward sul training per misurare se il procedimento regge fuori campione;
3. validazione 2024 con i parametri congelati: metriche, criteri della Fase 3 (con
   ETH/USDT per il criterio 4), robustezza su ETH e sul 4h senza riottimizzare, regimi;
4. la strategia è "promossa al test" solo se supera tutti i criteri sulla validazione.
   Il test finale (bot.evaluate) si esegue una sola volta, sulle sole promosse.
"""

import json
import logging
import math
from dataclasses import dataclass
from datetime import datetime, timezone

import duckdb
import pandas as pd

from bot.backtest.periods import Period, get_period
from bot.backtest.runner import DataQualityError, backtest_config, earliest_filters
from bot.backtest.storage import config_hash, git_commit
from bot.config import Settings
from bot.config.risk import RiskLimits
from bot.data.db import load_candles
from bot.data.quality import run_quality
from bot.evaluate.criteria import Criterion, phase3_criteria
from bot.metrics import compute_metrics
from bot.metrics.regimes import label_days, regime_performance
from bot.optimize.core import (ComboResult, Evaluator, Fold, grid_axes, select_robust, summarize_folds,
                               walk_forward, walk_forward_windows)
from bot.strategies import get_strategy, load_grid

log = logging.getLogger(__name__)

SLIPPAGE_STRESS = 0.001  # criterio 3: positivo anche con slippage 0,1% per lato


@dataclass
class StrategyPhase3:
    name: str
    symbol: str
    timeframe: str
    grid_size: int
    grid_table: pd.DataFrame
    selection_note: str | None
    selected: dict
    train: ComboResult
    isolated_gap: float              # Sharpe della scelta meno media delle vicine
    folds: list[Fold]
    wf_summary: dict
    validation: dict[str, dict]      # "valutazione", "riavvio", "buy & hold", "slippage 0,1%"
    validation_curves: dict[str, pd.DataFrame]
    regimes: pd.DataFrame
    robustness: pd.DataFrame
    criteria: list[Criterion]
    selection_id: str = ""

    @property
    def passed_validation(self) -> bool:
        return all(c.passed for c in self.criteria)


@dataclass
class Phase3Result:
    strategies: list[StrategyPhase3]
    train: Period
    validation: Period
    generated_at: datetime
    data_quality: str

    @property
    def total_combinations(self) -> int:
        return sum(s.grid_size for s in self.strategies)


def _json(obj) -> str:
    def clean(v):
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            return None
        if isinstance(v, (pd.Timedelta, pd.Timestamp, datetime)):
            return str(v)
        if isinstance(v, dict):
            return {k: clean(x) for k, x in v.items()}
        if hasattr(v, "item"):  # numeri numpy (int64, float64)
            return clean(v.item())
        return v
    return json.dumps(clean(obj))


def evaluate_strategy(name: str, histories: dict, filters: dict, settings: Settings, limits: RiskLimits,
                      train: Period, validation: Period) -> StrategyPhase3:
    spec = get_strategy(name)
    symbol, tf = settings.symbols[0], settings.timeframe
    confirm = settings.optimization.confirm_symbol
    opt = settings.optimization
    cfg, cfg_stress = backtest_config(settings), backtest_config(settings, SLIPPAGE_STRESS)

    def evaluator(sym, timeframe):
        return Evaluator(spec, histories[sym, timeframe], cfg, filters[sym], limits, timeframe)

    ev = evaluator(symbol, tf)
    combos = load_grid(spec)
    results = [ev.evaluate(p, train.start, train.end) for p in combos]
    selected, table, note = select_robust(results, grid_axes(spec), opt.min_trades_full)
    row = table[table["key"] == selected.key].iloc[0]
    isolated_gap = selected.score - row["neighbors_sharpe"] if row["n_neighbors"] else math.nan

    windows = walk_forward_windows(train.start, train.end, opt.is_months, opt.oos_months, opt.step_months)
    folds = walk_forward(ev, combos, windows, opt.min_trades_window)

    params = selected.params
    r_eval = ev.run(params, validation.start, validation.end, "off")
    r_restart = ev.run(params, validation.start, validation.end, "restart")
    r_bh = ev.run_benchmark(validation.start, validation.end)
    r_stress = ev.run(params, validation.start, validation.end, "off", cfg_stress)
    val = {
        "valutazione": compute_metrics(r_eval, tf), "riavvio": compute_metrics(r_restart, tf),
        "buy & hold": compute_metrics(r_bh, tf), "slippage 0,1%": compute_metrics(r_stress, tf),
    }

    ev_confirm = evaluator(confirm, tf)
    c_main = compute_metrics(ev_confirm.run(params, validation.start, validation.end, "off"), tf)
    c_bh = compute_metrics(ev_confirm.run_benchmark(validation.start, validation.end), tf)
    c_stress = compute_metrics(ev_confirm.run(params, validation.start, validation.end, "off", cfg_stress), tf)
    criteria = phase3_criteria(val["valutazione"], val["buy & hold"], val["slippage 0,1%"],
                               c_main, c_bh, c_stress, confirm)

    robustness_rows = []
    for period in (train, validation):
        for sym in (symbol, confirm):
            for timeframe in settings.timeframes:
                e = evaluator(sym, timeframe)
                m = compute_metrics(e.run(params, period.start, period.end, "off"), timeframe)
                b = compute_metrics(e.run_benchmark(period.start, period.end), timeframe)
                robustness_rows.append({
                    "period": period.name, "symbol": sym, "timeframe": timeframe,
                    "total_return": m["total_return"], "bh_return": b["total_return"],
                    "max_drawdown": m["max_drawdown"], "bh_max_drawdown": b["max_drawdown"],
                    "sharpe": m["sharpe"], "n_trades": m["n_trades"],
                })

    labels = label_days(histories[symbol, tf], settings.regimes.lookback_days, settings.regimes.threshold)
    regimes = regime_performance({"strategia": r_eval.equity, "buy & hold": r_bh.equity}, labels)

    return StrategyPhase3(
        name=name, symbol=symbol, timeframe=tf, grid_size=len(combos), grid_table=table, selection_note=note,
        selected=params.model_dump(), train=selected, isolated_gap=isolated_gap, folds=folds,
        wf_summary=summarize_folds(folds), validation=val,
        validation_curves={"strategia": r_eval.equity, "buy & hold": r_bh.equity},
        regimes=regimes, robustness=pd.DataFrame(robustness_rows), criteria=criteria,
    )


def save_selection(con: duckdb.DuckDBPyConnection, s: StrategyPhase3, settings: Settings, limits: RiskLimits,
                   created_at: datetime) -> str:
    selection_id = f"sel_{s.name}_{created_at:%Y%m%d_%H%M%S}"
    payload = {"strategy": s.name, "params": s.selected, "symbol": s.symbol, "timeframe": s.timeframe,
               "fee_rate": settings.fee_rate, "slippage_rate": settings.slippage_rate,
               "optimization": settings.optimization.model_dump(), "risk": limits.model_dump(),
               "train_end": str(settings.train_end), "validation_end": str(settings.validation_end)}
    con.execute("BEGIN TRANSACTION")
    try:
        con.execute(
            "INSERT INTO param_selections VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [selection_id, s.name, s.symbol, s.timeframe, json.dumps(s.selected), s.grid_size, created_at,
             config_hash(payload), git_commit(),
             _json({"sharpe": s.train.sharpe, "total_return": s.train.total_return,
                    "max_drawdown": s.train.max_drawdown, "n_trades": s.train.n_trades}),
             _json(s.wf_summary),
             _json({k: {m: v[m] for m in ("total_return", "max_drawdown", "sharpe", "n_trades")}
                    for k, v in s.validation.items()}),
             s.passed_validation],
        )
        for r in s.grid_table.itertuples():
            con.execute("INSERT INTO grid_results VALUES (?, ?, ?, ?, ?, ?, ?)",
                        [selection_id, json.dumps(r.params), None if math.isnan(r.sharpe) else r.sharpe,
                         r.total_return, r.max_drawdown, r.n_trades, r.robust_score])
        for f in s.folds:
            con.execute("INSERT INTO wf_folds VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        [selection_id, f.number, f.is_start, f.is_end, f.oos_start, f.oos_end,
                         json.dumps(f.params) if f.params else None,
                         None if math.isnan(f.is_sharpe) else f.is_sharpe, f.oos_return, f.oos_bh_return, f.oos_trades])
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return selection_id


def run_phase3(con: duckdb.DuckDBPyConnection, settings: Settings, limits: RiskLimits, strategies: list[str],
               now: datetime, allow_bad_data: bool = False, save: bool = True) -> Phase3Result:
    symbols = list(dict.fromkeys([settings.symbols[0], settings.optimization.confirm_symbol]))
    quality = run_quality(con, settings, symbols=symbols, now=now)
    quality_line = (f"Controllo qualità dati ({', '.join(symbols)}): {'OK' if quality.ok else 'NON OK'} "
                    f"({len(quality.errors)} errori, {len(quality.unexplained_gaps)} buchi non spiegati).")
    if not quality.ok and not allow_bad_data:
        raise DataQualityError(quality_line + " Eseguire uv run python -m bot.data.quality per i dettagli.")

    train, validation = get_period(settings, "train", now), get_period(settings, "validation", now)
    # Il periodo di test non viene nemmeno caricato: niente può "vederlo" per sbaglio
    histories = {(s, tf): load_candles(con, s, tf, end=validation.end) for s in symbols for tf in settings.timeframes}
    filters = {s: earliest_filters(con, s) for s in symbols}

    results = []
    for name in strategies:
        log.info("Fase 3: %s ...", name)
        res = evaluate_strategy(name, histories, filters, settings, limits, train, validation)
        if save:
            res.selection_id = save_selection(con, res, settings, limits, now)
        log.info("  parametri scelti %s; validazione %+.2f%% (buy & hold %+.2f%%); criteri superati %d/4",
                 res.selected, res.validation["valutazione"]["total_return"] * 100,
                 res.validation["buy & hold"]["total_return"] * 100, sum(c.passed for c in res.criteria))
        results.append(res)

    return Phase3Result(results, train, validation, now, quality_line)


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)
