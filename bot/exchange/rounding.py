"""Arrotondamento delle quantità ai filtri dell'exchange e verifica degli ordini.

Regole (binance-spot-api-docs, filters.md):
- LOT_SIZE: quantità multipla di step_size, tra min_qty e max_qty;
- NOTIONAL: prezzo x quantità >= min_notional (anche per ordini a mercato se
  apply_min_to_market è vero).
I calcoli usano Decimal: con i float, 0.1 + 0.2 != 0.3 e un arrotondamento "per difetto"
può finire un passo sopra o sotto.
"""

from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal


@dataclass(frozen=True)
class SymbolFilters:
    min_qty: Decimal
    max_qty: Decimal
    step_size: Decimal
    min_notional: Decimal
    apply_min_to_market: bool
    tick_size: Decimal

    @classmethod
    def from_mapping(cls, m: dict) -> "SymbolFilters":
        return cls(**{k: (v if k == "apply_min_to_market" else Decimal(str(v))) for k, v in m.items()})


def round_qty_down(qty: float | Decimal, step_size: Decimal) -> Decimal:
    """Arrotonda per difetto al multiplo di step_size (mai per eccesso: non si può
    comprare o vendere più di quanto si ha)."""
    qty = Decimal(str(qty))
    if qty <= 0:
        return Decimal(0)
    return (qty / step_size).to_integral_value(rounding=ROUND_DOWN) * step_size


def order_rejection_reason(qty: Decimal, price: float, filters: SymbolFilters) -> str | None:
    """Motivo per cui l'exchange rifiuterebbe un ordine a mercato, oppure None se valido."""
    if qty <= 0:
        return "quantità nulla dopo l'arrotondamento allo step size"
    if qty < filters.min_qty:
        return f"quantità {qty} sotto il minimo {filters.min_qty}"
    if qty > filters.max_qty:
        return f"quantità {qty} sopra il massimo {filters.max_qty}"
    if (qty % filters.step_size) != 0:
        return f"quantità {qty} non multipla dello step size {filters.step_size}"
    notional = qty * Decimal(str(price))
    if filters.apply_min_to_market and notional < filters.min_notional:
        return f"controvalore {notional:.2f} sotto il notional minimo {filters.min_notional}"
    return None
