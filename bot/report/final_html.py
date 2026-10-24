"""Report del test finale della Fase 3."""

import html

import plotly.graph_objects as go
import plotly.io as pio

from bot.backtest.periods import Period
from bot.backtest.storage import git_commit
from bot.evaluate.final import FinalTest
from bot.report.common import CSS, SERIES_COLORS, layout, num, pct, table


def _params(d: dict) -> str:
    return ", ".join(f"{k}={v:g}" if isinstance(v, float) else f"{k}={v}" for k, v in d.items())


def render_final_report(tests: list[FinalTest], period: Period, messages: list[str], generated_at, confirm_symbol: str) -> str:
    passed = [t.selection.strategy for t in tests if t.passed]
    verdict = (
        f"<div class='box ok'>Superano tutti i criteri sul periodo di test: <b>{', '.join(passed)}</b>. "
        "Possono passare alla Fase 4 (paper trading).</div>" if passed else
        "<div class='box'><b>Nessuna strategia supera tutti i criteri della Fase 3 sul periodo di test.</b> Per il brief "
        "il risultato corretto è scriverlo e fermarsi o rivedere le ipotesi. Non si abbassano i criteri e non si "
        "ripete il test con altri parametri.</div>"
    )
    sections, charts_js_done = [], False
    for t in tests:
        m, c = t.metrics, t.confirm_metrics
        fig = go.Figure()
        for color, (label, eq) in zip((SERIES_COLORS[0], SERIES_COLORS[2]), t.curves.items()):
            d = eq.set_index("open_time")["equity"].resample("D").last().dropna()
            fig.add_trace(go.Scatter(x=d.index, y=d, name=label, mode="lines", line={"color": color, "width": 2},
                                     hovertemplate="%{y:,.2f} USDT<extra>" + label + "</extra>"))
        layout(fig, f"{t.selection.strategy} — periodo di test", "USDT")
        chart = pio.to_html(fig, full_html=False, include_plotlyjs=not charts_js_done,
                            config={"displaylogo": False, "responsive": True})
        charts_js_done = True
        criteria = "".join(
            f"<div class='box {'ok' if x.passed else ''}'>{'✓' if x.passed else '✗'} <b>{html.escape(x.name)}</b>: "
            f"{html.escape(x.detail)}</div>" for x in t.criteria)
        metrics = table(
            ["Variante", "Rendimento", "Max drawdown", "Sharpe", "Operazioni", "Stop per drawdown"],
            [[k, pct(v["total_return"]), pct(v["max_drawdown"], 1), num(v["sharpe"]), str(v["n_trades"]),
              str(v.get("n_drawdown_stops", 0))] for k, v in m.items()]
            + [[f"{confirm_symbol}: {k}", pct(v["total_return"]), pct(v["max_drawdown"], 1), num(v["sharpe"]),
                str(v["n_trades"]), str(v.get("n_drawdown_stops", 0))] for k, v in c.items()],
        )
        regimes = table(
            ["Regime", "Quota del tempo", "Giorni", "Strategia", "Buy & hold"],
            [[r["regime"], pct(r["share_of_time"], 1), str(r["days"]), pct(r["strategia"]), pct(r["buy & hold"])]
             for _, r in t.regimes.iterrows()],
        )
        repeated = "<div class='box'>Test RIPETUTO su richiesta esplicita: il risultato vale meno del primo.</div>" if t.repeated else ""
        sections.append(f"""
<h2>{html.escape(t.selection.strategy)} — {'SUPERA' if t.passed else 'non supera'} i criteri</h2>
<p class="meta">Parametri congelati il {t.selection.created_at:%Y-%m-%d %H:%M} UTC (selezione
<code>{html.escape(t.selection.selection_id)}</code>): {html.escape(_params(t.selection.params))}</p>
{repeated}{criteria}{metrics}{chart}
<h3>Per regime di mercato</h3>{regimes}""")

    notes = "".join(f"<div class='box'>{html.escape(x)}</div>" for x in messages)
    return f"""<!doctype html>
<html lang="it"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Fase 3 — test finale</title><style>{CSS} h3 {{ font-size:14px; margin:20px 0 6px; }}</style></head>
<body><main>
<h1>Fase 3 — test finale</h1>
<div class="meta">Periodo di test {period.start:%Y-%m-%d} → {period.end:%Y-%m-%d %H:%M} UTC · commit
<code>{html.escape(git_commit())}</code> · generato il {generated_at:%Y-%m-%d %H:%M} UTC. Numeri <b>misurati</b> sulla
variante di valutazione, con le stesse ipotesi del report di confronto.</div>
<h2>Esito</h2>{verdict}{notes}
{"".join(sections) or "<p>Nessuna strategia testata.</p>"}
</main></body></html>
"""
