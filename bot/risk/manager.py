"""Risk manager: stesse regole in backtest, paper e live (brief §7).

Regole implementate qui (servono già al backtest):
- importo massimo per ordine di acquisto (frazione del capitale); le vendite non sono
  limitate, perché un limite di rischio non deve ritardare la riduzione del rischio
  (docs/DECISIONI.md, 2026-10-23);
- esposizione massima per simbolo;
- perdita giornaliera massima: raggiunta, niente nuovi acquisti fino a mezzanotte UTC;
- drawdown massimo dal picco: raggiunto, si chiude la posizione e il bot si ferma.
  La politica `drawdown_policy` decide cosa succede dopo (decisione A, docs/DECISIONI.md):
  "stop" (paper e live: serve un riavvio manuale), "restart" (solo backtest: si simula il
  riavvio manuale dopo `backtest_restart_after_days` giorni, con il picco azzerato),
  "off" (solo backtest: regola non applicata, per valutare la strategia nella Fase 3);
- ordini massimi per ora.
Le regole che dipendono dall'ambiente reale (dati vecchi, divergenza di prezzo, kill
switch, riconciliazione) arrivano con il paper trading (Fase 4).

Ogni intervento viene registrato in `events` (tabella risk_events / bt_risk_events).
"""

from collections import deque
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Literal

from bot.config.risk import RiskLimits


@dataclass
class RiskEvent:
    ts: datetime
    rule: str
    detail: str
    action_taken: str


DrawdownPolicy = Literal["stop", "restart", "off"]


class RiskManager:
    def __init__(self, limits: RiskLimits, drawdown_policy: DrawdownPolicy = "stop"):
        self.limits = limits
        self.drawdown_policy = drawdown_policy
        self.stopped_since: datetime | None = None
        self.n_stops = 0
        self.peak_equity: float | None = None
        self.day: date | None = None
        self.day_start_equity: float | None = None
        self.buys_blocked_today = False
        self.stopped = False
        self._order_times: deque[datetime] = deque()
        self.events: list[RiskEvent] = []

    def _event(self, ts: datetime, rule: str, detail: str, action: str) -> None:
        self.events.append(RiskEvent(ts, rule, detail, action))

    def on_candle_open(self, ts: datetime, equity: float) -> None:
        """Da chiamare all'apertura di ogni candela, prima di eseguire ordini."""
        if self.day != ts.date():
            self.day = ts.date()
            self.day_start_equity = equity
            if self.buys_blocked_today:
                self._event(ts, "max_daily_loss", "nuovo giorno UTC", "acquisti di nuovo consentiti")
            self.buys_blocked_today = False
        if self.peak_equity is None:
            self.peak_equity = equity
        if (self.stopped and self.drawdown_policy == "restart"
                and ts >= self.stopped_since + timedelta(days=self.limits.backtest_restart_after_days)):
            self.stopped = False
            self.peak_equity = equity
            self._event(ts, "max_drawdown", f"riavvio simulato dopo {self.limits.backtest_restart_after_days} giorni",
                        "operatività ripresa, picco azzerato all'equity attuale")

    def on_candle_close(self, ts: datetime, equity: float) -> None:
        """Da chiamare alla chiusura di ogni candela con l'equity valutata al close."""
        self.peak_equity = max(self.peak_equity or equity, equity)
        drawdown = 1 - equity / self.peak_equity
        if self.drawdown_policy != "off" and not self.stopped and drawdown >= self.limits.max_drawdown:
            self.stopped = True
            self.stopped_since = ts
            self.n_stops += 1
            self._event(
                ts, "max_drawdown",
                f"drawdown {drawdown:.2%} dal picco {self.peak_equity:.2f} (limite {self.limits.max_drawdown:.0%})",
                "chiusura della posizione e stop: serve un riavvio manuale" if self.drawdown_policy == "stop"
                else "chiusura della posizione e stop (riavvio simulato più avanti)",
            )
        if self.day_start_equity:
            daily_loss = 1 - equity / self.day_start_equity
            if not self.buys_blocked_today and daily_loss >= self.limits.max_daily_loss:
                self.buys_blocked_today = True
                self._event(
                    ts, "max_daily_loss",
                    f"perdita del giorno {daily_loss:.2%} (limite {self.limits.max_daily_loss:.0%})",
                    "nuovi acquisti bloccati fino a mezzanotte UTC",
                )

    def review_order(self, ts: datetime, side: str, notional: float, equity: float, position_value: float) -> float:
        """Restituisce il controvalore consentito (0 = ordine rifiutato)."""
        while self._order_times and self._order_times[0] <= ts - timedelta(hours=1):
            self._order_times.popleft()
        if len(self._order_times) >= self.limits.max_orders_per_hour:
            self._event(ts, "max_orders_per_hour", f"{len(self._order_times)} ordini nell'ultima ora", "ordine rifiutato")
            return 0.0
        if side == "sell":
            return notional

        if self.stopped:
            self._event(ts, "max_drawdown", "bot fermo per drawdown massimo", "acquisto rifiutato")
            return 0.0
        if self.buys_blocked_today:
            self._event(ts, "max_daily_loss", "acquisti bloccati per oggi", "acquisto rifiutato")
            return 0.0

        allowed = notional
        order_cap = self.limits.max_order_fraction * equity
        exposure_room = max(self.limits.max_exposure_per_symbol * equity - position_value, 0.0)
        if allowed > order_cap:
            self._event(ts, "max_order_fraction", f"richiesti {allowed:.2f}, massimo {order_cap:.2f}", "ordine ridotto")
            allowed = order_cap
        if allowed > exposure_room:
            self._event(ts, "max_exposure_per_symbol", f"richiesti {allowed:.2f}, spazio {exposure_room:.2f}", "ordine ridotto")
            allowed = exposure_room
        return allowed

    def record_order(self, ts: datetime) -> None:
        self._order_times.append(ts)
