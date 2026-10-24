# Query di verifica dei dati (DuckDB)

Query da lanciare a mano sul database per controllare i dati indipendentemente dal codice Python
del controllo qualità. Si aprono con la CLI di DuckDB (`duckdb data/market.duckdb`) oppure da
Python (`uv run python -c "import duckdb; print(duckdb.connect('data/market.duckdb', read_only=True).sql('...'))"`).

Convenzioni: tutti i TIMESTAMP sono UTC; `open_time` identifica la candela; il bot salva solo candele chiuse.
Ogni query è eseguita dal test `tests/test_query_docs.py` su dati sintetici: se lo schema cambia e
una query si rompe, il test fallisce.

## Q1 — Riepilogo per serie: candele attese vs presenti

```sql
-- Q1
WITH tf AS (
    SELECT * FROM (VALUES ('1h', INTERVAL 1 HOUR, 3600), ('4h', INTERVAL 4 HOUR, 14400)) t(timeframe, step, step_s)
)
SELECT c.symbol, c.timeframe,
       count(*)                                                           AS candles,
       min(c.open_time)                                                   AS first_open,
       max(c.open_time)                                                   AS last_open,
       (epoch(max(c.open_time)) - epoch(min(c.open_time))) / any_value(tf.step_s) + 1 AS expected,
       expected - candles                                                 AS missing
FROM candles c JOIN tf USING (timeframe)
GROUP BY ALL
ORDER BY ALL;
```

`missing` deve coincidere con la somma dei buchi di Q3.

## Q2 — Duplicati di chiave

```sql
-- Q2
SELECT symbol, timeframe, open_time, count(*) AS n
FROM candles
GROUP BY ALL
HAVING count(*) > 1;
```

Deve restituire zero righe (la chiave primaria lo impedisce già; la query è un controllo indipendente).

## Q3 — Buchi: candele mancanti tra due candele presenti

```sql
-- Q3
WITH tf AS (
    SELECT * FROM (VALUES ('1h', INTERVAL 1 HOUR), ('4h', INTERVAL 4 HOUR)) t(timeframe, step)
),
seq AS (
    SELECT c.symbol, c.timeframe, c.open_time, tf.step,
           lead(c.open_time) OVER (PARTITION BY c.symbol, c.timeframe ORDER BY c.open_time) AS next_open
    FROM candles c JOIN tf USING (timeframe)
)
SELECT symbol, timeframe,
       open_time + step                                         AS first_missing,
       next_open - step                                         AS last_missing,
       (epoch(next_open) - epoch(open_time)) / epoch(step) - 1  AS n_missing
FROM seq
WHERE next_open - open_time > step
ORDER BY first_missing, symbol, timeframe;
```

Ogni riga deve essere contenuta in un evento di `config/known_events.yaml`.
Se lo stesso buco compare per tutti i simboli, è con ogni probabilità un fermo dell'exchange.

## Q4 — Coerenza OHLC e valori

```sql
-- Q4
SELECT symbol, timeframe, open_time, open, high, low, close, volume
FROM candles
WHERE high < low
   OR open  NOT BETWEEN low AND high
   OR close NOT BETWEEN low AND high
   OR least(open, high, low, close) <= 0
   OR volume < 0;
```

Deve restituire zero righe.

## Q5 — Allineamento al timeframe e close_time

```sql
-- Q5
WITH tf AS (
    SELECT * FROM (VALUES ('1h', INTERVAL 1 HOUR, 3600), ('4h', INTERVAL 4 HOUR, 14400)) t(timeframe, step, step_s)
)
SELECT c.symbol, c.timeframe, c.open_time, c.close_time
FROM candles c JOIN tf USING (timeframe)
WHERE epoch(c.open_time) % tf.step_s <> 0
   OR c.close_time <> c.open_time + tf.step - INTERVAL 1 MILLISECOND;
```

Le uniche righe ammesse sono le candele che toccano un fermo dell'exchange (si chiudono in anticipo
quando il trading si interrompe) e sono contenute in un evento di `config/known_events.yaml`.

## Q6 — Candele 4h confrontate con la ricostruzione dalle 1h

```sql
-- Q6
WITH h1 AS (
    SELECT symbol, time_bucket(INTERVAL 4 HOUR, open_time) AS bucket,
           count(*)                  AS n,
           arg_min(open, open_time)  AS open,
           max(high)                 AS high,
           min(low)                  AS low,
           arg_max(close, open_time) AS close,
           sum(volume)               AS volume
    FROM candles WHERE timeframe = '1h'
    GROUP BY ALL
)
SELECT h4.symbol, h4.open_time,
       h4.open,  h1.open  AS open_1h,
       h4.high,  h1.high  AS high_1h,
       h4.low,   h1.low   AS low_1h,
       h4.close, h1.close AS close_1h,
       h4.volume, h1.volume AS volume_1h
FROM candles h4
JOIN h1 ON h1.symbol = h4.symbol AND h1.bucket = h4.open_time AND h1.n = 4
WHERE h4.timeframe = '4h'
  AND (h4.open <> h1.open OR h4.high <> h1.high OR h4.low <> h1.low OR h4.close <> h1.close
       OR abs(h4.volume - h1.volume) > 1e-6 * h4.volume);
```

I prezzi devono coincidere sempre. Il volume può differire su poche candele: è un'incoerenza
dell'archivio Binance (vedi `docs/DECISIONI.md`), segnalata come avviso. I blocchi con meno di 4
candele 1h (per un buco) sono esclusi: non si possono ricostruire.

## Q7 — Le 10 variazioni orarie più ampie (da verificare a mano sui grafici)

```sql
-- Q7
SELECT symbol, open_time, close,
       close / lag(close) OVER w - 1 AS ret
FROM candles
WHERE timeframe = '1h'
WINDOW w AS (PARTITION BY symbol ORDER BY open_time)
QUALIFY ret IS NOT NULL
ORDER BY abs(ret) DESC
LIMIT 10;
```

## Q8 — Provenienza dei dati per mese

```sql
-- Q8
SELECT symbol, timeframe, date_trunc('month', open_time) AS month, source, count(*) AS candles
FROM candles
GROUP BY ALL
ORDER BY symbol, timeframe, month, source;
```

I mesi conclusi e pubblicati nell'archivio sono `bulk`; il mese in corso (e quello appena concluso,
finché l'archivio non lo pubblica) sono `api`. Un `bulk` rilanciato sostituisce le righe `api`
solo se i valori differiscono (in quel caso il download lo segnala nel log).

## Q9 — Filtri dell'exchange in vigore

```sql
-- Q9
SELECT symbol, valid_from, min_qty, step_size, min_notional, apply_min_to_market, tick_size
FROM exchange_filters
QUALIFY row_number() OVER (PARTITION BY symbol ORDER BY valid_from DESC) = 1
ORDER BY symbol;
```
