# Bot-cripto

Bot di trading **spot** su Binance, progetto didattico/sperimentale. L'obiettivo è misurare onestamente se una strategia batte il buy & hold dopo i costi: prima in backtest, poi in paper trading sul Binance Spot Testnet, e solo dopo, eventualmente, con importi minimi reali.

- Regole permanenti e di sicurezza: [`CLAUDE.md`](CLAUDE.md)
- Specifica completa e fasi: [`docs/BRIEF_PROGETTO.md`](docs/BRIEF_PROGETTO.md)
- Registro delle decisioni: [`docs/DECISIONI.md`](docs/DECISIONI.md)

**Stato:** Fasi 1 e 2 chiuse il 2026-10-24. Fase 3 eseguita il 2026-10-24: **nessuna strategia supera i criteri** sulla validazione, quindi nessuna passa alla Fase 4 (vedi [`docs/RISULTATI_FASE3.md`](docs/RISULTATI_FASE3.md)). Il periodo di test è intatto. Prossimo passo da decidere: fermarsi o rivedere le ipotesi.

## Installazione

Prerequisiti: [`uv`](https://docs.astral.sh/uv/getting-started/installation/) e git. `uv` scarica da solo la versione di Python indicata in `.python-version` se non è presente.

```bash
git clone <url-del-repository> Bot-cripto
cd Bot-cripto
uv sync                 # crea .venv e installa le dipendenze dal lockfile uv.lock
cp .env.example .env    # poi compila .env (le chiavi servono solo dalla Fase 4)
uv run pytest           # verifica che tutto funzioni
```

`.env` è escluso da git: non va mai committato e non deve mai contenere chiavi nel codice o nei log.

## Modalità

| Modalità | Stato | Note |
|---|---|---|
| `backtest` | default | Solo dati storici locali |
| `paper` | Fase 4 | Binance Spot Testnet, fondi finti |
| `live` | non implementata | Solo su richiesta esplicita, dopo la chiusura della Fase 4 |

Kill switch: creare un file `STOP` nella root del progetto ferma il bot al ciclo successivo.

## Comandi

| Comando | Cosa fa | Disponibile da |
|---|---|---|
| `uv run pytest` | Esegue la suite di test | Fase 0 |
| `uv run python -m bot.data.download` | Scarica/aggiorna le candele storiche e i filtri dell'exchange | Fase 1 |
| `uv run python -m bot.data.quality` | Report di qualità dei dati in `reports/` | Fase 1 |
| `uv run python -m bot.backtest --strategy sma_cross` | Backtest con report HTML in `reports/` | Fase 2 |
| `uv run python -m bot.report` | Report delle performance del paper trading | Fase 4 |
| `uv run python -m bot.optimize --all` | Ottimizzazione walk-forward e confronto delle strategie | Fase 3 |
| `uv run python -m bot.evaluate --confirm-test-period` | Test finale (una sola volta) | Fase 3 |
| `uv run python -m bot.run --mode paper` | Avvia il paper trading sul Testnet | Fase 4 |

## Dati (Fase 1)

```bash
uv run python -m bot.data.download   # prima volta: qualche minuto; poi solo le candele nuove
uv run python -m bot.data.quality    # report in reports/data_quality_*.md
```

- Fonti: archivio mensile `data.binance.vision` (mesi conclusi, checksum verificato) e endpoint pubblico klines di Binance (il resto). Nessuna chiave API.
- Database: `data/market.duckdb`, tabelle `candles` ed `exchange_filters`. Orari sempre in UTC.
- Query di verifica in SQL: [`docs/QUERY_VERIFICA.md`](docs/QUERY_VERIFICA.md).
- Buchi e anomalie nei dati: vanno spiegati da un evento in `config/known_events.yaml` (es. un fermo di Binance), altrimenti il report è NON OK.

## Backtest (Fase 2)

```bash
uv run python -m bot.backtest --strategy buy_and_hold          # benchmark
uv run python -m bot.backtest --strategy sma_cross             # BTC/USDT 1h, periodo di training
uv run python -m bot.backtest --strategy sma_cross --symbol ETH/USDT --timeframe 4h --period validation
```

- Periodi fissi: `train` (default, 2020–2023), `validation` (2024), `test` (2025–oggi). Il test si usa una sola volta per strategia e richiede `--confirm-test-period`.
- Ogni backtest confronta tre varianti: strategia con risk manager, strategia senza, buy & hold. Verifica il buy & hold contro il calcolo manuale (soglia 0,01%) e salva il run nelle tabelle `runs` e `bt_*`.
- Parametri delle strategie in `config/strategies/*.yaml`, limiti di rischio in `config/risk.yaml`.

## Fase 3: ottimizzazione e confronto

```bash
uv run python -m bot.optimize --all                      # alcuni minuti; non legge mai il periodo di test
uv run python -m bot.evaluate --confirm-test-period      # solo alla fine, una volta sola
```

- Strategie candidate: `sma_cross`, `donchian_breakout`, `rsi_mean_reversion` e le stesse con il modificatore `trend_filter` (suffisso `_trend_filter`). Griglie in `config/strategies/*.yaml`.
- `bot.optimize`: per ogni strategia sceglie i parametri sul training (scelta robusta, niente picchi isolati), misura il walk-forward, valuta sulla validazione 2024 i quattro criteri del brief (con ETH/USDT per il criterio 4) e la robustezza su ETH e sul 4h. Report: `reports/fase3_confronto_*.html`.
- `bot.evaluate`: test finale sul periodo 2025–oggi, solo per le strategie promosse dalla validazione e con i parametri congelati. Una strategia già testata non si ritesta. Report: `reports/fase3_test_finale_*.html`.

## Struttura

| Percorso | Contenuto |
|---|---|
| `bot/` | Codice sorgente, un sottopacchetto per modulo (config, data, indicators, strategies, risk, execution, exchange, backtest, optimize, metrics, report, run) |
| `config/` | `settings.yaml`, `risk.yaml`, `strategies/*.yaml` |
| `tests/` | Test `pytest` |
| `docs/` | Brief e registro decisioni |
| `data/`, `reports/`, `logs/` | Generati in locale, non versionati |
