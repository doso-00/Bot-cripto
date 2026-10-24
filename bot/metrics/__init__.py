"""Metriche di performance di un backtest (brief §6.3)."""

import math

import numpy as np
import pandas as pd

from bot.backtest.engine import BacktestResult
from bot.data.klines import timeframe_ms

YEAR_MS = 365 * 24 * 3600 * 1000


def periods_per_year(timeframe: str) -> float:
    """Candele in un anno per un mercato 24/7: 8.760 sull'1h, 2.190 sul 4h."""
    return YEAR_MS / timeframe_ms(timeframe)


def max_drawdown_duration(equity: pd.DataFrame) -> pd.Timedelta:
    """Tempo massimo trascorso sotto un picco precedente (anche se non ancora recuperato)."""
    peak = equity["equity"].cummax()
    at_peak_time = equity["open_time"].where(equity["equity"] >= peak).ffill()
    return (equity["open_time"] - at_peak_time).max()


def compute_metrics(result: BacktestResult, timeframe: str) -> dict:
    eq = result.equity
    step = pd.Timedelta(milliseconds=timeframe_ms(timeframe))
    years = (eq["open_time"].iat[-1] + step - eq["open_time"].iat[0]) / pd.Timedelta(days=365.25)
    total_return = result.final_equity / result.initial_capital - 1
    cagr = (result.final_equity / result.initial_capital) ** (1 / years) - 1 if years > 0 else math.nan

    returns = eq["equity"].pct_change().dropna()
    ppy = periods_per_year(timeframe)
    std = returns.std()
    sharpe = returns.mean() / std * math.sqrt(ppy) if std > 0 else math.nan
    downside = math.sqrt((np.minimum(returns, 0) ** 2).mean()) if len(returns) else 0.0
    sortino = returns.mean() / downside * math.sqrt(ppy) if downside > 0 else math.nan
    max_dd = -eq["drawdown"].min()

    trades = result.trades
    wins = trades.loc[trades["net_pnl"] > 0, "net_pnl"].sum()
    losses = -trades.loc[trades["net_pnl"] < 0, "net_pnl"].sum()
    executed = result.orders[result.orders["status"] == "eseguito"] if len(result.orders) else result.orders
    fees = float(executed["fee"].sum()) if len(executed) else 0.0
    slippage = float((executed["qty"] * (executed["price_exec"] - executed["price_ref"]).abs()).sum()) if len(executed) else 0.0
    gross = float(trades["gross_pnl"].sum())

    return {
        "final_equity": result.final_equity,
        "total_return": total_return,
        "cagr": cagr,
        "max_drawdown": max_dd,
        "max_drawdown_duration": max_drawdown_duration(eq),
        "sharpe": sharpe,
        "sortino": sortino,
        "calmar": cagr / max_dd if max_dd > 0 else math.nan,
        "n_trades": len(trades),
        "win_rate": (trades["net_pnl"] > 0).mean() if len(trades) else math.nan,
        "profit_factor": wins / losses if losses > 0 else (math.inf if wins > 0 else math.nan),
        "avg_exposure": eq["exposure"].mean(),
        "fees": fees,
        "slippage_cost": slippage,
        "total_costs": fees + slippage,
        "costs_pct_of_gross": (fees + slippage) / gross if gross > 0 else math.nan,
        "n_rejected_orders": int((result.orders["status"] == "rifiutato").sum()) if len(result.orders) else 0,
        "n_risk_events": len(result.risk_events),
        "stopped_at": result.stopped_at,
        "n_drawdown_stops": result.n_stops,
    }


def alpha_vs_benchmark(strategy: dict, benchmark: dict) -> dict:
    """Differenze rispetto al buy & hold: rendimento (punti) e drawdown (relativo)."""
    bm_dd = benchmark["max_drawdown"]
    return {
        "return_diff": strategy["total_return"] - benchmark["total_return"],
        "max_drawdown_diff": strategy["max_drawdown"] - bm_dd,
        "max_drawdown_reduction": 1 - strategy["max_drawdown"] / bm_dd if bm_dd > 0 else math.nan,
    }


def warnings_for(metrics: dict, is_benchmark: bool = False) -> list[str]:
    """Avvisi automatici del report (brief §6.4). Un risultato troppo bello è un bug finché
    non è dimostrato il contrario (CLAUDE.md)."""
    out = []
    if not math.isnan(metrics["sharpe"]) and metrics["sharpe"] > 3:
        out.append(f"Sharpe sospettosamente alto ({metrics['sharpe']:.2f}): presumere un bug finché non è dimostrato il contrario.")
    if metrics["n_trades"] > 0 and metrics["max_drawdown"] < 0.01:
        out.append("Drawdown quasi zero: risultato sospetto, da verificare.")
    if not is_benchmark and metrics["n_trades"] < 30:
        out.append(f"Solo {metrics['n_trades']} operazioni (meno di 30): campione non significativo.")
    if not math.isnan(metrics["costs_pct_of_gross"]) and metrics["costs_pct_of_gross"] > 0.5:
        out.append(f"I costi assorbono il {metrics['costs_pct_of_gross']:.0%} del profitto lordo.")
    if metrics["stopped_at"] is not None:
        out.append(
            f"Lo stop per drawdown massimo è scattato {metrics.get('n_drawdown_stops', 1)} volte "
            f"(la prima il {metrics['stopped_at']}): in paper e live ogni stop richiede un riavvio manuale."
        )
    if metrics["n_rejected_orders"] > 0:
        out.append(f"{metrics['n_rejected_orders']} ordini rifiutati (filtri dell'exchange o risk manager).")
    return out
