"""Commissioni, tasse e prezzo di pareggio (Trade Republic)."""

from stocks.config import StocksSettings


def round_trip_fees(s: StocksSettings) -> float:
    """Commissioni di un acquisto più una vendita."""
    return 2 * s.fee_per_order_eur


def net_pnl_eur(buy_eur: float, sell_eur: float, s: StocksSettings) -> float:
    """Utile netto in euro: commissioni sempre, tasse solo su plusvalenza positiva.

    La tassa si calcola sulla plusvalenza già al netto delle commissioni;
    se è negativa o nulla non si paga nulla (ipotesi prudente: nessun credito d'imposta).
    """
    gain = sell_eur - buy_eur - round_trip_fees(s)
    if gain > 0:
        gain *= 1 - s.capital_gains_tax
    return gain


def breakeven_price(entry: float, qty: float, s: StocksSettings) -> float:
    """Prezzo per azione a cui vendere `qty` azioni dà netto zero (tasse nulle a pareggio)."""
    return entry + round_trip_fees(s) / qty
