# Registro decisioni

Ogni scelta non banale va annotata qui: data, decisione, alternative scartate, motivo (brief §13).

| Data | Decisione | Alternative scartate | Motivo |
|---|---|---|---|
| 2026-10-23 | `pandas` come libreria di elaborazione dati | `polars` | Più familiare per un profilo Python intermedio; ccxt e plotly lavorano nativamente con DataFrame pandas; DuckDB converte da/verso pandas senza dipendenze extra. I volumi (qualche centinaio di migliaia di candele) non richiedono le prestazioni di polars. |
| 2026-10-23 | Python 3.12 fissato in `.python-version`; `requires-python >= 3.11` | 3.13 | Versione stabile con wheel disponibili per tutte le dipendenze; resta compatibile con il minimo 3.11 del brief. |
| 2026-10-23 | Parquet scritto direttamente da DuckDB (`COPY ... TO ... (FORMAT parquet)`) | `pyarrow` | Evita una dipendenza non elencata nel brief. Da rivalutare in Fase 1 se serve. |
| 2026-10-23 | Build backend `hatchling`, pacchetto `bot` installato in modalità editable da `uv sync` | Layout `src/` | Permette `uv run python -m bot.<modulo>` e gli import nei test senza manipolare `sys.path`; i comandi di CLAUDE.md restano invariati. |
