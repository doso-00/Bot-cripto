"""Criteri della Fase 3 (brief §9), con le definizioni fissate il 2026-10-23 prima di
vedere i risultati (docs/DECISIONI.md). Non vanno abbassati.

1. Rendimento netto > buy & hold, OPPURE rendimento "simile" (non più di 5 punti sotto)
   con max drawdown inferiore di almeno il 30%.
2. Almeno 30 operazioni.
3. Rendimento positivo anche con slippage 0,1% per lato.
4. Coerente su ETH/USDT con gli stessi parametri: rendimento positivo con slippage 0,1% E
   criterio 1 soddisfatto rispetto al buy & hold di ETH.
Si applicano alla variante di valutazione (risk manager senza stop per drawdown, decisione A).
"""

from dataclasses import dataclass

SIMILAR_RETURN_GAP = 0.05
MIN_DRAWDOWN_REDUCTION = 0.30
MIN_TRADES = 30


@dataclass
class Criterion:
    name: str
    passed: bool
    detail: str


def beats_benchmark(metrics: dict, benchmark: dict) -> tuple[bool, str]:
    r, bh = metrics["total_return"], benchmark["total_return"]
    dd, bh_dd = metrics["max_drawdown"], benchmark["max_drawdown"]
    reduction = 1 - dd / bh_dd if bh_dd > 0 else 0.0
    if r > bh:
        return True, f"rendimento {r:+.2%} > buy & hold {bh:+.2%}"
    similar = r >= bh - SIMILAR_RETURN_GAP
    if similar and reduction >= MIN_DRAWDOWN_REDUCTION:
        return True, (f"rendimento {r:+.2%} simile al buy & hold {bh:+.2%} e max drawdown ridotto del "
                      f"{reduction:.0%} ({dd:.1%} contro {bh_dd:.1%})")
    return False, (f"rendimento {r:+.2%} contro buy & hold {bh:+.2%}"
                   + (f"; simile ma max drawdown ridotto solo del {reduction:.0%} (serve il 30%)" if similar else
                      f" (oltre {SIMILAR_RETURN_GAP * 100:.0f} punti sotto)"))


def phase3_criteria(main: dict, benchmark: dict, main_slip: dict,
                    confirm: dict, confirm_benchmark: dict, confirm_slip: dict, confirm_symbol: str) -> list[Criterion]:
    """Metriche (compute_metrics) di: strategia, buy & hold, strategia con slippage 0,1%, e le
    stesse tre sul simbolo di conferma."""
    c1, d1 = beats_benchmark(main, benchmark)
    c4_bm, d4_bm = beats_benchmark(confirm, confirm_benchmark)
    c4_pos = confirm_slip["total_return"] > 0
    return [
        Criterion("Batte il buy & hold", c1, d1),
        Criterion(f"Almeno {MIN_TRADES} operazioni", main["n_trades"] >= MIN_TRADES, f"{main['n_trades']} operazioni"),
        Criterion("Positiva con slippage 0,1%", main_slip["total_return"] > 0,
                  f"rendimento con slippage 0,1%: {main_slip['total_return']:+.2%}"),
        Criterion(f"Coerente su {confirm_symbol}", c4_bm and c4_pos,
                  f"{d4_bm}; con slippage 0,1%: {confirm_slip['total_return']:+.2%}"),
    ]
