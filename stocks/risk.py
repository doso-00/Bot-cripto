"""Limiti di rischio: importo massimo per ordine e blocco del portafoglio."""

from stocks.config import StocksSettings
from stocks.models import PositionKind

_TOL = 1e-9


def max_order_eur(
    kind: PositionKind,
    holdings_eur: dict[PositionKind, float],
    open_opps: int,
    s: StocksSettings,
) -> tuple[float, str | None]:
    """Importo massimo investibile ora e, se zero, il motivo del blocco.

    L'importo minimo d'ordine vince sugli altri limiti: sotto il minimo si restituisce 0.
    """
    opp_used = holdings_eur.get(PositionKind.opportunity, 0.0) + holdings_eur.get(PositionKind.event, 0.0)
    opp_left = s.budget_eur * s.opp_max_total_share - opp_used

    if kind == PositionKind.core:
        core_left = s.budget_eur * s.core_share - holdings_eur.get(PositionKind.core, 0.0)
        amount = min(s.budget_eur * s.core_max_weight, core_left)
    elif kind == PositionKind.opportunity:
        if open_opps >= s.opp_max_open:
            return 0.0, f"già {open_opps} occasioni aperte (massimo {s.opp_max_open})"
        amount = opp_left
    else:
        amount = min(s.event_max_eur, opp_left)

    if amount < s.min_order_eur:
        return 0.0, f"importo sotto il minimo di {s.min_order_eur:.0f} €"
    return amount, None


def portfolio_halted(equity_history_eur: list[float], s: StocksSettings) -> bool:
    """True se il valore attuale è sceso almeno della soglia dal massimo storico."""
    if not equity_history_eur:
        return False
    peak = max(equity_history_eur)
    if peak <= 0:
        return False
    return (peak - equity_history_eur[-1]) / peak >= s.portfolio_halt_drawdown - _TOL
