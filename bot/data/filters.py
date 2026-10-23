"""Filtri dell'exchange (lot size, notional minimo, tick size) e loro storicizzazione.

Riferimento: binance-spot-api-docs, filters.md. Filtri letti:
- LOT_SIZE      -> min_qty, max_qty, step_size
- PRICE_FILTER  -> tick_size
- NOTIONAL      -> min_notional, apply_min_to_market (oppure il vecchio MIN_NOTIONAL)
"""

import json
from datetime import datetime
from decimal import Decimal

import duckdb

FILTER_FIELDS = ["min_qty", "max_qty", "step_size", "min_notional", "apply_min_to_market", "tick_size"]


def parse_symbol_filters(symbol_info: dict) -> dict:
    """Estrae i filtri dalla voce `symbols[i]` della risposta di exchangeInfo."""
    by_type = {f["filterType"]: f for f in symbol_info.get("filters", [])}
    try:
        lot = by_type["LOT_SIZE"]
        price = by_type["PRICE_FILTER"]
    except KeyError as exc:
        raise ValueError(f"{symbol_info.get('symbol')}: filtro {exc} assente in exchangeInfo") from None

    if "NOTIONAL" in by_type:
        notional = by_type["NOTIONAL"]
        min_notional, apply_to_market = notional["minNotional"], notional.get("applyMinToMarket", True)
    elif "MIN_NOTIONAL" in by_type:
        notional = by_type["MIN_NOTIONAL"]
        min_notional, apply_to_market = notional["minNotional"], notional.get("applyToMarket", True)
    else:
        raise ValueError(f"{symbol_info.get('symbol')}: nessun filtro NOTIONAL/MIN_NOTIONAL")

    return {
        "min_qty": Decimal(lot["minQty"]),
        "max_qty": Decimal(lot["maxQty"]),
        "step_size": Decimal(lot["stepSize"]),
        "min_notional": Decimal(min_notional),
        "apply_min_to_market": bool(apply_to_market),
        "tick_size": Decimal(price["tickSize"]),
    }


def latest_filters(con: duckdb.DuckDBPyConnection, symbol: str, at: datetime | None = None) -> dict | None:
    """Filtri validi per `symbol` all'istante `at` (default: gli ultimi salvati)."""
    sql = f"SELECT {', '.join(FILTER_FIELDS)} FROM exchange_filters WHERE symbol = ?"
    params: list = [symbol]
    if at is not None:
        sql += " AND valid_from <= ?"
        params.append(at)
    rows = con.execute(sql + " ORDER BY valid_from DESC LIMIT 1", params).fetchall()
    return dict(zip(FILTER_FIELDS, rows[0])) if rows else None


def store_filters(
    con: duckdb.DuckDBPyConnection, symbol: str, symbol_info: dict, now: datetime
) -> bool:
    """Salva i filtri se diversi dagli ultimi registrati. Restituisce True se ha inserito."""
    parsed = parse_symbol_filters(symbol_info)
    if latest_filters(con, symbol) == parsed:
        return False
    con.execute(
        f"INSERT INTO exchange_filters (symbol, valid_from, {', '.join(FILTER_FIELDS)}, raw_json) "
        f"VALUES (?, ?, {', '.join('?' * len(FILTER_FIELDS))}, ?)",
        [symbol, now, *(parsed[f] for f in FILTER_FIELDS), json.dumps(symbol_info.get("filters", []))],
    )
    return True
