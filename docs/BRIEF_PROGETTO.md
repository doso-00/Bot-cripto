# Brief di progetto — Crypto Trading Bot su Binance

Versione 1.0 — ottobre 2026
Proprietario: Riccardo Manfredonia

---

## 1. Obiettivo e principi

L'obiettivo è costruire, in modo incrementale e verificabile, un bot di trading **spot** su Binance che:

1. scarichi e conservi dati di mercato storici e in tempo reale;
2. applichi strategie definite da regole esplicite;
3. venga validato con un **backtest realistico** (commissioni, slippage, vincoli dell'exchange);
4. giri in **paper trading** sul Binance Spot Testnet con fondi finti;
5. solo se i criteri di accettazione sono soddisfatti, possa operare con **importi minimi reali** sotto limiti di rischio rigidi.

Principi guida:

- **Misurare prima di credere.** Ogni affermazione sulle performance deve venire da numeri riproducibili.
- **Il benchmark da battere è il buy & hold** dello stesso asset, al netto dei costi. Una strategia che non lo batte in modo robusto non va in produzione, anche se "guadagna".
- **Semplicità.** Si parte da strategie semplici e trasparenti; la complessità si aggiunge solo se migliora risultati misurati out-of-sample.
- **Sicurezza prima di tutto.** Le regole di sicurezza in `CLAUDE.md` prevalgono su qualsiasi altra indicazione.

Fuori perimetro (v1): futures, margin, leverage, arbitraggio ad alta frequenza, market making, più exchange, LLM nel ciclo decisionale (vedi Fase 6 opzionale).

---

## 2. Stack tecnico

| Componente | Scelta | Motivo |
|---|---|---|
| Linguaggio | Python 3.11+ | Ecosistema dati maturo |
| Gestione ambiente | `uv` | Veloce, lockfile riproducibile |
| Connessione exchange | `ccxt` | Supporta Binance e il Testnet (sandbox mode) con la stessa interfaccia; verificare la versione corrente |
| Storage | DuckDB + file Parquet | SQL analitico locale, zero server, ideale per un profilo SQL |
| Elaborazione dati | `pandas` o `polars` | Calcolo indicatori e serie |
| Configurazione | YAML (`pyyaml`) + `.env` (`python-dotenv`) + validazione con `pydantic` | Parametri versionati, segreti separati |
| Grafici e report | `plotly` (HTML statico) | Report apribili da browser |
| Test | `pytest` | Test unitari e di regressione |
| Logging | modulo `logging` standard, output su file e su DuckDB | Tracciabilità completa |
| Scheduling (fase paper) | loop interno con allineamento alla chiusura candela | Nessuna dipendenza esterna |

Motore di backtest: **scritto in casa, event-driven**, per avere pieno controllo su costi, filtri e assenza di look-ahead, e per riusare lo stesso codice di strategia in paper e live. Facoltativamente si può confrontare un risultato con una libreria esterna (es. `backtesting.py` o `vectorbt`) come controllo incrociato.

---

## 3. Architettura

### 3.1 Moduli

| Modulo | Responsabilità |
|---|---|
| `bot/config` | Carica e valida YAML + `.env`; espone un oggetto `Settings` tipizzato |
| `bot/data` | Download storico (bulk + API), aggiornamento incrementale, controllo qualità dati, accesso a DuckDB |
| `bot/indicators` | Funzioni pure per indicatori tecnici (SMA, EMA, RSI, ATR, Donchian) |
| `bot/strategies` | Strategie come funzioni pure: input = DataFrame di candele chiuse + parametri, output = segnale target (es. esposizione 0 o 1) |
| `bot/risk` | Risk manager: sizing, limiti per ordine, perdita giornaliera, drawdown massimo, kill switch |
| `bot/execution` | Tre esecutori con la stessa interfaccia: `BacktestExecutor`, `PaperExecutor` (Testnet), `LiveExecutor` |
| `bot/exchange` | Wrapper su `ccxt`: info filtri (lot size, notional minimo, tick size), arrotondamenti, gestione errori e rate limit |
| `bot/backtest` | Motore event-driven, applicazione costi e slippage |
| `bot/optimize` | Grid search e walk-forward sui soli dati di training |
| `bot/metrics` | Calcolo metriche di performance |
| `bot/report` | Report HTML con equity curve, drawdown, tabella operazioni, confronto con buy & hold |
| `bot/run` | Entry point del loop paper/live |
| `tests/` | Test unitari e di regressione |

### 3.2 Struttura cartelle

| Percorso | Contenuto |
|---|---|
| `CLAUDE.md` | Regole permanenti per Claude Code |
| `docs/BRIEF_PROGETTO.md` | Questo documento |
| `docs/DECISIONI.md` | Registro delle decisioni prese durante il progetto (data, decisione, motivo) |
| `config/settings.yaml` | Parametri generali (simboli, timeframe, costi, percorsi) |
| `config/strategies/*.yaml` | Parametri per strategia |
| `config/risk.yaml` | Limiti di rischio per paper |
| `config/live_limits.yaml` | Limiti di rischio per live (creato solo in Fase 5) |
| `data/raw/` | File scaricati in bulk (zip/CSV), non versionati |
| `data/market.duckdb` | Database locale, non versionato |
| `reports/` | Report HTML generati, non versionati |
| `logs/` | Log testuali, non versionati |
| `bot/` | Codice sorgente (moduli della sezione 3.1) |
| `tests/` | Test |
| `.env.example` | Modello delle variabili d'ambiente, senza valori reali |
| `.gitignore` | Esclude `.env`, `data/`, `reports/`, `logs/`, `STOP` |
| `pyproject.toml` | Dipendenze gestite da `uv` |

### 3.3 Flusso per modalità

| Passo | Backtest | Paper (Testnet) | Live |
|---|---|---|---|
| Sorgente dati | DuckDB storico | Candele chiuse dall'API pubblica Binance | Come paper |
| Strategia | Stessa funzione pura | Stessa funzione pura | Stessa funzione pura |
| Risk manager | Attivo | Attivo | Attivo, con limiti di `live_limits.yaml` |
| Esecuzione | Simulata al prezzo di apertura della candela successiva + slippage | Ordini reali sul Testnet, fondi finti | Ordini reali, fondi reali |
| Persistenza | Tabelle `bt_*` | Tabelle `orders`, `fills`, `equity_snapshots` con `mode='paper'` | Stesse tabelle con `mode='live'` |

---

## 4. Dati

### 4.1 Universo iniziale

- Simboli: `BTC/USDT` (principale), `ETH/USDT` (verifica di robustezza). Altri simboli solo dopo la Fase 3.
- Timeframe principale: **1 ora**. Timeframe di confronto: 4 ore.
- Storico: almeno **dal 2020** a oggi, così da includere mercati rialzisti, ribassisti e laterali.

### 4.2 Fonti

- **Storico bulk:** archivio pubblico dei dati Binance (file mensili di klines). Preferibile per scaricare anni di dati senza consumare rate limit.
- **Aggiornamento incrementale e real-time:** endpoint pubblico delle klines di Binance (nessuna chiave richiesta). Rispettare il limite di candele per richiesta e i rate limit: **verificare i valori attuali nella documentazione ufficiale.**
- **Filtri exchange:** endpoint `exchangeInfo` (lot size, notional minimo, tick size). Vanno letti e rispettati sia in backtest sia in esecuzione.
- **Cambio EUR/USDT:** per il registro fiscale, salvare il tasso al momento di ogni operazione (es. coppia `EUR/USDT` di Binance).

### 4.3 Modello dati (DuckDB)

| Tabella | Chiave | Colonne principali | Note |
|---|---|---|---|
| `candles` | `symbol, timeframe, open_time` | open, high, low, close, volume, close_time, is_closed | Solo candele chiuse vengono usate dalle strategie |
| `exchange_filters` | `symbol, valid_from` | min_qty, step_size, min_notional, tick_size | Storicizzata: i filtri cambiano |
| `runs` | `run_id` | mode, strategy, params_json, config_hash, git_commit, started_at, ended_at | Ogni esecuzione è riproducibile |
| `signals` | `run_id, symbol, candle_time` | target_exposure, indicator_values_json | Cosa ha deciso la strategia e perché |
| `orders` | `order_id` | run_id, mode, symbol, side, type, qty, price_requested, status, created_at, exchange_order_id | Include ordini rifiutati dal risk manager con motivo |
| `fills` | `fill_id` | order_id, qty, price, fee, fee_asset, filled_at, eur_usdt_rate | Base per fisco e analisi costi |
| `equity_snapshots` | `run_id, ts` | cash_usdt, position_qty, position_value, equity, drawdown | Una riga per candela |
| `risk_events` | `event_id` | run_id, ts, rule, detail, action_taken | Ogni intervento del risk manager |
| `bt_trades` | `run_id, trade_id` | entry_time, exit_time, entry_price, exit_price, qty, gross_pnl, fees, slippage_cost, net_pnl | Solo backtest |

### 4.4 Controllo qualità dati

Prima di ogni backtest verificare e riportare: buchi temporali (candele mancanti), duplicati, valori nulli o ≤ 0, high < low, close fuori dal range high/low, salti anomali di prezzo. Il backtest si rifiuta di partire se la qualità non è adeguata, a meno di un flag esplicito.

---

## 5. Strategie

Tutte le strategie restituiscono un'**esposizione target** (0 = tutto in USDT, 1 = tutto nell'asset, valori intermedi ammessi solo se previsti). Solo posizioni long: niente vendite allo scoperto.

| Strategia | Logica | Parametri principali | Ruolo |
|---|---|---|---|
| `buy_and_hold` | Compra alla prima candela, tiene fino alla fine | nessuno | **Benchmark obbligatorio** |
| `sma_cross` | Long quando SMA veloce > SMA lenta, flat altrimenti | fast, slow | Trend following di base |
| `donchian_breakout` | Long su rottura del massimo di N candele, uscita su rottura del minimo di M candele | n_entry, n_exit | Trend following alternativo |
| `rsi_mean_reversion` | Long quando RSI < soglia bassa, uscita quando RSI > soglia alta o dopo K candele | period, low, high, max_hold | Mean reversion, per confronto |
| `trend_filter` (modificatore) | Applica una delle precedenti solo se il prezzo è sopra la SMA 200 | sma_period | Riduce le operazioni in mercati ribassisti |

Regole comuni:

- Il segnale usa **solo candele chiuse** fino a `t`; l'ordine si esegue all'apertura di `t+1`.
- Nessun ribilanciamento se la variazione di esposizione è sotto una soglia minima (evita micro-ordini che sotto il notional minimo verrebbero rifiutati).
- Ogni strategia ha un file YAML di parametri e un test che verifica l'output su un piccolo dataset sintetico con risultato noto.

---

## 6. Backtest realistico

### 6.1 Costi e vincoli

| Elemento | Modellazione |
|---|---|
| Commissioni | 0,1% per lato come default (livello standard spot); parametrizzabile. Verificare il proprio livello commissioni sul conto |
| Slippage | Default 0,05% per lato; analisi di sensibilità a 0, 0,05%, 0,1%, 0,2% |
| Prezzo di esecuzione | Apertura della candela successiva al segnale, più slippage nella direzione sfavorevole |
| Quantità | Arrotondata per difetto allo step size; ordine scartato se sotto quantità minima o notional minimo |
| Capitale iniziale | 1.000 USDT di default (parametrizzabile) |
| Interessi su USDT inattivi | Ignorati (scelta conservativa) |

### 6.2 Metodologia anti-illusione

1. **Split temporale fisso:** training (es. 2020–2023), validazione (2024), test (2025–oggi). Il periodo di test si guarda **una sola volta** per strategia, alla fine.
2. **Ottimizzazione solo sul training**, con walk-forward (finestre scorrevoli: ottimizza su N mesi, verifica sui M successivi, avanza).
3. **Griglia di parametri limitata** e registrata: numero di combinazioni provate sempre riportato nel report (più combinazioni = più rischio di overfitting).
4. **Robustezza:** la strategia deve funzionare anche su ETH/USDT e su timeframe 4h senza riottimizzare; i parametri vicini all'ottimo devono dare risultati simili (niente "picchi isolati").
5. **Analisi per regime:** risultati separati per periodi rialzisti, ribassisti e laterali.

### 6.3 Metriche

| Metrica | Definizione |
|---|---|
| Rendimento netto totale | Equity finale / iniziale − 1, al netto di costi |
| CAGR | Rendimento annualizzato composto |
| Max drawdown | Massima perdita da un picco di equity |
| Durata max drawdown | Tempo massimo sotto un picco precedente |
| Sharpe | Media / deviazione standard dei rendimenti per periodo, annualizzata (crypto 24/7: 8.760 ore/anno su timeframe 1h) |
| Sortino | Come Sharpe ma solo con la volatilità negativa |
| Calmar | CAGR / max drawdown |
| Numero operazioni, win rate, profit factor | Profit factor = somma guadagni / somma perdite |
| Esposizione media | % di tempo investito nell'asset |
| Costi totali | Commissioni + slippage, in valore e in % del profitto lordo |
| Alpha vs buy & hold | Differenza di rendimento e di drawdown rispetto al benchmark |

### 6.4 Report

Un file HTML per run con: parametri e hash di configurazione, equity curve strategia vs buy & hold, curva di drawdown, tabella metriche, istogramma dei rendimenti per operazione, tabella operazioni, risultati per regime, sensibilità allo slippage, avvisi automatici (es. "Sharpe sospettosamente alto", "meno di 30 operazioni: campione non significativo").

---

## 7. Risk management

| Regola | Default paper | Note |
|---|---|---|
| Esposizione massima per simbolo | 100% del capitale del bot | Il "capitale del bot" è un budget dedicato, non il saldo totale del conto |
| Importo massimo per singolo ordine | 25% del capitale del bot | Ordini più grandi vengono spezzati o rifiutati |
| Perdita giornaliera massima | 3% | Raggiunta: niente nuovi acquisti fino a mezzanotte UTC |
| Drawdown massimo dal picco | 15% | Raggiunto: chiude le posizioni e si ferma; serve riavvio manuale |
| Ordini massimi per ora | 6 | Protegge da loop impazziti |
| Dati vecchi | Se l'ultima candela chiusa ha più di 2 intervalli di ritardo, nessun ordine | Protegge da API ferme |
| Divergenza prezzo | Se il prezzo d'esecuzione stimato differisce oltre lo 0,5% dall'ultimo close, nessun ordine | Protegge da spike e dati errati |
| Kill switch | File `STOP` nella root | Controllato a ogni ciclo |
| Riconciliazione | A ogni ciclo, il saldo reale sull'exchange deve coincidere con quello atteso entro una tolleranza | Se no: stop e allarme |

Ogni intervento del risk manager viene registrato in `risk_events` con motivo.

---

## 8. Esecuzione paper e live

### 8.1 Binance Spot Testnet

- Ambiente separato con fondi finti e API compatibili con quelle reali. Le chiavi si generano sul sito del Testnet, sono diverse da quelle reali e il Testnet può essere azzerato periodicamente: il bot deve gestire saldi che ripartono da capo.
- Con `ccxt` si attiva la modalità sandbox; **verificare nella documentazione corrente** come si configura per Binance spot.

### 8.2 Ciclo del bot

1. Attende la chiusura della candela successiva (allineamento all'orario + piccolo margine).
2. Controlla il file `STOP`.
3. Scarica le ultime candele chiuse e verifica freschezza e qualità.
4. Calcola il segnale con la strategia.
5. Passa il segnale al risk manager, che calcola l'ordine consentito o lo rifiuta.
6. Arrotonda ai filtri exchange e invia l'ordine (market o limit con timeout, da decidere e documentare in `DECISIONI.md`).
7. Registra ordine, fill, commissioni, cambio EUR/USDT.
8. Riconcilia i saldi e salva lo snapshot di equity.
9. Gestisce errori di rete con retry limitati e backoff; dopo N errori consecutivi si ferma.

### 8.3 Dove gira

Il paper trading deve girare per settimane: serve una macchina sempre accesa (PC lasciato acceso o un piccolo server/VPS). Il bot deve essere riavviabile senza perdere stato: lo stato si ricostruisce da DuckDB e dai saldi sull'exchange.

### 8.4 Chiavi API reali (solo Fase 5)

- Permessi: **solo lettura + trading spot**. Prelievi **disattivati**.
- **Restrizione IP** attiva sull'indirizzo della macchina che fa girare il bot.
- Sub-account o budget dedicato con importo che si è disposti a perdere interamente.

---

## 9. Fasi e criteri di accettazione

### Fase 0 — Setup

- Repository git, `uv`, `pyproject.toml`, struttura cartelle, `.gitignore`, `.env.example`, configurazione `pytest`.
- **Fatto quando:** `uv run pytest` gira (anche con un solo test segnaposto) e il README spiega come installare.

### Fase 1 — Dati

- Download bulk + incrementale per BTC/USDT e ETH/USDT, 1h e 4h, dal 2020.
- Tabelle `candles` ed `exchange_filters` popolate; report di qualità dati.
- **Fatto quando:** nessun buco non spiegato, nessun duplicato, query di verifica documentate, test sul controllo qualità.

### Fase 2 — Motore di backtest e benchmark

- Motore event-driven, costi, slippage, filtri, metriche, report HTML.
- Strategia `buy_and_hold` e `sma_cross`.
- **Fatto quando:** buy & hold nel backtest coincide con il calcolo manuale (prezzo finale / iniziale meno costi) entro lo 0,01%; test anti-look-ahead superato; test su commissioni, arrotondamenti e notional minimo superati.

### Fase 3 — Strategie, ottimizzazione e validazione

- Tutte le strategie della sezione 5, walk-forward, analisi di robustezza e per regime.
- **Fatto quando:** esiste un report comparativo; una strategia passa alla Fase 4 **solo se** sul periodo di test (mai usato prima) soddisfa tutti questi criteri:
  - rendimento netto > buy & hold **oppure** rendimento simile con max drawdown almeno del 30% inferiore;
  - almeno 30 operazioni;
  - risultato positivo anche con slippage 0,1%;
  - risultato coerente su ETH/USDT senza riottimizzare.
- Se nessuna strategia passa, il risultato corretto è scriverlo nel report e fermarsi o rivedere le ipotesi. **Non abbassare i criteri.**

### Fase 4 — Paper trading sul Testnet

- Esecutore paper, risk manager completo, riconciliazione, kill switch, logging.
- Durata minima: **4 settimane** di funzionamento continuo.
- **Fatto quando:** nessun crash non gestito nelle ultime 2 settimane; ogni ordine tracciato; le performance del paper sono coerenti con un backtest rieseguito sullo stesso periodo (differenze spiegate); tutti i test del risk manager superati, incluso un test che simula la perdita giornaliera massima.

### Fase 5 — Live con importi minimi (solo su richiesta esplicita)

- Esecutore live, `live_limits.yaml`, controlli di avvio della sezione "Regole di sicurezza" di `CLAUDE.md`.
- Budget iniziale minimo, appena sopra il notional minimo necessario per operare.
- Revisione settimanale: confronto live vs paper vs backtest.
- **Fatto quando:** 4 settimane senza incidenti; decisione esplicita di Riccardo su se e come aumentare il budget.

### Fase 6 — Opzionale: componente LLM

- Solo dopo la Fase 4. Ipotesi da testare: un segnale di "sentiment" (es. notizie o post) prodotto da un LLM migliora una strategia esistente.
- Va trattato come qualsiasi altro segnale: backtest con dati storici datati correttamente (niente informazioni del futuro), confronto con e senza il segnale, stessi criteri della Fase 3.
- L'LLM **non** invia mai ordini direttamente: produce un valore numerico che entra nella strategia, e passa comunque dal risk manager.

---

## 10. Test minimi obbligatori

| Test | Verifica |
|---|---|
| `test_no_lookahead` | Modificando le candele future rispetto a `t`, il segnale a `t` non cambia |
| `test_buy_and_hold_matches_manual` | Risultato del backtest = calcolo manuale |
| `test_fees` | Commissioni calcolate correttamente su acquisto e vendita |
| `test_rounding_filters` | Quantità arrotondate allo step size; ordini sotto il notional minimo scartati |
| `test_slippage_direction` | Lo slippage peggiora sempre il prezzo |
| `test_risk_daily_loss` | Superata la perdita giornaliera, nuovi acquisti bloccati |
| `test_risk_max_drawdown` | Superato il drawdown massimo, il bot chiude e si ferma |
| `test_kill_switch` | Con il file `STOP` presente nessun ordine viene inviato |
| `test_stale_data` | Con dati vecchi nessun ordine viene inviato |
| `test_live_guard` | La modalità live non parte se manca anche una sola condizione di sicurezza |
| `test_no_withdraw_calls` | Ricerca statica nel codice: nessuna chiamata a funzioni di prelievo/trasferimento |

---

## 11. Configurazione

Esempio di `.env.example`:

```
TRADING_MODE=backtest
BINANCE_TESTNET_API_KEY=
BINANCE_TESTNET_API_SECRET=
BINANCE_LIVE_API_KEY=
BINANCE_LIVE_API_SECRET=
```

Le chiavi live restano vuote fino alla Fase 5.

Parametri principali di `config/settings.yaml`:

| Chiave | Default | Descrizione |
|---|---|---|
| `symbols` | `[BTC/USDT, ETH/USDT]` | Universo |
| `timeframe` | `1h` | Timeframe principale |
| `history_start` | `2020-01-01` | Inizio storico |
| `initial_capital_usdt` | `1000` | Capitale per backtest/paper |
| `fee_rate` | `0.001` | Commissione per lato |
| `slippage_rate` | `0.0005` | Slippage per lato |
| `train_end` | `2023-12-31` | Fine periodo training |
| `validation_end` | `2024-12-31` | Fine periodo validazione |
| `min_rebalance_delta` | `0.1` | Variazione minima di esposizione per generare un ordine |

---

## 12. Aspetti fiscali e registro operazioni

- In Italia, dal 1° gennaio 2026, le plusvalenze su cripto-attività sono tassate al 33% senza soglia di esenzione (le stablecoin in euro conformi MiCA hanno regime diverso). Verificare con un commercialista il trattamento delle operazioni del bot.
- Il bot deve poter esportare un CSV annuale con: data/ora UTC, simbolo, lato, quantità, prezzo, commissione e valuta della commissione, controvalore in USDT, tasso EUR/USDT, controvalore in EUR.
- Lo storico in DuckDB non va mai cancellato; i backup sono responsabilità del proprietario.

---

## 13. Registro decisioni

Ogni scelta non banale (tipo di ordine, gestione errori, cambio di libreria, modifica di un criterio) va annotata in `docs/DECISIONI.md` con data, decisione, alternative scartate e motivo.

---

## 14. Prompt di avvio per Claude Code

Da incollare nella prima sessione, con `CLAUDE.md` nella root e questo file in `docs/`:

```
Leggi CLAUDE.md e docs/BRIEF_PROGETTO.md.
Riassumimi in 10 righe cosa hai capito, segnala eventuali incoerenze o punti da decidere, poi esegui SOLO la Fase 0.
Quando la Fase 0 rispetta i criteri di accettazione, fermati e mostrami cosa hai fatto prima di passare alla Fase 1.
```
