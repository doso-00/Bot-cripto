"""Motore di backtest event-driven, una candela alla volta (brief §6.1).

Ciclo per ogni candela i:
1. apertura: si esegue l'esposizione target decisa alla chiusura della candela i-1, al
   prezzo di apertura più lo slippage nella direzione sfavorevole (niente look-ahead);
   se la candela ha volume zero (nessuno scambio possibile, es. durante un fermo) l'ordine
   slitta alla candela successiva;
2. chiusura: si valuta l'equity al close, il risk manager controlla perdita giornaliera e
   drawdown, la strategia fornisce il nuovo target (già calcolato, vedi bot/strategies).
A fine periodo l'eventuale posizione viene chiusa al close dell'ultima candela, con costi,
così il rendimento finale è al netto anche dell'uscita.

Ipotesi di modellazione (riportate nel report):
- commissione calcolata sul controvalore e pagata in USDT (Binance la trattiene
  nell'asset ricevuto: il costo in valore è lo stesso, ma così non restano frazioni di
  BTC non vendibili);
- quantità arrotondata per difetto allo step size, ordine scartato sotto quantità minima
  o notional minimo;
- nessun interesse sugli USDT inattivi.
"""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

import pandas as pd

from bot.exchange.rounding import SymbolFilters, order_rejection_reason, round_qty_down
from bot.risk.manager import RiskManager


@dataclass(frozen=True)
class BacktestConfig:
    initial_capital: float
    fee_rate: float
    slippage_rate: float
    min_rebalance_delta: float


@dataclass
class BacktestResult:
    equity: pd.DataFrame
    trades: pd.DataFrame
    orders: pd.DataFrame
    risk_events: pd.DataFrame
    initial_capital: float
    final_equity: float
    stopped_at: datetime | None = None


TRADE_COLUMNS = [
    "trade_id", "entry_time", "exit_time", "entry_price", "exit_price", "qty",
    "gross_pnl", "fees", "slippage_cost", "net_pnl", "return_pct", "exit_reason",
]


def execution_price(reference: float, side: str, slippage_rate: float) -> float:
    """Lo slippage peggiora sempre il prezzo: si compra più caro e si vende più a buon mercato."""
    return reference * (1 + slippage_rate) if side == "buy" else reference * (1 - slippage_rate)


@dataclass
class _OpenTrade:
    entry_time: datetime
    buy_qty: float = 0.0
    buy_ref: float = 0.0       # controvalore ai prezzi di riferimento (open), senza costi
    buy_exec: float = 0.0      # controvalore ai prezzi eseguiti (con slippage)
    sell_qty: float = 0.0
    sell_ref: float = 0.0
    sell_exec: float = 0.0
    fees: float = 0.0
    entry_cost: float = 0.0    # cassa spesa in entrata (eseguito + commissioni)


@dataclass
class _State:
    cash: float
    qty: Decimal = Decimal(0)
    trade: _OpenTrade | None = None
    trades: list[dict] = field(default_factory=list)
    orders: list[dict] = field(default_factory=list)


def run_backtest(
    candles: pd.DataFrame,
    targets: pd.Series,
    config: BacktestConfig,
    filters: SymbolFilters,
    risk: RiskManager | None = None,
) -> BacktestResult:
    """`candles` e `targets` allineati riga per riga, già limitati al periodo da simulare."""
    if len(candles) != len(targets):
        raise ValueError("candles e targets devono avere la stessa lunghezza")
    candles = candles.reset_index(drop=True)
    targets = targets.reset_index(drop=True)

    times = candles["open_time"].tolist()
    opens = candles["open"].to_numpy()
    closes = candles["close"].to_numpy()
    volumes = candles["volume"].to_numpy()
    s = _State(cash=config.initial_capital)
    rows = []
    pending: float | None = None
    stopped_at = None

    for i, ts in enumerate(times):
        if risk is not None:
            risk.on_candle_open(ts, s.cash + float(s.qty) * opens[i])
        if pending is not None and volumes[i] > 0:
            _rebalance(s, ts, opens[i], pending, config, filters, risk)
            pending = None

        position_value = float(s.qty) * closes[i]
        equity = s.cash + position_value
        if risk is not None:
            was_stopped = risk.stopped
            risk.on_candle_close(ts, equity)
            if risk.stopped and not was_stopped:
                stopped_at = ts
        target = 0.0 if (risk is not None and risk.stopped) else float(targets.iat[i])
        pending = target
        rows.append((ts, s.cash, float(s.qty), position_value, equity, target))

    if s.qty > 0:  # chiusura forzata a fine periodo, al close dell'ultima candela
        _execute(s, times[-1], closes[-1], "sell", s.qty, config, filters, None, exit_reason="fine periodo")

    equity_df = pd.DataFrame(rows, columns=["open_time", "cash_usdt", "position_qty", "position_value", "equity", "target_exposure"])
    equity_df["exposure"] = equity_df["position_value"] / equity_df["equity"]
    equity_df["drawdown"] = equity_df["equity"] / equity_df["equity"].cummax() - 1
    return BacktestResult(
        equity=equity_df,
        trades=pd.DataFrame(s.trades, columns=TRADE_COLUMNS),
        orders=pd.DataFrame(s.orders),
        risk_events=pd.DataFrame([e.__dict__ for e in risk.events] if risk else [], columns=["ts", "rule", "detail", "action_taken"]),
        initial_capital=config.initial_capital,
        final_equity=s.cash + float(s.qty) * closes[-1],
        stopped_at=stopped_at,
    )


def _rebalance(s: _State, ts, reference: float, target: float, config: BacktestConfig,
               filters: SymbolFilters, risk: RiskManager | None) -> None:
    position_value = float(s.qty) * reference
    equity = s.cash + position_value
    current = position_value / equity if equity > 0 else 0.0
    delta = target - current
    if abs(delta) < config.min_rebalance_delta:
        return

    if delta > 0:
        notional = delta * equity
        if risk is not None:
            notional = risk.review_order(ts, "buy", notional, equity, position_value)
            if notional <= 0:
                s.orders.append({"ts": ts, "side": "buy", "qty": 0.0, "status": "rifiutato", "reason": "risk manager"})
                return
        notional = min(notional, s.cash / (1 + config.fee_rate))
        exec_price = execution_price(reference, "buy", config.slippage_rate)
        qty = round_qty_down(notional / exec_price, filters.step_size)
        _execute(s, ts, reference, "buy", qty, config, filters, risk)
    else:
        if target == 0:
            qty = s.qty  # uscita completa: si vende tutto, già multiplo dello step size
        else:
            qty = round_qty_down(-delta * equity / reference, filters.step_size)
        if risk is not None and risk.review_order(ts, "sell", float(qty) * reference, equity, position_value) <= 0:
            s.orders.append({"ts": ts, "side": "sell", "qty": float(qty), "status": "rifiutato", "reason": "risk manager"})
            return
        reason = ("risk manager" if risk is not None and risk.stopped else "segnale") if target == 0 else None
        _execute(s, ts, reference, "sell", qty, config, filters, risk, exit_reason=reason)


def _execute(s: _State, ts, reference: float, side: str, qty: Decimal, config: BacktestConfig,
             filters: SymbolFilters, risk: RiskManager | None, exit_reason: str | None = None) -> None:
    exec_price = execution_price(reference, side, config.slippage_rate)
    reason = order_rejection_reason(qty, exec_price, filters)
    if reason:
        s.orders.append({"ts": ts, "side": side, "qty": float(qty), "status": "rifiutato", "reason": reason})
        return

    q = float(qty)
    value = q * exec_price
    fee = value * config.fee_rate
    if side == "buy":
        s.cash -= value + fee
        s.qty += qty
        if s.trade is None:
            s.trade = _OpenTrade(entry_time=ts)
        t = s.trade
        t.buy_qty += q
        t.buy_ref += q * reference
        t.buy_exec += value
        t.entry_cost += value + fee
    else:
        s.cash += value - fee
        s.qty -= qty
        t = s.trade
        t.sell_qty += q
        t.sell_ref += q * reference
        t.sell_exec += value
    t.fees += fee
    s.orders.append({
        "ts": ts, "side": side, "qty": q, "price_ref": reference, "price_exec": exec_price,
        "fee": fee, "status": "eseguito", "reason": None,
    })
    if risk is not None:
        risk.record_order(ts)

    if side == "sell" and s.qty == 0:
        gross = t.sell_ref - t.buy_ref
        slippage = (t.buy_exec - t.buy_ref) + (t.sell_ref - t.sell_exec)
        net = gross - t.fees - slippage
        s.trades.append({
            "trade_id": len(s.trades) + 1,
            "entry_time": t.entry_time,
            "exit_time": ts,
            "entry_price": t.buy_exec / t.buy_qty,
            "exit_price": t.sell_exec / t.sell_qty,
            "qty": t.buy_qty,
            "gross_pnl": gross,
            "fees": t.fees,
            "slippage_cost": slippage,
            "net_pnl": net,
            "return_pct": net / t.entry_cost,
            "exit_reason": exit_reason or "segnale",
        })
        s.trade = None
