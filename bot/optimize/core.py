"""Ottimizzazione dei parametri sul solo training (brief §6.2 punti 2-4).

- Ogni combinazione della griglia si valuta con la variante di valutazione (risk manager
  senza stop per drawdown, decisione A) e i costi realistici.
- Funzione obiettivo: Sharpe annualizzato. Il rendimento puro premierebbe chi rischia di
  più; lo Sharpe confronta il rendimento con la sua variabilità.
- Scelta finale "robusta": non la combinazione con lo Sharpe più alto, ma quella con il
  punteggio robusto più alto = min(Sharpe della combinazione, media degli Sharpe delle
  vicine), dove "vicine" = un passo della griglia su un solo parametro. Vince solo una
  combinazione buona circondata da combinazioni buone: un "picco isolato" ha vicine
  scarse, e una combinazione scarsa vicina a un picco ha a sua volta Sharpe scarso
  (brief §6.2 punto 4). La semplice media "combinazione + vicine" non basterebbe: premierebbe
  le vicine di un picco.
- Walk-forward: si sceglie la combinazione migliore su `is_months` mesi, la si applica ai
  `oos_months` successivi, si avanza di `step_months`. I risultati fuori campione dicono se
  il procedimento di ottimizzazione regge su dati che non ha visto.
"""

import math
from dataclasses import dataclass
from datetime import datetime

import pandas as pd
import yaml
from pydantic import BaseModel

from bot.backtest.engine import BacktestConfig, BacktestResult
from bot.backtest.runner import simulate
from bot.config.risk import RiskLimits
from bot.exchange.rounding import SymbolFilters
from bot.metrics import compute_metrics
from bot.strategies import STRATEGY_CONFIG_DIR, StrategySpec, compute_targets


@dataclass
class ComboResult:
    params: BaseModel
    sharpe: float
    total_return: float
    max_drawdown: float
    n_trades: int

    @property
    def key(self) -> str:
        return self.params.model_dump_json()

    @property
    def score(self) -> float:
        """Sharpe usato per confrontare le combinazioni (nessuna operazione -> 0)."""
        return 0.0 if math.isnan(self.sharpe) else self.sharpe


@dataclass
class Fold:
    number: int
    is_start: datetime
    is_end: datetime
    oos_start: datetime
    oos_end: datetime
    params: dict | None          # None: nessuna combinazione idonea nella finestra
    is_sharpe: float
    oos_return: float
    oos_bh_return: float
    oos_trades: int
    oos_max_drawdown: float


class Evaluator:
    """Esegue una strategia su intervalli di una stessa storia, con i target calcolati una
    sola volta per combinazione sull'intera storia (gli indicatori usano solo il passato,
    quindi i target di un intervallo non cambiano se la storia continua dopo)."""

    def __init__(self, spec: StrategySpec, history: pd.DataFrame, cfg: BacktestConfig,
                 filters: SymbolFilters, limits: RiskLimits, timeframe: str):
        self.spec, self.cfg, self.filters, self.limits, self.timeframe = spec, cfg, filters, limits, timeframe
        self.history = history.reset_index(drop=True)
        self._targets: dict[str, pd.Series] = {}

    def targets(self, params: BaseModel) -> pd.Series:
        key = params.model_dump_json()
        if key not in self._targets:
            self._targets[key] = compute_targets(self.spec, self.history, params)
        return self._targets[key]

    def _mask(self, start: datetime, end: datetime) -> pd.Series:
        return (self.history["open_time"] >= start) & (self.history["open_time"] < end)

    def run(self, params: BaseModel, start: datetime, end: datetime, policy: str = "off",
            cfg: BacktestConfig | None = None) -> BacktestResult:
        mask = self._mask(start, end)
        return simulate(self.history[mask], self.targets(params)[mask], cfg or self.cfg, self.filters,
                        self.limits, policy)

    def run_benchmark(self, start: datetime, end: datetime, cfg: BacktestConfig | None = None) -> BacktestResult:
        mask = self._mask(start, end)
        ones = pd.Series(1.0, index=self.history.index[mask])
        return simulate(self.history[mask], ones, cfg or self.cfg, self.filters, None)

    def evaluate(self, params: BaseModel, start: datetime, end: datetime) -> ComboResult:
        m = compute_metrics(self.run(params, start, end), self.timeframe)
        return ComboResult(params, m["sharpe"], m["total_return"], m["max_drawdown"], m["n_trades"])


def grid_axes(spec: StrategySpec) -> dict[str, list]:
    """Valori della griglia per parametro, nell'ordine di config/strategies/<nome>.yaml."""
    path = STRATEGY_CONFIG_DIR / f"{spec.name}.yaml"
    return (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("grid") or {}


def are_neighbors(a: BaseModel, b: BaseModel, axes: dict[str, list]) -> bool:
    """Vicine = differiscono di un solo passo della griglia su un solo parametro."""
    return sum(abs(axes[n].index(getattr(a, n)) - axes[n].index(getattr(b, n))) for n in axes) == 1


def select_robust(results: list[ComboResult], axes: dict[str, list], min_trades: int) -> tuple[ComboResult, pd.DataFrame, str | None]:
    """Combinazione con il punteggio robusto più alto tra quelle idonee (vedi docstring del modulo)."""
    rows = []
    for r in results:
        neigh = [o for o in results if o is not r and are_neighbors(r.params, o.params, axes)]
        neigh_mean = sum(o.score for o in neigh) / len(neigh) if neigh else math.nan
        robust = min(r.score, neigh_mean) if neigh else r.score
        rows.append({
            "params": r.params.model_dump(), "key": r.key, "sharpe": r.sharpe, "total_return": r.total_return,
            "max_drawdown": r.max_drawdown, "n_trades": r.n_trades, "n_neighbors": len(neigh),
            "neighbors_sharpe": neigh_mean, "robust_score": robust, "eligible": r.n_trades >= min_trades,
        })
    table = pd.DataFrame(rows).sort_values("robust_score", ascending=False).reset_index(drop=True)
    note = None
    pool = table[table["eligible"]]
    if pool.empty:
        note = f"Nessuna combinazione con almeno {min_trades} operazioni sul training: scelta tra tutte."
        pool = table
    best_key = pool["key"].iat[0]
    return next(r for r in results if r.key == best_key), table, note


def walk_forward_windows(start: datetime, end: datetime, is_months: int, oos_months: int,
                         step_months: int) -> list[tuple[datetime, datetime, datetime, datetime]]:
    """Finestre (inizio IS, fine IS, inizio OOS, fine OOS) interamente dentro [start, end)."""
    windows = []
    is_start = pd.Timestamp(start)
    while True:
        is_end = is_start + pd.DateOffset(months=is_months)
        oos_end = is_end + pd.DateOffset(months=oos_months)
        if oos_end > pd.Timestamp(end):
            return windows
        windows.append((is_start.to_pydatetime(), is_end.to_pydatetime(), is_end.to_pydatetime(), oos_end.to_pydatetime()))
        is_start += pd.DateOffset(months=step_months)


def walk_forward(evaluator: Evaluator, combos: list[BaseModel], windows: list[tuple], min_trades: int) -> list[Fold]:
    folds = []
    for n, (is_start, is_end, oos_start, oos_end) in enumerate(windows, start=1):
        candidates = [evaluator.evaluate(p, is_start, is_end) for p in combos]
        eligible = [c for c in candidates if c.n_trades >= min_trades]
        bh = evaluator.run_benchmark(oos_start, oos_end)
        bh_return = bh.final_equity / bh.initial_capital - 1
        if not eligible:
            folds.append(Fold(n, is_start, is_end, oos_start, oos_end, None, math.nan, 0.0, bh_return, 0, 0.0))
            continue
        best = max(eligible, key=lambda c: c.score)
        oos = compute_metrics(evaluator.run(best.params, oos_start, oos_end), evaluator.timeframe)
        folds.append(Fold(n, is_start, is_end, oos_start, oos_end, best.params.model_dump(), best.sharpe,
                          oos["total_return"], bh_return, oos["n_trades"], oos["max_drawdown"]))
    return folds


def summarize_folds(folds: list[Fold]) -> dict:
    """Rendimento composto fuori campione della strategia e del buy & hold sulle stesse finestre."""
    strat = math.prod(1 + f.oos_return for f in folds) - 1 if folds else math.nan
    bh = math.prod(1 + f.oos_bh_return for f in folds) - 1 if folds else math.nan
    chosen = [str(f.params) for f in folds if f.params is not None]
    return {
        "n_folds": len(folds),
        "oos_return": strat,
        "oos_bh_return": bh,
        "folds_beating_bh": sum(f.oos_return > f.oos_bh_return for f in folds),
        "folds_without_choice": sum(f.params is None for f in folds),
        "distinct_params": len(set(chosen)),
    }
