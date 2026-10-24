"""Report comparativo della Fase 3 (training e validazione, mai il test)."""

import html
import json
import math

import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio

from bot.backtest.storage import git_commit
from bot.optimize.phase3 import Phase3Result, StrategyPhase3
from bot.report.common import CSS, SERIES_COLORS, layout, num, pct, table

ISOLATED_PEAK_GAP = 0.5  # Sharpe della scelta oltre le vicine di più di 0,5: possibile picco isolato

ASSUMPTIONS = [
    "Tutte le ipotesi del motore di backtest (esecuzione all'apertura successiva, slippage costante, filtri "
    "dell'exchange attuali applicati al passato, commissioni in USDT, nessun interesse sugli USDT).",
    "Variante di valutazione: risk manager senza stop per drawdown massimo (decisione A). L'effetto dello stop è "
    "misurato a parte con un riavvio simulato dopo 7 giorni; in paper e live lo stop resta definitivo.",
    "Obiettivo dell'ottimizzazione: Sharpe annualizzato. Scelta finale: punteggio robusto più alto = minimo tra lo "
    "Sharpe della combinazione e la media delle vicine nella griglia, tra quelle con almeno 30 operazioni sul training.",
    "Walk-forward: ogni finestra fuori campione parte da capitale pieno e chiude la posizione alla sua fine "
    "(costi di uscita in più rispetto a un'operatività continua).",
    "Una sola validazione (2024) è un campione piccolo: un anno di un solo regime prevalente può favorire o "
    "penalizzare un tipo di strategia.",
]


def _mark(ok: bool) -> str:
    return "✓" if ok else "✗"


def _params(d: dict) -> str:
    return ", ".join(f"{k}={v:g}" if isinstance(v, float) else f"{k}={v}" for k, v in d.items())


def _summary_table(res: Phase3Result) -> str:
    rows = []
    for s in res.strategies:
        v, bh = s.validation["valutazione"], s.validation["buy & hold"]
        rows.append([
            s.name, str(s.grid_size), _params(s.selected),
            f"{pct(s.train.total_return, 1)} / {num(s.train.sharpe)} / {s.train.n_trades}",
            f"{pct(s.wf_summary['oos_return'], 1)} vs {pct(s.wf_summary['oos_bh_return'], 1)}",
            f"{pct(v['total_return'], 1)} vs {pct(bh['total_return'], 1)}",
            f"{pct(v['max_drawdown'], 1)} vs {pct(bh['max_drawdown'], 1)}",
            str(v["n_trades"]),
            " ".join(_mark(c.passed) for c in s.criteria),
            "SÌ" if s.passed_validation else "no",
        ])
    return table(
        ["Strategia", "Combinazioni", "Parametri scelti", "Training: rendimento / Sharpe / operazioni",
         "Walk-forward fuori campione: strategia vs buy & hold", "Validazione: rendimento vs buy & hold",
         "Validazione: max drawdown vs buy & hold", "Operazioni (validazione)", "Criteri 1-4", "Promossa al test"],
        rows, numeric_from=3,
    )


def _validation_charts(res: Phase3Result) -> list[str]:
    out = []
    for i, s in enumerate(res.strategies):
        fig = go.Figure()
        for color, (label, eq) in zip((SERIES_COLORS[0], SERIES_COLORS[2]), s.validation_curves.items()):
            d = eq.set_index("open_time")["equity"].resample("D").last().dropna()
            fig.add_trace(go.Scatter(x=d.index, y=d, name=label, mode="lines", line={"color": color, "width": 2},
                                     hovertemplate="%{y:,.2f} USDT<extra>" + label + "</extra>"))
        layout(fig, f"{s.name} — validazione {res.validation.start:%Y}", "USDT")
        fig.update_layout(height=300)
        out.append(pio.to_html(fig, full_html=False, include_plotlyjs=(i == 0),
                               config={"displaylogo": False, "responsive": True}))
    return out


def _strategy_details(s: StrategyPhase3) -> str:
    top = s.grid_table.head(10)
    grid_html = table(
        ["Parametri", "Sharpe", "Rendimento", "Max drawdown", "Operazioni", "Sharpe medio vicine", "Punteggio robusto"],
        [[_params(r.params), num(r.sharpe), pct(r.total_return, 1), pct(r.max_drawdown, 1), str(r.n_trades),
          num(r.neighbors_sharpe), num(r.robust_score)] for r in top.itertuples()],
    )
    folds_html = table(
        ["#", "Ottimizzazione su", "Verifica su", "Parametri scelti", "Sharpe in campione",
         "Rendimento fuori campione", "Buy & hold", "Operazioni"],
        [[str(f.number), f"{f.is_start:%Y-%m} → {f.is_end:%Y-%m}", f"{f.oos_start:%Y-%m} → {f.oos_end:%Y-%m}",
          _params(f.params) if f.params else "nessuna idonea", num(f.is_sharpe), pct(f.oos_return, 1),
          pct(f.oos_bh_return, 1), str(f.oos_trades)] for f in s.folds],
        numeric_from=4,
    )
    variants_html = table(
        ["Variante", "Rendimento", "Max drawdown", "Sharpe", "Operazioni", "Stop per drawdown"],
        [[label, pct(m["total_return"]), pct(m["max_drawdown"], 1), num(m["sharpe"]), str(m["n_trades"]),
          str(m.get("n_drawdown_stops", 0))] for label, m in s.validation.items()],
    )
    criteria_html = "".join(
        f"<div class='box {'ok' if c.passed else ''}'>{_mark(c.passed)} <b>{html.escape(c.name)}</b>: "
        f"{html.escape(c.detail)}</div>" for c in s.criteria
    )
    robustness_html = table(
        ["Periodo", "Simbolo", "Timeframe", "Rendimento", "Buy & hold", "Max drawdown", "Max drawdown B&H", "Sharpe", "Operazioni"],
        [[r.period, r.symbol, r.timeframe, pct(r.total_return, 1), pct(r.bh_return, 1), pct(r.max_drawdown, 1),
          pct(r.bh_max_drawdown, 1), num(r.sharpe), str(r.n_trades)] for r in s.robustness.itertuples()],
        numeric_from=3,
    )
    regimes_html = table(
        ["Regime", "Quota del tempo", "Giorni", "Strategia", "Buy & hold"],
        [[r["regime"], pct(r["share_of_time"], 1), str(r["days"]), pct(r["strategia"]), pct(r["buy & hold"])]
         for _, r in s.regimes.iterrows()],
    )
    wf = s.wf_summary
    peak = ("n/d" if math.isnan(s.isolated_gap) else
            f"{s.isolated_gap:+.2f}" + (" (possibile picco isolato)" if s.isolated_gap > ISOLATED_PEAK_GAP else ""))
    note = f"<div class='box'>{html.escape(s.selection_note)}</div>" if s.selection_note else ""
    return f"""
<details><summary><b>{html.escape(s.name)}</b> — parametri {html.escape(_params(s.selected))} —
{'promossa al test' if s.passed_validation else 'non promossa'}</summary>
{note}
<p class="meta">Combinazioni provate: {s.grid_size}. Sharpe della scelta meno media delle vicine: {peak}.
Walk-forward: {wf['n_folds']} finestre, strategia meglio del buy &amp; hold in {wf['folds_beating_bh']},
{wf['folds_without_choice']} senza combinazioni idonee, {wf['distinct_params']} combinazioni diverse scelte.</p>
<h3>Criteri della Fase 3 sulla validazione (anteprima: il giudizio vincolante è sul test)</h3>
{criteria_html}
<h3>Validazione {html.escape(s.symbol)} {html.escape(s.timeframe)}</h3>
{variants_html}
<h3>Robustezza: stessi parametri su altri simboli e timeframe</h3>
{robustness_html}
<h3>Validazione per regime di mercato</h3>
{regimes_html}
<h3>Walk-forward sul training</h3>
{folds_html}
<h3>Griglia sul training: le 10 combinazioni con punteggio robusto più alto</h3>
{grid_html}
</details>"""


def render_phase3_report(res: Phase3Result, optimization: dict) -> str:
    promoted = [s.name for s in res.strategies if s.passed_validation]
    verdict = (
        f"<div class='box ok'>Strategie promosse al test finale: <b>{', '.join(promoted)}</b>. Il test si esegue una "
        "sola volta con <code>uv run python -m bot.evaluate --confirm-test-period</code>.</div>" if promoted else
        "<div class='box'><b>Nessuna strategia supera tutti i criteri della Fase 3 sulla validazione.</b> Per il brief "
        "il risultato corretto è scriverlo e fermarsi o rivedere le ipotesi, senza abbassare i criteri. Il periodo di "
        "test resta intatto.</div>"
    )
    warnings = [f"{res.total_combinations} combinazioni provate in totale su {len(res.strategies)} strategie: "
                "più combinazioni si provano, più è facile trovarne una buona per caso (overfitting)."]
    for s in res.strategies:
        if not math.isnan(s.isolated_gap) and s.isolated_gap > ISOLATED_PEAK_GAP:
            warnings.append(f"{s.name}: la combinazione scelta ha uno Sharpe molto sopra le vicine: possibile picco isolato.")
        if s.wf_summary["folds_without_choice"]:
            warnings.append(f"{s.name}: {s.wf_summary['folds_without_choice']} finestre walk-forward senza combinazioni "
                            "con abbastanza operazioni.")
        if s.validation["valutazione"]["sharpe"] > 3:
            warnings.append(f"{s.name}: Sharpe di validazione sopra 3, presumere un bug finché non è dimostrato il contrario.")
    charts = _validation_charts(res)
    bh = res.strategies[0].validation["buy & hold"] if res.strategies else None
    bh_line = (f"Buy &amp; hold {html.escape(res.strategies[0].symbol)} sulla validazione: rendimento "
               f"{pct(bh['total_return'])}, max drawdown {pct(bh['max_drawdown'], 1)}." if bh else "")
    return f"""<!doctype html>
<html lang="it"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Fase 3 — confronto strategie</title>
<style>{CSS}
details {{ border-top:1px solid var(--grid); padding:8px 0; }} summary {{ cursor:pointer; padding:4px 0; }}
h3 {{ font-size:14px; margin:20px 0 6px; }}</style></head>
<body><main>
<h1>Fase 3 — confronto delle strategie</h1>
<div class="meta">Training {res.train.start:%Y-%m-%d} → {res.train.end:%Y-%m-%d} · validazione
{res.validation.start:%Y-%m-%d} → {res.validation.end:%Y-%m-%d} · il periodo di test non è stato letto ·
ottimizzazione {html.escape(json.dumps(optimization))} · commit <code>{html.escape(git_commit())}</code> ·
generato il {res.generated_at:%Y-%m-%d %H:%M} UTC</div>
<div class="meta">{html.escape(res.data_quality)}</div>

<h2>Esito</h2>
{verdict}

<h2>Avvisi</h2>
{"".join(f"<div class='box'>{html.escape(w)}</div>" for w in warnings)}

<h2>Riepilogo</h2>
<p class="meta">{bh_line} Criteri: 1 batte il buy &amp; hold (o rendimento simile con drawdown −30%), 2 almeno 30
operazioni, 3 positiva con slippage 0,1%, 4 coerente su ETH/USDT. Tutti i numeri sono <b>misurati</b> sulla variante
di valutazione.</p>
{_summary_table(res)}

<h2>Validazione: equity della strategia contro il buy &amp; hold</h2>
{"".join(charts)}

<h2>Dettaglio per strategia</h2>
{"".join(_strategy_details(s) for s in res.strategies)}

<h2>Misurato e ipotesi</h2>
<ul>{"".join(f"<li>{html.escape(a)}</li>" for a in ASSUMPTIONS)}</ul>
</main></body></html>
"""
