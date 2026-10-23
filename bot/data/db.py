"""Accesso a DuckDB: schema delle tabelle dati e scrittura idempotente delle candele.

Convenzione: tutte le colonne TIMESTAMP sono in UTC (senza fuso esplicito).
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import duckdb
import pandas as pd

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS candles (
    symbol        VARCHAR   NOT NULL,
    timeframe     VARCHAR   NOT NULL,
    open_time     TIMESTAMP NOT NULL,
    open          DOUBLE    NOT NULL,
    high          DOUBLE    NOT NULL,
    low           DOUBLE    NOT NULL,
    close         DOUBLE    NOT NULL,
    volume        DOUBLE    NOT NULL,
    close_time    TIMESTAMP NOT NULL,
    quote_volume  DOUBLE,
    trade_count   BIGINT,
    is_closed     BOOLEAN   NOT NULL,
    source        VARCHAR   NOT NULL,   -- 'bulk' (archivio data.binance.vision) o 'api'
    ingested_at   TIMESTAMP NOT NULL,
    PRIMARY KEY (symbol, timeframe, open_time)
);

-- Storicizzata: una nuova riga solo quando i filtri cambiano rispetto all'ultima salvata
CREATE TABLE IF NOT EXISTS exchange_filters (
    symbol              VARCHAR       NOT NULL,
    valid_from          TIMESTAMP     NOT NULL,
    min_qty             DECIMAL(24,8) NOT NULL,
    max_qty             DECIMAL(24,8) NOT NULL,
    step_size           DECIMAL(24,8) NOT NULL,
    min_notional        DECIMAL(24,8) NOT NULL,
    apply_min_to_market BOOLEAN       NOT NULL,
    tick_size           DECIMAL(24,8) NOT NULL,
    raw_json            VARCHAR       NOT NULL,
    PRIMARY KEY (symbol, valid_from)
);
"""

_VALUE_COLUMNS = ["open", "high", "low", "close", "volume", "close_time", "quote_volume", "trade_count"]


@dataclass
class UpsertStats:
    inserted: int = 0
    changed: int = 0
    unchanged: int = 0

    def __iadd__(self, other: "UpsertStats") -> "UpsertStats":
        self.inserted += other.inserted
        self.changed += other.changed
        self.unchanged += other.unchanged
        return self


def scalar(con: duckdb.DuckDBPyConnection, sql: str, params: list | None = None):
    """Primo valore della prima riga.

    Usa fetchall(): da DuckDB 2.0 un risultato letto solo in parte (fetchone) resta
    "aperto" e blocca register()/unregister() sulla stessa connessione.
    """
    rows = con.execute(sql, params or []).fetchall()
    return rows[0][0] if rows else None


def connect(path: Path | str, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path), read_only=read_only)
    if not read_only:
        con.execute(SCHEMA)
    return con


def upsert_candles(
    con: duckdb.DuckDBPyConnection, frame: pd.DataFrame, ingested_at: datetime
) -> UpsertStats:
    """Inserisce le candele nuove e aggiorna quelle già presenti ma con valori diversi.

    Una candela già presente con valori diversi significa che la sorgente è cambiata
    (es. Binance ha corretto un file dell'archivio): si aggiorna e si segnala nel log,
    così la differenza non passa inosservata.
    """
    if frame.empty:
        return UpsertStats()
    if frame.duplicated(["symbol", "timeframe", "open_time"]).any():
        raise ValueError("candele duplicate nello stesso batch: sorgente da verificare")

    staging = frame.assign(ingested_at=ingested_at)
    con.register("staging_candles", staging)
    try:
        key = "c.symbol = s.symbol AND c.timeframe = s.timeframe AND c.open_time = s.open_time"
        differs = " OR ".join(f"c.{col} IS DISTINCT FROM s.{col}" for col in _VALUE_COLUMNS)

        new_rows = scalar(
            con, f"SELECT count(*) FROM staging_candles s WHERE NOT EXISTS (SELECT 1 FROM candles c WHERE {key})"
        )
        changed = scalar(con, f"SELECT count(*) FROM staging_candles s JOIN candles c ON {key} WHERE {differs}")

        con.execute("BEGIN TRANSACTION")
        try:
            if changed:
                sets = ", ".join(
                    f"{col} = s.{col}" for col in [*_VALUE_COLUMNS, "source", "ingested_at"]
                )
                con.execute(
                    f"UPDATE candles c SET {sets} FROM staging_candles s WHERE {key} AND ({differs})"
                )
            con.execute(
                f"""
                INSERT INTO candles
                SELECT symbol, timeframe, open_time, open, high, low, close, volume, close_time,
                       quote_volume, trade_count, is_closed, source, ingested_at
                FROM staging_candles s
                WHERE NOT EXISTS (SELECT 1 FROM candles c WHERE {key})
                """
            )
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
    finally:
        con.unregister("staging_candles")

    if changed:
        log.warning(
            "%s %s: %d candele già presenti avevano valori diversi e sono state aggiornate",
            frame["symbol"].iat[0], frame["timeframe"].iat[0], changed,
        )
    return UpsertStats(inserted=new_rows, changed=changed, unchanged=len(frame) - new_rows - changed)


def last_open_time(con: duckdb.DuckDBPyConnection, symbol: str, timeframe: str) -> datetime | None:
    return scalar(con, "SELECT max(open_time) FROM candles WHERE symbol = ? AND timeframe = ?", [symbol, timeframe])


def load_candles(
    con: duckdb.DuckDBPyConnection,
    symbol: str,
    timeframe: str,
    start: datetime | None = None,
    end: datetime | None = None,
) -> pd.DataFrame:
    """Candele chiuse ordinate per open_time, opzionalmente nell'intervallo [start, end)."""
    sql = "SELECT * FROM candles WHERE symbol = ? AND timeframe = ? AND is_closed"
    params: list = [symbol, timeframe]
    if start is not None:
        sql += " AND open_time >= ?"
        params.append(start)
    if end is not None:
        sql += " AND open_time < ?"
        params.append(end)
    return con.execute(sql + " ORDER BY open_time", params).df()
