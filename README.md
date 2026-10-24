# Bot-cripto

Bot di trading **spot** su Binance, progetto didattico/sperimentale. L'obiettivo è misurare onestamente se una strategia batte il buy & hold dopo i costi: prima in backtest, poi in paper trading sul Binance Spot Testnet, e solo dopo, eventualmente, con importi minimi reali.

- Regole permanenti e di sicurezza: [`CLAUDE.md`](CLAUDE.md)
- Specifica completa e fasi: [`docs/BRIEF_PROGETTO.md`](docs/BRIEF_PROGETTO.md)
- Registro delle decisioni: [`docs/DECISIONI.md`](docs/DECISIONI.md)

**Stato:** Fase 1 (dati): codice completo e testato; il download reale e la chiusura della fase si fanno sulla macchina dell'utente. Nessuna funzionalità di trading è ancora implementata.

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
| `uv run python -m bot.backtest --strategy sma_cross` | Esegue un backtest | Fase 2 |
| `uv run python -m bot.report` | Genera il report delle performance | Fase 2 |
| `uv run python -m bot.optimize --strategy sma_cross` | Ottimizzazione walk-forward | Fase 3 |
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

## Struttura

| Percorso | Contenuto |
|---|---|
| `bot/` | Codice sorgente, un sottopacchetto per modulo (config, data, indicators, strategies, risk, execution, exchange, backtest, optimize, metrics, report, run) |
| `config/` | `settings.yaml`, `risk.yaml`, `strategies/*.yaml` |
| `tests/` | Test `pytest` |
| `docs/` | Brief e registro decisioni |
| `data/`, `reports/`, `logs/` | Generati in locale, non versionati |
