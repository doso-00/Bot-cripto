"""Registro delle operazioni: `uv run python -m stocks.trade add|list`.

Riccardo compra e vende a mano su Trade Republic; questo comando scrive l'operazione
nello stato locale (`out/state.json`), con gli stessi controlli che il refresh applica
alle posizioni. Nessun collegamento al broker: si registra solo ciò che è già avvenuto.
La procedura completa per una sessione di chat (lettura e scrittura del documento
`state/current` della pagina) è in `docs/REGISTRARE_OPERAZIONI.md`.

Esempio:
    uv run python -m stocks.trade add --ticker NVDA --side buy --qty 1.3 --price 140.50 \\
        --currency EUR --kind opportunity --date 2026-11-18T15:40
"""

import argparse
import math
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from stocks.config import load_settings
from stocks.models import PositionKind
from stocks.state import State, Trade, holdings_from_trades, load_state, save_state

PAGE_TZ = ZoneInfo("Europe/Rome")
DEFAULT_STATE = "out/state.json"
_SIDE_LABEL = {"buy": "acquisto", "sell": "vendita"}


class TradeError(ValueError):
    """Operazione non ammessa: il messaggio è il motivo in italiano."""


def _num(x: float, decimals: int = 2) -> str:
    text = f"{x:,.{decimals}f}"
    return text.replace(",", "\0").replace(".", ",").replace("\0", ".")


def _qty(x: float) -> str:
    return f"{x:.6f}".rstrip("0").rstrip(".").replace(".", ",")


def parse_ts(text: str | None, now: datetime) -> datetime:
    """Data/ora dell'operazione: ISO 8601; senza fuso = ora italiana; assente = adesso."""
    if text is None:
        return now
    try:
        ts = datetime.fromisoformat(text)
    except ValueError:
        raise TradeError(
            f"data non valida: {text!r} (usa AAAA-MM-GG o AAAA-MM-GGTHH:MM)"
        ) from None
    return ts if ts.tzinfo is not None else ts.replace(tzinfo=PAGE_TZ)


def add_trade(state: State, trade: Trade) -> State:
    """Stato con l'operazione aggiunta; solleva TradeError se non è ammessa.

    Quantità e prezzo > 0, commissione >= 0 (numeri finiti); per il titolo, stessa valuta
    e stesso tipo delle operazioni precedenti e nessuna vendita oltre la quantità
    posseduta (gli stessi controlli di `holdings_from_trades` usati dal refresh).
    """
    for name, value, positive in (
        ("quantità", trade.qty, True),
        ("prezzo", trade.price, True),
        ("commissione", trade.fee_eur, False),
    ):
        if not math.isfinite(value) or value < 0 or (positive and value == 0):
            rule = "maggiore di zero" if positive else "zero o più"
            raise TradeError(f"{name} non valida: {value} (deve essere {rule})")
    if not trade.ticker:
        raise TradeError("ticker mancante")
    same = [t for t in state.trades if t.ticker == trade.ticker]
    try:
        holdings_from_trades([*same, trade])
    except TypeError:
        # date con e senza fuso mescolate nelle operazioni già registrate
        raise TradeError(
            f"{trade.ticker}: le operazioni già registrate hanno date senza fuso orario"
        ) from None
    except (ValueError, ArithmeticError) as exc:
        raise TradeError(str(exc)) from None
    return state.model_copy(update={"trades": [*state.trades, trade]})


def _trade_line(t: Trade) -> str:
    return (
        f"{t.ts.astimezone(PAGE_TZ):%d/%m/%Y %H:%M}  {_SIDE_LABEL[t.side]:<9} {t.ticker:<8} "
        f"{_qty(t.qty)} az. a {_num(t.price)} {t.currency} "
        f"(commissione {_num(t.fee_eur)} €, tipo {t.kind.value})"
        + (f" - {t.note}" if t.note else "")
    )


def _positions(state: State) -> list[str]:
    try:
        holdings = holdings_from_trades(state.trades)
    except (ValueError, TypeError, ArithmeticError) as exc:
        return [f"Operazioni incoerenti: {exc}"]
    if not holdings:
        return ["nessuna"]
    return [
        f"{h.ticker}: {_qty(h.qty)} az., prezzo medio {_num(h.avg_price)} {h.currency}, tipo {h.kind.value}"
        for h in sorted(holdings.values(), key=lambda h: h.ticker)
    ]


def _add(args: argparse.Namespace, now: datetime) -> int:
    path = Path(args.state)
    try:
        state = load_state(path)
        trade = Trade(
            ts=parse_ts(args.date, now),
            ticker=args.ticker.strip().upper(),
            side=args.side,
            qty=args.qty,
            price=args.price,
            currency=args.currency,
            fee_eur=load_settings().fee_per_order_eur if args.fee is None else args.fee,
            kind=PositionKind(args.kind),
            note=args.note,
        )
        new_state = add_trade(state, trade)
    except (TradeError, ValueError, OSError) as exc:  # ValidationError è un ValueError
        print(f"Operazione non registrata: {exc}", file=sys.stderr)
        return 2
    path.parent.mkdir(parents=True, exist_ok=True)
    save_state(new_state, path)
    print(f"Registrata in {path}:")
    print("  " + _trade_line(trade))
    print("Posizioni aperte:")
    for line in _positions(new_state):
        print("  " + line)
    return 0


def _list(args: argparse.Namespace) -> int:
    try:
        state = load_state(Path(args.state))
    except (ValueError, OSError) as exc:
        print(f"Stato non leggibile: {exc}", file=sys.stderr)
        return 2
    if not state.trades:
        print("Nessuna operazione registrata.")
        return 0
    print(f"Operazioni ({len(state.trades)}):")
    for t in sorted(state.trades, key=lambda x: x.ts):
        print("  " + _trade_line(t))
    print("Posizioni aperte:")
    for line in _positions(state):
        print("  " + line)
    return 0


def main(argv: list[str] | None = None, now: datetime | None = None) -> int:
    """Exit code 0 se l'operazione è registrata (o l'elenco stampato); 2 se non è ammessa."""
    parser = argparse.ArgumentParser(
        prog="python -m stocks.trade",
        description="Registra nello stato le operazioni fatte a mano su Trade Republic.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    add = sub.add_parser("add", help="registra un acquisto o una vendita")
    add.add_argument("--ticker", required=True, help="ticker come nella pagina (es. NVDA, ENEL.MI)")
    add.add_argument("--side", required=True, choices=["buy", "sell"], help="buy = acquisto, sell = vendita")
    add.add_argument("--qty", required=True, type=float, help="quantità (anche frazioni di azione)")
    add.add_argument(
        "--price", required=True, type=float,
        help="prezzo per azione pagato o incassato, come mostrato da Trade Republic",
    )
    add.add_argument("--currency", required=True, choices=["EUR", "USD"], help="valuta del prezzo")
    add.add_argument(
        "--kind", required=True, choices=[k.value for k in PositionKind],
        help="core = base, opportunity = occasione, event = evento",
    )
    add.add_argument("--fee", type=float, default=None, help="commissione in euro (default: settings.yaml)")
    add.add_argument("--date", default=None, help="data/ora ISO (es. 2026-11-18T15:40); senza fuso = ora italiana")
    add.add_argument("--note", default="", help="nota libera")
    add.add_argument("--state", default=DEFAULT_STATE, help="file JSON dello stato")

    lst = sub.add_parser("list", help="elenca operazioni e posizioni aperte")
    lst.add_argument("--state", default=DEFAULT_STATE, help="file JSON dello stato")

    args = parser.parse_args(argv)
    now = now or datetime.now(PAGE_TZ)
    if args.command == "add":
        return _add(args, now)
    return _list(args)


if __name__ == "__main__":
    sys.exit(main())
