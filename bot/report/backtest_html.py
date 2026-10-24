"""Report HTML statico di un backtest (brief §6.4), apribile da browser senza server.

Contenuto: parametri e hash, avvisi automatici, cosa è misurato e cosa è ipotesi, verifica
del buy & hold, tabella metriche, equity e drawdown contro il buy & hold, istogramma dei
rendimenti per operazione, risultati per regime, sensibilità allo slippage, operazioni ed
eventi del risk manager.
"""

import html
import json
from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio

from bot.backtest.engine import BacktestResult
from bot.report.common import CSS, SERIES_COLORS, duration, layout, num, pct, table

@dataclass
class Variant:
    label: str
    result: BacktestResult
    metrics: dict


@dataclass
class ReportContext:
    run_id: str
    strategy: str
    symbol: str
    timeframe: str
    period_name: str
    period_start: datetime
    period_end: datetime
    params: dict
    config_hash: str
    git_commit: str
    generated_at: datetime
    variants: list[Variant]                 # [valutazione, stop con riavvio simulato, buy & hold]
    alpha: dict[str, dict]                  # etichetta variante -> differenze vs buy & hold
    warnings: list[str]
    bh_check: dict
    regimes: pd.DataFrame
    sensitivity: pd.DataFrame
    data_quality: str
    assumptions: list[str] = field(default_factory=list)


def _daily(result: BacktestResult) -> pd.DataFrame:
    """Equity a fine giornata e drawdown minimo del giorno: grafici leggeri anche su 6 anni di 1h."""
    eq = result.equity.set_index("open_time")
    return pd.DataFrame({"equity": eq["equity"].resample("D").last(), "drawdown": eq["drawdown"].resample("D").min()}).dropna()


def _charts(ctx: ReportContext) -> list[str]:
    equity_fig, dd_fig = go.Figure(), go.Figure()
    for color, v in zip(SERIES_COLORS, ctx.variants):
        d = _daily(v.result)
        equity_fig.add_trace(go.Scatter(x=d.index, y=d["equity"], name=v.label, mode="lines",
                                        line={"color": color, "width": 2},
                                        hovertemplate="%{y:,.2f} USDT<extra>" + v.label + "</extra>"))
        dd_fig.add_trace(go.Scatter(x=d.index, y=d["drawdown"], name=v.label, mode="lines",
                                    line={"color": color, "width": 2},
                                    hovertemplate="%{y:.1%}<extra>" + v.label + "</extra>"))
    layout(equity_fig, "Equity (scala logaritmica, valore a fine giornata)", "USDT", log_y=True)
    layout(dd_fig, "Drawdown dal picco (minimo del giorno)", "", y_format=".0%")

    figures = [equity_fig, dd_fig]
    trades = ctx.variants[0].result.trades
    if len(trades):
        hist = go.Figure(go.Histogram(x=trades["return_pct"], nbinsx=40, marker={"color": SERIES_COLORS[0]},
                                      name=ctx.variants[0].label, hovertemplate="%{x:.1%}: %{y} operazioni<extra></extra>"))
        hist.update_layout(bargap=0.08, showlegend=False)
        layout(hist, f"Rendimento netto per operazione — {ctx.variants[0].label}", "operazioni", y_format=None)
        hist.update_xaxes(tickformat=".0%")
        hist.update_layout(hovermode="closest")
        figures.append(hist)

    out = []
    for i, fig in enumerate(figures):
        out.append(pio.to_html(fig, full_html=False, include_plotlyjs=(i == 0),
                               config={"displaylogo": False, "responsive": True}))
    return out


def _metricstable(ctx: ReportContext) -> str:
    labels = [v.label for v in ctx.variants]
    m = [v.metrics for v in ctx.variants]
    rows = [
        ["Equity finale (USDT)", *(num(x["final_equity"]) for x in m)],
        ["Rendimento netto totale", *(pct(x["total_return"]) for x in m)],
        ["CAGR", *(pct(x["cagr"]) for x in m)],
        ["Max drawdown", *(pct(x["max_drawdown"]) for x in m)],
        ["Durata max drawdown", *(duration(x["max_drawdown_duration"]) for x in m)],
        ["Sharpe", *(num(x["sharpe"]) for x in m)],
        ["Sortino", *(num(x["sortino"]) for x in m)],
        ["Calmar", *(num(x["calmar"]) for x in m)],
        ["Operazioni", *(str(x["n_trades"]) for x in m)],
        ["Win rate", *(pct(x["win_rate"], 1) for x in m)],
        ["Profit factor", *(num(x["profit_factor"]) for x in m)],
        ["Esposizione media", *(pct(x["avg_exposure"], 1) for x in m)],
        ["Commissioni (USDT)", *(num(x["fees"]) for x in m)],
        ["Slippage (USDT)", *(num(x["slippage_cost"]) for x in m)],
        ["Costi in % del profitto lordo", *(pct(x["costs_pct_of_gross"], 1) for x in m)],
        ["Ordini rifiutati", *(str(x["n_rejected_orders"]) for x in m)],
        ["Interventi del risk manager", *(str(x["n_risk_events"]) for x in m)],
        ["Stop per drawdown massimo", *(str(x.get("n_drawdown_stops", 0)) for x in m)],
    ]
    for label, a in ctx.alpha.items():
        idx = labels.index(label)
        cells = [""] * len(labels)
        cells[idx] = f"{a['return_diff'] * 100:+.2f} punti"
        rows.append(["Rendimento vs buy & hold", *cells])
        cells = [""] * len(labels)
        cells[idx] = pct(a["max_drawdown_reduction"], 1)
        rows.append(["Riduzione del max drawdown vs buy & hold", *cells])
    return table(["Metrica", *labels], rows)


def render_backtest_report(ctx: ReportContext) -> str:
    charts = _charts(ctx)
    warnings = "".join(f"<div class='box'>{html.escape(w)}</div>" for w in ctx.warnings) or \
        "<div class='box ok'>Nessun avviso automatico.</div>"

    c = ctx.bh_check
    bh_ok = abs(c["diff"]) < 0.0001
    bh_box = (
        f"<div class='box {'ok' if bh_ok else ''}'>Buy &amp; hold: motore {c['engine']:,.4f} USDT, calcolo manuale "
        f"{c['manual']:,.4f} USDT, differenza {c['diff'] * 100:+.5f}% (soglia ±0,01%: {'superata' if bh_ok else 'NON superata'}). "
        f"Formula senza arrotondamenti allo step size: {c['formula']:,.4f} USDT.</div>"
    )

    regime_cols = [v.label for v in ctx.variants]
    regimes = table(
        ["Regime", "Quota del tempo", "Giorni", *regime_cols],
        [[r["regime"], pct(r["share_of_time"], 1), str(r["days"]), *(pct(r[c_]) for c_ in regime_cols)]
         for _, r in ctx.regimes.iterrows()],
    )
    sensitivity = table(
        ["Slippage per lato", *[c_ for c_ in ctx.sensitivity.columns if c_ != "slippage"]],
        [[pct(r["slippage"], 2), *(pct(r[c_]) if "rendimento" in c_ else str(int(r[c_]))
                                    for c_ in ctx.sensitivity.columns if c_ != "slippage")]
         for _, r in ctx.sensitivity.iterrows()],
    )

    trades = ctx.variants[0].result.trades
    trade_rows = [
        [str(t.trade_id), str(t.entry_time), str(t.exit_time), num(t.entry_price), num(t.exit_price), num(t.qty, 5),
         num(t.gross_pnl), num(t.fees), num(t.slippage_cost), num(t.net_pnl), pct(t.return_pct), t.exit_reason]
        for t in trades.itertuples()
    ]
    trades_html = table(["#", "Entrata", "Uscita", "Prezzo entrata", "Prezzo uscita", "Quantità", "P&L lordo",
                          "Commissioni", "Slippage", "P&L netto", "Rendimento", "Motivo uscita"],
                         trade_rows, numeric_from=3) if trade_rows else "<p>Nessuna operazione.</p>"
    events = ctx.variants[0].result.risk_events
    events_html = table(["Quando", "Regola", "Dettaglio", "Azione"],
                         [[str(e.ts), e.rule, e.detail, e.action_taken] for e in events.itertuples()],
                         numeric_from=99) if len(events) else "<p>Nessun intervento.</p>"

    params = html.escape(json.dumps(ctx.params, ensure_ascii=False))
    return f"""<!doctype html>
<html lang="it"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Backtest {html.escape(ctx.strategy)} {html.escape(ctx.symbol)} {html.escape(ctx.timeframe)}</title>
<style>{CSS}</style></head>
<body><main>
<h1>Backtest {html.escape(ctx.strategy)} — {html.escape(ctx.symbol)} {html.escape(ctx.timeframe)}</h1>
<div class="meta">Periodo <b>{ctx.period_name}</b>: {ctx.period_start:%Y-%m-%d} → {ctx.period_end:%Y-%m-%d %H:%M} UTC ·
parametri {params} · run <code>{html.escape(ctx.run_id)}</code> · hash configurazione <code>{ctx.config_hash}</code> ·
commit <code>{html.escape(ctx.git_commit)}</code> · generato il {ctx.generated_at:%Y-%m-%d %H:%M} UTC</div>
<div class="meta">{html.escape(ctx.data_quality)}</div>

<h2>Avvisi automatici</h2>
{warnings}

<h2>Verifica del motore</h2>
{bh_box}

<h2>Metriche</h2>
<p class="meta">Tutti i valori sono <b>misurati</b> sui dati storici con le ipotesi elencate sotto. La variante di
<b>valutazione</b> è quella su cui si applicano i criteri della Fase 3 (risk manager senza stop per drawdown). Il buy &amp;
hold è il benchmark da battere ed è sempre calcolato senza risk manager.</p>
{_metricstable(ctx)}

<h2>Equity e drawdown</h2>
{charts[0]}
{charts[1]}

<h2>Operazioni</h2>
{charts[2] if len(charts) > 2 else ""}
<div class="scroll">{trades_html}</div>

<h2>Risultati per regime di mercato</h2>
<p class="meta">Regime del giorno dal rendimento dei 90 giorni precedenti (oltre ±15%), calcolato solo con dati passati.
Rendimento composto delle candele che cadono in ciascun regime.</p>
{regimes}

<h2>Sensibilità allo slippage</h2>
{sensitivity}

<h2>Interventi del risk manager — {html.escape(ctx.variants[0].label)}</h2>
<div class="scroll">{events_html}</div>

<h2>Misurato e ipotesi</h2>
<p class="meta"><b>Misurato</b>: ogni numero di questo report, calcolato su candele storiche reali verificate dal
controllo qualità. <b>Ipotesi</b> del modello, che il paper trading dovrà confermare:</p>
<ul>{"".join(f"<li>{html.escape(a)}</li>" for a in ctx.assumptions)}</ul>
</main></body></html>
"""
