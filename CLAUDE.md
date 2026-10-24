# CLAUDE.md — Crypto Trading Bot (progetto didattico/sperimentale)

Questo file contiene le regole permanenti del progetto. Il documento di specifica completo è `docs/BRIEF_PROGETTO.md`: leggilo all'inizio di ogni nuova fase.

## Contesto

- Proprietario: Riccardo, profilo dati/BI (SQL esperto, Python intermedio). Spiega le scelte non ovvie, non le basi di SQL.
- Lingua: commenti, docstring, log e documentazione in **italiano**; nomi di variabili, funzioni e tabelle in **inglese**.
- Obiettivo: costruire un bot di trading spot su Binance, validarlo in backtest e paper trading, e solo dopo, eventualmente, usarlo con importi minimi.
- Il valore del progetto è **misurare onestamente** se una strategia funziona, non "far soldi in fretta". Se i numeri dicono che la strategia non batte il buy & hold dopo i costi, va detto chiaramente.

## Regole di sicurezza (non negoziabili)

1. **Modalità di default = `backtest`.** Le modalità sono `backtest`, `paper` (Binance Spot Testnet), `live`.
2. **Mai implementare, attivare o testare la modalità `live`** finché Riccardo non lo chiede esplicitamente in quella sessione, e solo dopo che la Fase 4 del brief è chiusa.
3. La modalità `live` richiede **tutte** queste condizioni, verificate dal codice all'avvio: `TRADING_MODE=live` nel `.env`, flag CLI `--i-understand-real-money`, file `config/live_limits.yaml` presente, limiti di rischio non nulli. Se manca una sola condizione il programma termina.
4. **Solo spot.** Vietati margin, futures, leverage, prestiti, staking, conversioni automatiche.
5. **Mai chiamare endpoint di prelievo o trasferimento.** Il codice non deve contenere nessuna chiamata a withdraw/transfer.
6. **Segreti:** chiavi API solo nel `.env` (escluso da git). Mai stamparle nei log, mai scriverle nel codice, mai committarle. Chiavi testnet e chiavi reali sono variabili diverse.
7. **Mai disattivare, aggirare o "allentare temporaneamente"** i controlli del risk manager per far passare un test o un ordine. Se un limite blocca qualcosa, segnalarlo.
8. Un file `STOP` nella root del progetto deve fermare il bot al ciclo successivo (kill switch manuale).

## Regole di lavoro

- **Una fase alla volta**, nell'ordine del brief. Ogni fase si chiude solo quando i suoi criteri di accettazione sono soddisfatti e verificati.
- **Prima di usare un'API o una libreria esterna, verifica la documentazione attuale** (endpoint, limiti, parametri cambiano). Non affidarti alla memoria per URL, rate limit o nomi di metodi.
- **Test:** ogni modulo di calcolo (commissioni, sizing, arrotondamenti ai filtri exchange, metriche, risk manager) ha test `pytest`. Esegui la suite prima di dichiarare finita una modifica.
- **Niente look-ahead:** un segnale calcolato sulla candela `t` (chiusa) si esegue al prezzo di apertura di `t+1`. Esiste un test dedicato che lo verifica.
- **Stesso codice per tutte le modalità:** la strategia è una funzione pura che riceve dati e restituisce segnali; backtest, paper e live cambiano solo l'esecutore.
- Chiedi prima di aggiungere dipendenze non elencate nel brief.
- Distingui sempre nei report ciò che è **misurato** da ciò che è **ipotesi**.
- Quando un risultato sembra troppo bello (es. Sharpe > 3, drawdown quasi zero), presumi un bug finché non è dimostrato il contrario.

## Comandi principali (da mantenere aggiornati)

| Comando | Cosa fa |
|---|---|
| `uv run python -m bot.data.download` | Scarica/aggiorna le candele storiche e i filtri dell'exchange |
| `uv run python -m bot.data.quality` | Report di qualità dei dati (exit code 1 se ci sono errori o buchi non spiegati) |
| `uv run python -m bot.backtest --strategy sma_cross` | Esegue un backtest (default: periodo di training) e scrive il report HTML in `reports/` |
| `uv run python -m bot.optimize --all` | Fase 3: ottimizzazione walk-forward sul training e confronto delle strategie sulla validazione (`--strategy X` per una sola) |
| `uv run python -m bot.evaluate --confirm-test-period` | Fase 3: test finale, una sola volta, sulle strategie promosse dalla validazione |
| `uv run python -m bot.run --mode paper` | Avvia il paper trading sul Testnet |
| `uv run python -m bot.report` | Report delle performance del paper trading (Fase 4); i report di backtest li genera `bot.backtest` |
| `uv run pytest` | Esegue la suite di test |
