"""Esecuzione completa di un backtest: dati, controllo qualità, varianti, metriche, report."""

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pandas as pd

from bot.backtest.engine import BacktestConfig, run_backtest
from bot.backtest.periods import Period
from bot.backtest.storage import config_hash, git_commit, save_run
from bot.backtest.verify import formula_buy_and_hold, manual_buy_and_hold
from bot.config import Settings
from bot.config.risk import RiskLimits
from bot.data.db import load_candles
from bot.data.filters import FILTER_FIELDS
from bot.data.quality import run_quality
from bot.exchange.rounding import SymbolFilters
from bot.metrics import alpha_vs_benchmark, compute_metrics, warnings_for
from bot.metrics.regimes import label_days, regime_performance
from bot.report import ReportContext, Variant, render_backtest_report
from bot.risk.manager import RiskManager
from bot.strategies import STRATEGIES, compute_targets, get_strategy, load_params

log = logging.getLogger(__name__)

SLIPPAGE_SENSITIVITY = (0.0, 0.0005, 0.001, 0.002)
LABEL_RM, LABEL_NO_RM, LABEL_BH = "Strategia con risk manager", "Strategia senza risk manager", "Buy & hold"

ASSUMPTIONS = [
    "Esecuzione a mercato all'apertura della candela successiva al segnale, per l'intera quantità, "
    "con slippage costante nella direzione sfavorevole (nessun limite di liquidità).",
    "Commissione sul controvalore pagata in USDT; Binance la trattiene nell'asset ricevuto, con lo stesso costo in valore.",
    "Filtri dell'exchange (step size, notional minimo): la versione più vecchia salvata, applicata a tutto il "
    "periodo. Binance non pubblica lo storico dei filtri: quelli del passato potevano essere diversi.",
    "Nessuna esecuzione su candele a volume zero (es. durante un fermo): l'ordine slitta alla candela successiva.",
    "Nessun interesse sugli USDT non investiti.",
    "Posizione chiusa al close dell'ultima candela del periodo, con costi, per avere un rendimento netto completo.",
    "Il risk manager in backtest valuta l'equity al close di ogni candela; dopo lo stop per drawdown il bot resta "
    "fuori dal mercato fino a fine periodo (in reale servirebbe un riavvio manuale).",
]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class DataQualityError(Exception):
    pass


@dataclass
class BacktestRun:
    context: ReportContext
    report_path: Path | None
    saved: bool


def earliest_filters(con: duckdb.DuckDBPyConnection, symbol: str) -> SymbolFilters:
    rows = con.execute(
        f"SELECT {', '.join(FILTER_FIELDS)} FROM exchange_filters WHERE symbol = ? ORDER BY valid_from LIMIT 1",
        [symbol],
    ).fetchall()
    if not rows:
        raise ValueError(f"nessun filtro salvato per {symbol}: eseguire prima uv run python -m bot.data.download")
    return SymbolFilters.from_mapping(dict(zip(FILTER_FIELDS, rows[0])))


def run_full_backtest(
    con: duckdb.DuckDBPyConnection,
    settings: Settings,
    limits: RiskLimits,
    strategy_name: str,
    symbol: str,
    timeframe: str,
    period: Period,
    now: datetime,
    params=None,
    allow_bad_data: bool = False,
    save: bool = True,
    report_dir: Path | None = None,
) -> BacktestRun:
    spec = get_strategy(strategy_name)
    params = params if params is not None else load_params(spec)

    quality = run_quality(con, settings, symbols=[symbol], now=now)
    quality_line = (
        f"Controllo qualità dati: {'OK' if quality.ok else 'NON OK'} ({len(quality.errors)} errori, "
        f"{len(quality.unexplained_gaps)} buchi non spiegati, {len(quality.warnings)} avvisi)."
    )
    if not quality.ok:
        if not allow_bad_data:
            raise DataQualityError(quality_line + " Eseguire uv run python -m bot.data.quality per i dettagli.")
        quality_line += " Backtest eseguito comunque con --allow-bad-data: risultati non affidabili."

    filters = earliest_filters(con, symbol)
    # Le candele precedenti al periodo servono solo a "riscaldare" gli indicatori
    history = load_candles(con, symbol, timeframe, end=period.end)
    in_period = history["open_time"] >= period.start
    if in_period.sum() < 2:
        raise ValueError(f"meno di 2 candele {symbol} {timeframe} nel periodo {period.name}")
    targets = compute_targets(spec, history, params)[in_period]
    candles = history[in_period]

    cfg = BacktestConfig(settings.initial_capital_usdt, settings.fee_rate, settings.slippage_rate,
                         settings.min_rebalance_delta)
    ones = pd.Series(1.0, index=candles.index)
    with_rm = run_backtest(candles, targets, cfg, filters, RiskManager(limits))
    no_rm = run_backtest(candles, targets, cfg, filters, None)
    bh = run_backtest(candles, ones, cfg, filters, None)

    variants = [
        Variant(LABEL_RM, with_rm, compute_metrics(with_rm, timeframe)),
        Variant(LABEL_NO_RM, no_rm, compute_metrics(no_rm, timeframe)),
        Variant(LABEL_BH, bh, compute_metrics(bh, timeframe)),
    ]
    bh_metrics = variants[2].metrics
    alpha = {v.label: alpha_vs_benchmark(v.metrics, bh_metrics) for v in variants[:2]}

    manual = manual_buy_and_hold(candles, cfg, filters)
    bh_check = {"engine": bh.final_equity, "manual": manual, "formula": formula_buy_and_hold(candles, cfg),
                "diff": bh.final_equity / manual - 1}

    warnings = []
    if period.name == "test":
        warnings.append("Periodo di TEST: per il brief si guarda una sola volta per strategia, alla fine. "
                        "Ogni nuova esecuzione su questo periodo ne riduce il valore.")
    for v in variants:
        warnings += [f"{v.label}: {w}" for w in warnings_for(v.metrics, is_benchmark=v.label == LABEL_BH)]
    if abs(bh_check["diff"]) >= 0.0001:
        warnings.insert(0, "Il buy & hold del motore NON coincide con il calcolo manuale entro lo 0,01%: "
                           "i risultati non sono affidabili finché la differenza non è spiegata.")

    labels = label_days(history, settings.regimes.lookback_days, settings.regimes.threshold)
    regimes = regime_performance({v.label: v.result.equity for v in variants}, labels)

    sens_rows = []
    for slip in SLIPPAGE_SENSITIVITY:
        c = BacktestConfig(cfg.initial_capital, cfg.fee_rate, slip, cfg.min_rebalance_delta)
        r_rm = run_backtest(candles, targets, c, filters, RiskManager(limits))
        r_bh = run_backtest(candles, ones, c, filters, None)
        sens_rows.append({"slippage": slip,
                          f"rendimento {LABEL_RM.lower()}": r_rm.final_equity / cfg.initial_capital - 1,
                          "operazioni": len(r_rm.trades),
                          f"rendimento {LABEL_BH.lower()}": r_bh.final_equity / cfg.initial_capital - 1})
    sensitivity = pd.DataFrame(sens_rows)

    params_dict = params.model_dump()
    hash_payload = {
        "strategy": strategy_name, "params": params_dict, "symbol": symbol, "timeframe": timeframe,
        "period": [period.name, period.start, period.end], "fee_rate": cfg.fee_rate,
        "slippage_rate": cfg.slippage_rate, "initial_capital": cfg.initial_capital,
        "min_rebalance_delta": cfg.min_rebalance_delta, "risk": limits.model_dump(),
        "filters": {k: str(v) for k, v in filters.__dict__.items()},
    }
    started = _utc_now()
    run_id = f"bt_{strategy_name}_{symbol.replace('/', '')}_{timeframe}_{period.name}_{now:%Y%m%d_%H%M%S}"
    ctx = ReportContext(
        run_id=run_id, strategy=strategy_name, symbol=symbol, timeframe=timeframe, period_name=period.name,
        period_start=period.start, period_end=min(period.end, candles["open_time"].iat[-1].to_pydatetime()),
        params=params_dict, config_hash=config_hash(hash_payload), git_commit=git_commit(), generated_at=now,
        variants=variants, alpha=alpha, warnings=warnings, bh_check=bh_check, regimes=regimes,
        sensitivity=sensitivity, data_quality=quality_line, assumptions=ASSUMPTIONS,
    )

    if save:
        save_run(con, {
            "run_id": run_id, "strategy": strategy_name, "symbol": symbol, "timeframe": timeframe,
            "period_name": period.name, "period_start": period.start, "period_end": period.end,
            "params_json": json.dumps(params_dict), "risk_enabled": True, "config_hash": ctx.config_hash,
            "git_commit": ctx.git_commit, "started_at": started, "ended_at": _utc_now(),
        }, with_rm, variants[0].metrics)

    report_path = None
    if report_dir is not None:
        report_dir.mkdir(parents=True, exist_ok=True)
        report_path = report_dir / f"{run_id}.html"
        report_path.write_text(render_backtest_report(ctx), encoding="utf-8")
    return BacktestRun(ctx, report_path, save)


def available_strategies() -> list[str]:
    return sorted(STRATEGIES)
