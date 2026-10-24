"""Salvataggio di un run di backtest nelle tabelle runs e bt_*."""

import hashlib
import json
import math
import subprocess
from datetime import datetime

import duckdb
import pandas as pd

from bot.backtest.engine import BacktestResult
from bot.config import PROJECT_ROOT


def config_hash(payload: dict) -> str:
    """Hash stabile della configurazione: stessi parametri -> stesso hash."""
    text = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def git_commit() -> str:
    """Commit corrente, con '+modifiche' se ci sono file non committati."""
    try:
        sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=PROJECT_ROOT,
                             capture_output=True, text=True, check=True).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=PROJECT_ROOT,
                               capture_output=True, text=True, check=True).stdout.strip()
        return sha + ("+modifiche" if dirty else "")
    except (OSError, subprocess.CalledProcessError):
        return "sconosciuto"


def _json_safe(metrics: dict) -> str:
    def clean(v):
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            return None
        if isinstance(v, (pd.Timedelta, pd.Timestamp, datetime)):
            return str(v)
        return v
    return json.dumps({k: clean(v) for k, v in metrics.items()})


def previous_test_runs(con: duckdb.DuckDBPyConnection, strategy: str, symbol: str, timeframe: str) -> list[tuple]:
    return con.execute(
        "SELECT run_id, started_at FROM runs WHERE mode = 'backtest' AND period_name = 'test' "
        "AND strategy = ? AND symbol = ? AND timeframe = ? ORDER BY started_at",
        [strategy, symbol, timeframe],
    ).fetchall()


def save_run(con: duckdb.DuckDBPyConnection, run: dict, result: BacktestResult, metrics: dict) -> None:
    """`run` contiene le colonne della tabella runs (tranne final_equity e metrics_json)."""
    run_id = run["run_id"]
    con.execute("BEGIN TRANSACTION")
    try:
        con.execute(
            """
            INSERT INTO runs (run_id, mode, strategy, symbol, timeframe, period_name, period_start, period_end,
                              params_json, risk_enabled, config_hash, git_commit, started_at, ended_at,
                              final_equity, metrics_json)
            VALUES (?, 'backtest', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [run_id, run["strategy"], run["symbol"], run["timeframe"], run["period_name"], run["period_start"],
             run["period_end"], run["params_json"], run["risk_enabled"], run["config_hash"], run["git_commit"],
             run["started_at"], run["ended_at"], result.final_equity, _json_safe(metrics)],
        )
        tables = {
            "bt_trades": result.trades,
            "bt_equity": result.equity.rename(columns={"open_time": "ts"})[
                ["ts", "cash_usdt", "position_qty", "position_value", "equity", "drawdown", "target_exposure"]
            ],
            "bt_orders": result.orders.reindex(
                columns=["ts", "side", "qty", "price_ref", "price_exec", "fee", "status", "reason"]
            ),
            "bt_risk_events": result.risk_events,
        }
        for table, frame in tables.items():
            if frame.empty:
                continue
            con.register("staging_run", frame.assign(run_id=run_id))
            cols = ", ".join(["run_id", *frame.columns])
            con.execute(f"INSERT INTO {table} ({cols}) SELECT {cols} FROM staging_run")
            con.unregister("staging_run")
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
