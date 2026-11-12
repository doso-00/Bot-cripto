# Dashboard azioni — Fase A: piano di implementazione

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** entro lunedì 16/11/2026 una pagina privata, aggiornata alle ~06:50 e ~23:15 da un'attività programmata
Claude, che per la watchlist e le posizioni di Riccardo mostra azione, piano (stop/obiettivo/scadenza) e spiegazione
delle notizie, con il piano NVIDIA pronto per la trimestrale del 17/11.

**Architecture:** pacchetto Python `stocks/` che produce numeri e segnali deterministici (`snapshot.json`); la sessione
Claude programmata legge notizie e scrive `claude_review.json`; un validatore applica solo le revisioni ammesse; un
renderer produce l'HTML pubblicato come Artifact. Lo stato persistente (posizioni, piani, registro) vive nel
database dell'Artifact e viene passato al Python come file JSON locale.

**Tech Stack:** Python 3.12, uv, pandas, pydantic, pyyaml, yfinance 1.8 (nuova, approvata), pytest.

**Spec:** `docs/superpowers/specs/2026-11-12-stock-dashboard-design.md` (leggerlo prima di iniziare).

## Global Constraints

- Commenti, docstring, log, testi della pagina in **italiano**; nomi di variabili, funzioni, file in **inglese**.
- Nessun test usa la rete: il livello dati si testa con oggetti finti (monkeypatch), come in `tests/conftest.py`.
- Nessun codice che si colleghi a Trade Republic o invii ordini. Nessuna credenziale broker richiesta o salvata.
- Dipendenze ammesse in Fase A: quelle già in `pyproject.toml` + `yfinance`. Nient'altro senza chiedere.
- Niente look-ahead: i segnali del giorno t usano solo barre giornaliere **chiuse** con data ≤ t.
- Ogni numero mostrato viene dallo snapshot calcolato dal codice, mai dal testo di Claude.
- Ogni `COMPRA`/`ACCUMULA` in Fase A porta `validated = False` e in pagina l'etichetta "regola non ancora validata".
- Valori della spec da usare (in `config/stocks/settings.yaml`): budget 1000 €, ordine minimo 100 €, commissione 1 €
  per ordine, tasse 26% sulle plusvalenze, base 80% del budget in 3–5 posizioni con max 30% per titolo, occasioni in
  tutto max 20% del budget (ognuna ≥ 100 €), evento max 100 €, stop occasione max −8%, rapporto
  obiettivo/rischio ≥ 2, base: revisione a −15% e uscita a −20% dal prezzo medio, blocco nuovi acquisti se il
  portafoglio scende del 15% dal massimo. **L'importo minimo vince sugli altri limiti.**
- Fuso orario della pagina: `Europe/Rome`. Date interne: `datetime.date` (giorno di borsa del titolo).
- I comandi nuovi vanno aggiunti alla tabella di `CLAUDE.md` e al `README.md` (Task 11).

## Review Focus

1. **Yahoo fallisce o restituisce dati vuoti per un titolo** (429, ticker delistato): gli altri titoli proseguono; quel
   titolo ha `data_ok = False`, azione `DATI_MANCANTI`, nessun acquisto; la pagina lo dice in alto. → Task 4, Task 8.
2. **Esecuzione con una barra di oggi non ancora chiusa** (run manuale a metà giornata): la barra viene scartata. → Task 4.
3. **Trimestrale con data passata ma utile non ancora pubblicato** (sera del 17/11 prima delle 22:20): resta `ATTENDI`;
   diventa "risultati usciti" solo quando `Reported EPS` è presente. → Task 5, Task 6.
4. **Revisione di Claude assente, malformata o con ticker sconosciuti**: la pagina esce lo stesso con i soli segnali
   del codice e l'avviso "revisione notizie non disponibile". → Task 9.
5. **Stato vuoto al primo avvio, o posizione su un titolo fuori watchlist**: funziona; il titolo in portafoglio viene
   seguito comunque. → Task 7, Task 8.

---

## File structure

```
config/stocks/settings.yaml      # limiti e soglie (Global Constraints)
config/stocks/watchlist.yaml     # titoli seguiti + indici di riferimento
stocks/__init__.py
stocks/config.py                 # modelli pydantic + load_settings / load_watchlist
stocks/models.py                 # modelli condivisi: Action, Plan, Signal, TickerSnapshot, Snapshot, ...
stocks/indicators.py             # funzioni pure
stocks/costs.py                  # commissioni, tasse, pareggio
stocks/risk.py                   # sizing e blocchi
stocks/data.py                   # yfinance + retry + normalizzazione
stocks/earnings.py               # prossima trimestrale, movimenti storici, movimento atteso
stocks/signals.py                # motore regole → Signal
stocks/state.py                  # State (posizioni, piani, registro) su JSON
stocks/review.py                 # schema revisione Claude + validatore
stocks/refresh.py  (+ __main__)  # orchestratore: python -m stocks.refresh
stocks/apply_review.py (+ main)  # python -m stocks.apply_review
stocks/render.py   (+ main)      # python -m stocks.render → out/dashboard.html
stocks/PROMPT_REFRESH.md         # istruzioni della sessione programmata
tests/stocks/...                 # un file di test per modulo
```

Scelta: entry point come moduli con `if __name__ == "__main__"` dentro `refresh.py`, `apply_review.py`, `render.py`
(comando `python -m stocks.refresh` ecc.). Cartella di output `out/` (aggiungerla a `.gitignore`).

---

### Task 1: Prova del ciclo cloud (spike, usa e getta)

Decide la persistenza prima di scrivere codice (spec §7). Nessun test, nessun commit di codice.

- [ ] **Step 1:** pubblicare un Artifact di prova con `capabilities: {db: {}, user: {}}` e scrivere un documento
  `state/probe` con `ArtifactData set`.
- [ ] **Step 2:** creare un'attività programmata one-shot (`create_trigger`, `run_once_at` tra ~3 minuti,
  `create_new_session_on_fire: true`, `notifications: {push: true}`) con prompt: "Clona doso-00/Bot-cripto (usa add_repo
  se non è presente), leggi con ArtifactData il doc state/probe dell'artifact <url>, scrivi state/probe2 con l'ora
  attuale, ripubblica l'artifact <url> con una riga in più, rispondi con l'esito di ogni passo."
- [ ] **Step 3:** leggere l'esito con `list_events` sulla sessione creata. Verificare: repo disponibile, `uv sync` ok,
  rete Yahoo ok, `ArtifactData` lettura/scrittura ok, ripubblicazione ok, avviso push ricevuto (chiedere a Riccardo).
- [ ] **Step 4:** annotare l'esito in `docs/DECISIONI.md`. Se `ArtifactData` o la pubblicazione non funzionano dalla
  sessione programmata, si passa al ripiego della spec §7 (branch `stocks-state`) e si aggiornano i Task 7 e 11 prima di
  proseguire. Cancellare l'artifact di prova solo con il consenso di Riccardo.

### Task 2: Struttura, configurazione e modelli condivisi

**Files:**
- Create: `config/stocks/settings.yaml`, `config/stocks/watchlist.yaml`, `stocks/__init__.py`, `stocks/config.py`,
  `stocks/models.py`, `tests/stocks/__init__.py`, `tests/stocks/test_config.py`
- Modify: `pyproject.toml` (`packages = ["bot", "stocks"]`, dipendenza `yfinance`), `.gitignore` (`/out/`)

**Interfaces:**
- Produces:
  - `StocksSettings` (pydantic) con i campi: `budget_eur=1000`, `min_order_eur=100`, `fee_per_order_eur=1.0`,
    `capital_gains_tax=0.26`, `core_share=0.80`, `core_min_positions=3`, `core_max_positions=5`,
    `core_max_weight=0.30`, `opp_max_total_share=0.20`, `opp_max_open=2`, `event_max_eur=100`,
    `opp_max_stop_pct=0.08`, `opp_atr_stop_mult=2.0`, `opp_atr_target_mult=4.0`, `opp_min_reward_risk=2.0`,
    `opp_max_days=30`, `core_review_drawdown=0.15`, `core_exit_drawdown=0.20`, `portfolio_halt_drawdown=0.15`,
    `event_window_days=5`, `history_years=5`, più le soglie di qualità `quality_min_roe=0.08`,
    `quality_max_debt_to_equity=150`, `quality_max_payout=0.80`, `core_pullback_min=0.05`, `core_pullback_max=0.15`,
    `opp_pullback_min=0.10`, `opp_rsi_max=35`.
  - `WatchItem(ticker: str, note: str = "", proposed_by: Literal["riccardo","claude"])`;
    `Watchlist(items: list[WatchItem], benchmarks: dict[str, str])` (es. `{"US": "^GSPC", "EU": "^STOXX50E"}`).
  - `load_settings(path: Path = Path("config/stocks/settings.yaml")) -> StocksSettings`;
    `load_watchlist(path: Path = Path("config/stocks/watchlist.yaml")) -> Watchlist`.
  - In `stocks/models.py`: `Action` (StrEnum: `COMPRA, ACCUMULA, TIENI, ATTENDI, RIVEDI, RIDUCI, VENDI,
    NESSUNA_AZIONE, DATI_MANCANTI`); `PositionKind` (`core, opportunity, event`); `Plan`, `Signal`, `Fundamentals`,
    `EarningsInfo`, `TickerSnapshot`, `PortfolioSummary`, `Snapshot` come modelli pydantic con i campi elencati nei Task
    3–8 (ogni task aggiunge i campi che produce, in questo file).

Watchlist iniziale: `NVDA` (`proposed_by: riccardo`) e, marcati `proposed_by: claude` con nota "esempio da confermare",
`MSFT`, `JNJ`, `KO`, `ENEL.MI`, `ISP.MI`. Riccardo può toglierli; la pagina mostra chi li ha proposti.

- [ ] **Step 1: test** `test_load_settings_defaults` (valori sopra letti dal YAML), `test_settings_rejects_negative_budget`,
  `test_watchlist_has_nvda_and_benchmarks`, `test_settings_yaml_matches_spec_values` (budget 1000, min order 100,
  fee 1.0, tax 0.26, core_share 0.80, opp_max_total_share 0.20).
- [ ] **Step 2:** `uv run pytest tests/stocks/test_config.py -v` → FAIL (modulo inesistente).
- [ ] **Step 3:** implementare; `uv add yfinance`; verificare che `uv run pytest` (tutta la suite, incluso `bot/`) resti verde.
- [ ] **Step 4:** `uv run pytest tests/stocks/test_config.py -v` → PASS.
- [ ] **Step 5:** commit `Azioni: configurazione, modelli e dipendenza yfinance`.

### Task 3: Indicatori, costi e rischio

**Files:**
- Create: `stocks/indicators.py`, `stocks/costs.py`, `stocks/risk.py`, `tests/stocks/test_indicators.py`,
  `tests/stocks/test_costs.py`, `tests/stocks/test_risk.py`

**Interfaces:**
- Consumes: `StocksSettings`, `PositionKind`.
- Produces:
  - `sma(close: pd.Series, n: int) -> pd.Series`; `atr(df: pd.DataFrame, n: int = 14) -> pd.Series` (Wilder, colonne
    `high, low, close`); `rsi(close: pd.Series, n: int = 14) -> pd.Series` (Wilder);
    `drawdown_from_high(close: pd.Series, window: int = 63) -> pd.Series` (valore ≥ 0, es. 0.12 = −12% dal massimo).
  - `round_trip_fees(s: StocksSettings) -> float` (= 2 × commissione);
    `net_pnl_eur(buy_eur: float, sell_eur: float, s: StocksSettings) -> float` (commissioni sempre; tasse 26% solo se
    la plusvalenza è positiva, calcolate sulla plusvalenza al netto delle commissioni);
    `breakeven_price(entry: float, qty: float, s: StocksSettings) -> float`.
  - `Holding(ticker: str, kind: PositionKind, qty: float, avg_price: float, currency: str)` (in `models.py`).
  - `max_order_eur(kind: PositionKind, holdings_eur: dict[PositionKind, float], open_opps: int, s: StocksSettings) -> tuple[float, str | None]`
    → (importo, motivo del blocco o `None`). Regole: core = min(30% budget, budget core residuo); opportunity =
    budget occasioni residuo, solo se `open_opps < opp_max_open`; event = min(event_max_eur, budget occasioni residuo);
    se l'importo < `min_order_eur` → `(0.0, "importo sotto il minimo di 100 €")`.
  - `portfolio_halted(equity_history_eur: list[float], s: StocksSettings) -> bool` (calo ≥ 15% dal massimo).

- [ ] **Step 1: test** — `test_sma_matches_manual`, `test_atr_wilder_known_values` (serie costruita a mano),
  `test_rsi_all_up_is_100`, `test_drawdown_zero_at_new_high`;
  `test_round_trip_fees_is_2_eur`, `test_net_pnl_loss_has_no_tax` (100→90 ⇒ −12.0),
  `test_net_pnl_gain_taxed_26pct` (100→120 ⇒ (20−2)×0.74 = 13.32), `test_breakeven_covers_fees`;
  `test_core_capped_at_30pct` (300.0), `test_opportunity_blocked_when_two_open`,
  `test_event_capped_at_100`, `test_min_order_wins` (budget occasioni residuo 60 € ⇒ `(0.0, motivo)`),
  `test_portfolio_halt_at_15pct` (1000 → 850 ⇒ True; 1000 → 860 ⇒ False).
- [ ] **Step 2:** eseguire → FAIL.
- [ ] **Step 3:** implementare.
- [ ] **Step 4:** `uv run pytest tests/stocks -v` → PASS.
- [ ] **Step 5:** commit `Azioni: indicatori, costi e limiti di rischio`.

### Task 4: Livello dati (yfinance)

**Files:**
- Create: `stocks/data.py`, `tests/stocks/test_data.py`, `tests/stocks/fakes.py` (oggetti finti di `yf.Ticker`)

**Interfaces:**
- Produces:
  - `with_retry(fn: Callable[[], T], attempts: int = 4, base_delay: float = 2.0, sleep=time.sleep) -> T`
    (attese 2, 4, 8 s; rilancia l'ultima eccezione).
  - `fetch_history(ticker: str, years: int, now: datetime, client=yf) -> pd.DataFrame` → indice `date`
    (`datetime.date`), colonne `open, high, low, close, adj_close, volume`; usa `auto_adjust=False`; **scarta la barra di
    oggi se il mercato del titolo non è ancora chiuso** (`drop_incomplete_bar`); DataFrame vuoto se Yahoo non dà dati.
  - `drop_incomplete_bar(df: pd.DataFrame, now: datetime, exchange_tz: str, close_time: time) -> pd.DataFrame`
    (chiusure: USA 16:00 `America/New_York`; Milano/Xetra/Parigi 17:30 `Europe/Rome`/`Europe/Berlin`/`Europe/Paris`;
    margine 20 minuti). Fuso e orario si ricavano dal suffisso del ticker (nessun suffisso → USA; `.MI`, `.DE`, `.PA`,
    `.AS` → Europa); suffisso ignoto → si tratta come USA e si aggiunge un avviso.
  - `fetch_fx_eurusd(now: datetime, client=yf) -> float` (ultimo close chiuso di `EURUSD=X`, cioè USD per 1 EUR).
  - `fetch_fundamentals(ticker: str, client=yf) -> Fundamentals` con `name, currency, trailing_pe, forward_pe,
    dividend_yield_pct, payout_ratio, debt_to_equity, roe, market_cap, ex_dividend_date: date | None` (quest'ultimo
    da `Ticker.calendar["Ex-Dividend Date"]`, serve al calendario della pagina). **Attenzione:** yfinance 1.8 restituisce
    `dividendYield` già in percentuale (NVDA 0.58 = 0,58%), `payoutRatio` e `returnOnEquity` come frazioni,
    `debtToEquity` in percento (16.97 = 0,17×). Campi mancanti → `None`.
  - `fetch_dividends(ticker: str, client=yf) -> pd.Series` e `dividend_years_without_cut(divs: pd.Series, asof: date) -> int`
    (anni solari completi consecutivi, a ritroso, con somma annua ≥ quella dell'anno precedente).
  - `to_eur(amount: float, currency: str, eurusd: float) -> float` (EUR invariato; USD diviso per `eurusd`; altre valute
    → `ValueError`).

- [ ] **Step 1: test** — `test_retry_succeeds_on_third_attempt`, `test_retry_raises_after_four`,
  `test_history_normalized_columns_and_date_index`, `test_history_empty_when_yahoo_returns_nothing`,
  `test_drop_incomplete_bar_us_during_session` (now = 2026-11-12 18:00 Europe/Rome ⇒ barra del 12/11 scartata),
  `test_keep_bar_after_us_close` (now = 2026-11-12 23:15 Europe/Rome ⇒ tenuta),
  `test_keep_bar_milan_after_close` (`ENEL.MI`, now 18:00 ⇒ tenuta), `test_fundamentals_missing_fields_are_none`,
  `test_dividend_yield_kept_as_percent`, `test_dividend_years_without_cut` (2020 1.0, 2021 1.1, 2022 0.9, 2023 1.0,
  2024 1.2, 2025 1.3, asof 2026-11-12 ⇒ 3), `test_to_eur_usd`, `test_to_eur_rejects_gbp`.
- [ ] **Step 2:** eseguire → FAIL.
- [ ] **Step 3:** implementare; client iniettabile per i test (`client` con `.Ticker(...)`).
- [ ] **Step 4:** test → PASS; poi prova reale fuori dai test:
  `uv run python -c "from stocks.data import *; ..."` su `NVDA` e `ENEL.MI` → righe > 1000, ultima data coerente.
- [ ] **Step 5:** commit `Azioni: livello dati yfinance con retry e scarto della barra aperta`.

### Task 5: Trimestrali

**Files:**
- Create: `stocks/earnings.py`, `tests/stocks/test_earnings.py`

**Interfaces:**
- Consumes: `fetch_history` (DataFrame prezzi), `with_retry`.
- Produces:
  - `fetch_earnings(ticker: str, client=yf) -> pd.DataFrame` da `Ticker.get_earnings_dates(limit=12)` → colonne
    `ts` (datetime con fuso), `eps_estimate`, `eps_reported`, `surprise_pct`. Vuoto se non disponibile.
  - `EarningsInfo` (in `models.py`): `next_ts: datetime | None`, `days_to_next: int | None`,
    `reported_since_last_run: bool`, `last_ts: datetime | None`, `last_surprise_pct: float | None`,
    `past_moves_pct: list[float]`, `avg_abs_move_pct: float | None`, `expected_move_pct: float | None`.
  - `earnings_info(edf: pd.DataFrame, prices: pd.DataFrame, asof_now: datetime) -> EarningsInfo`:
    - prossimo evento = primo `ts` con `eps_reported` NaN e `ts` ≥ (oggi − 1 giorno); un evento con data passata ma
      senza `eps_reported` **resta il prossimo** (Review Focus 3);
    - `reported_since_last_run` = l'ultimo evento con `eps_reported` ha `ts` entro le ultime 36 ore;
    - movimento storico (ultime 8 trimestrali con prezzo disponibile): se l'ora del `ts` è ≥ 16:00 locale (dopo la
      chiusura) → `close(giorno successivo) / close(giorno ts) − 1`; altrimenti (prima dell'apertura) →
      `close(giorno ts) / close(giorno precedente) − 1`.
  - `expected_move_pct(ticker: str, event_date: date, spot: float, client=yf) -> float | None`: prima scadenza
    opzioni ≥ data evento; straddle ATM = (mid call + mid put allo strike più vicino a `spot`) / `spot`; mid =
    (bid+ask)/2, se bid o ask = 0 usa `lastPrice`; `None` se non ci sono opzioni (es. titoli europei).

- [ ] **Step 1: test** — `test_next_event_future`, `test_past_date_without_eps_is_still_next` (ts 17/11 16:00 ET,
  now 17/11 22:00 Europe/Rome, eps NaN ⇒ next = 17/11), `test_reported_since_last_run_true_after_release`,
  `test_after_close_move_uses_next_day`, `test_pre_open_move_uses_same_day`, `test_past_moves_max_eight`,
  `test_expected_move_straddle` (spot 100, call mid 5, put mid 4 ⇒ 0.09), `test_expected_move_none_without_options`.
- [ ] **Step 2:** eseguire → FAIL.
- [ ] **Step 3:** implementare.
- [ ] **Step 4:** test → PASS; prova reale su `NVDA`: next = 17/11/2026, 8 movimenti storici, movimento atteso non `None`.
- [ ] **Step 5:** commit `Azioni: trimestrali, movimenti storici e movimento atteso dalle opzioni`.

### Task 6: Motore dei segnali e piani

**Files:**
- Create: `stocks/signals.py`, `tests/stocks/test_signals.py`, `tests/stocks/test_no_lookahead_stocks.py`

**Interfaces:**
- Consumes: indicatori, `max_order_eur`, `breakeven_price`, `EarningsInfo`, `Fundamentals`, `Holding`.
- Produces:
  - `Plan` (in `models.py`): `kind: PositionKind`, `entry_ref: float`, `stop: float | None`, `target: float | None`,
    `deadline: date | None`, `review_level: float | None`, `exit_level: float | None`, `currency: str`,
    `stop_eur: float | None`, `target_eur: float | None`, `scenarios: list[str]`, `stop_order_on_tr: bool`
    (True per opportunity/event: il livello va inserito come ordine stop-loss su Trade Republic).
  - `Signal`: `action: Action`, `kind: PositionKind | None`, `amount_eur: float | None`, `plan: Plan | None`,
    `reasons: list[str]` (italiano, frasi brevi), `validated: bool = False`, `blocked_by: str | None`.
  - `quality_check(f: Fundamentals, div_years: int, s: StocksSettings) -> tuple[bool, list[str]]`
    (utili positivi: `trailing_pe > 0`; `roe ≥ 0.08`; `debt_to_equity ≤ 150` o `None`; `payout ≤ 0.80` o `None`;
    i dividendi sono un plus nei motivi, non un requisito).
  - `evaluate(prices: pd.DataFrame, f: Fundamentals, div_years: int, e: EarningsInfo, holding: Holding | None, plan: Plan | None, asof: date, ctx: SizingContext, eurusd: float, s: StocksSettings) -> Signal`
    con `SizingContext(holdings_eur: dict[PositionKind, float], open_opps: int, halted: bool)`.

Regole, nell'ordine (la prima che si applica vince):
1. `prices` vuoto o ultima barra più vecchia di 5 giorni di calendario rispetto ad `asof` → `DATI_MANCANTI`.
2. **Posizione aperta, kind core:** close ≤ avg × (1 − 0.20) → `VENDI`; ≤ avg × (1 − 0.15) → `RIVEDI`;
   altrimenti `TIENI` (motivo aggiuntivo se trimestrale entro 5 giorni).
3. **Posizione aperta, kind opportunity/event:** close ≤ stop → `VENDI` ("stop raggiunto");
   close ≥ target → `VENDI` ("obiettivo raggiunto"); asof > deadline → `VENDI` ("scadenza"); altrimenti `TIENI`.
4. **Non posseduto, trimestrale entro `event_window_days` e non ancora uscita** → `ATTENDI` con piano a due scenari
   (testi: "Se i risultati battono le attese e il titolo regge: …", "Se deludono: …") e il movimento atteso/storico nei
   motivi. Nessun acquisto prima dei risultati (decisione di Riccardo/Claude, spec §0).
5. **Ingresso core:** qualità ok **e** close > SMA200 **e** drawdown 63g tra 5% e 15% → `COMPRA` kind core con
   `review_level = close × 0.85`, `exit_level = close × 0.80`, deadline `None` (orizzonte 6–24 mesi nei motivi).
6. **Occasione:** drawdown 63g ≥ 10% **e** RSI14 ≤ 35 → stop = max(close − 2×ATR, close × 0.92); target =
   min(close + 4×ATR, massimo 63g); scartata se (target − close − costi per azione) / (close − stop) < 2;
   deadline = asof + 30 giorni di calendario → `COMPRA` kind opportunity.
7. Altrimenti `NESSUNA_AZIONE` con il motivo principale.
Dopo le regole: se l'azione è `COMPRA`/`ACCUMULA` e `ctx.halted` → `NESSUNA_AZIONE` con `blocked_by` = "portafoglio
in calo del 15%: nuovi acquisti bloccati"; altrimenti importo = `max_order_eur(...)`, e se 0 → `NESSUNA_AZIONE` con
`blocked_by` = motivo. Livelli in EUR = `to_eur(livello, currency, eurusd)` arrotondati per difetto (stop) e per eccesso
(target) a 2 decimali.

- [ ] **Step 1: test** (dati sintetici costruiti nel test) — un test per ciascuna regola 1–7, più
  `test_halt_blocks_buy`, `test_min_order_blocks_buy`, `test_buy_never_validated_in_phase_a`,
  `test_event_plan_has_two_scenarios`, `test_opportunity_rejected_when_reward_risk_below_2`,
  `test_stop_eur_rounded_down`. In `test_no_lookahead_stocks.py`: `test_future_bars_do_not_change_signal` (stesso
  segnale ad `asof` anche cambiando a caso le barre successive) e `test_bars_after_asof_are_ignored`.
- [ ] **Step 2:** eseguire → FAIL.
- [ ] **Step 3:** implementare (`evaluate` taglia `prices` a `date ≤ asof` come prima istruzione).
- [ ] **Step 4:** test → PASS.
- [ ] **Step 5:** commit `Azioni: motore regole con piani di uscita e test anti look-ahead`.

### Task 7: Stato persistente

**Files:**
- Create: `stocks/state.py`, `tests/stocks/test_state.py`

**Interfaces:**
- Produces:
  - `Trade(ts: datetime, ticker: str, side: Literal["buy","sell"], qty: float, price: float, currency: str, fee_eur: float = 1.0, kind: PositionKind, note: str = "")`.
  - `PlanRevision(ts: datetime, ticker: str, field: str, old: str | None, new: str | None, source: Literal["engine","claude","riccardo"], reason: str, accepted: bool)`.
  - `JournalEntry(ts: datetime, ticker: str, action: Action, close: float, reasons: list[str], source: Literal["engine","claude"], event_class: str | None = None)`.
  - `State(version: int = 1, trades: list[Trade] = [], plans: dict[str, Plan] = {}, revisions: list[PlanRevision] = [], journal: list[JournalEntry] = [], equity_history_eur: list[tuple[date, float]] = [])`.
  - `load_state(path: Path) -> State` (file assente o vuoto → `State()`); `save_state(state: State, path: Path) -> None`
    (JSON con date ISO).
  - `holdings_from_trades(trades: list[Trade]) -> dict[str, Holding]` (prezzo medio ponderato; vendita totale toglie
    la posizione; vendita oltre la quantità → `ValueError`).

- [ ] **Step 1: test** — `test_missing_file_gives_empty_state`, `test_roundtrip_json`,
  `test_holdings_weighted_average` (2 @ 100 + 1 @ 130 ⇒ 3 @ 110), `test_full_sell_removes_holding`,
  `test_oversell_raises`, `test_holding_outside_watchlist_is_kept` (verificato poi in Task 8).
- [ ] **Step 2:** eseguire → FAIL. **Step 3:** implementare. **Step 4:** test → PASS.
- [ ] **Step 5:** commit `Azioni: stato persistente (operazioni, piani, registro)`.

### Task 8: Orchestratore `stocks.refresh`

**Files:**
- Create: `stocks/refresh.py`, `tests/stocks/test_refresh.py`

**Interfaces:**
- Consumes: tutto quanto sopra.
- Produces:
  - `TickerSnapshot`: `ticker, name, currency, proposed_by, asof: date, close, close_eur, change_1d_pct, sma200, atr14,
    rsi14, drawdown_63d_pct, high_63d, fundamentals: Fundamentals, dividend_years_without_cut: int,
    earnings: EarningsInfo, holding: Holding | None, pnl_net_eur: float | None, signal: Signal, data_ok: bool,
    data_issues: list[str]`.
  - `PortfolioSummary`: `invested_eur, value_eur, pnl_net_eur, cash_eur, halted: bool, peak_value_eur`.
  - `Snapshot`: `generated_at: datetime, run_slot: Literal["mattina","sera","manuale"], eurusd: float,
    benchmarks: dict[str, float]` (variazione 1g %), `tickers: list[TickerSnapshot], portfolio: PortfolioSummary,
    issues: list[str]`.
  - `run_refresh(now: datetime, state_path: Path, out_dir: Path, settings: StocksSettings, watchlist: Watchlist, client=yf) -> Snapshot`
    — titoli seguiti = watchlist ∪ titoli in portafoglio; un errore su un titolo non ferma gli altri (Review Focus 1);
    scrive `out/snapshot.json`; aggiorna nello stato `plans` (piano del segnale per i nuovi `COMPRA` non viene salvato
    finché Riccardo non registra l'acquisto), `journal` (una voce per titolo con azione ≠ run precedente) e
    `equity_history_eur`; salva lo stato.
  - CLI: `uv run python -m stocks.refresh [--state out/state.json] [--out out] [--slot mattina|sera|manuale]`;
    exit code 0 anche con titoli in errore (elencati in `issues`), 2 se nessun titolo ha dati.

- [ ] **Step 1: test** (client finto da `fakes.py`, nessuna rete) — `test_refresh_writes_snapshot`,
  `test_one_ticker_failing_does_not_stop_others` (NVDA ok, XXX solleva ⇒ XXX `DATI_MANCANTI`, issue presente),
  `test_holding_outside_watchlist_is_followed`, `test_first_run_empty_state`, `test_exit_code_2_when_no_data`,
  `test_journal_only_on_action_change`.
- [ ] **Step 2:** eseguire → FAIL. **Step 3:** implementare. **Step 4:** test → PASS.
- [ ] **Step 5:** prova reale: `uv run python -m stocks.refresh --slot manuale`; aprire `out/snapshot.json` e verificare
  che NVDA abbia `ATTENDI` con trimestrale 17/11 e che i prezzi coincidano con quelli di Yahoo.
- [ ] **Step 6:** commit `Azioni: orchestratore refresh e snapshot`.

### Task 9: Revisione di Claude e validatore

**Files:**
- Create: `stocks/review.py`, `stocks/apply_review.py`, `tests/stocks/test_review.py`

**Interfaces:**
- Consumes: `Snapshot`, `State`, `PlanRevision`.
- Produces:
  - Schema (pydantic, `extra="forbid"`): `NewsItem(title, url, source, published: date | None)`;
    `Proposal(type: Literal["tighten_stop","shorten_deadline","extend_deadline","request_review","downgrade_buy","watch"], value: str | None, reason: str, reason_kind: Literal["evento","notizia","tecnico"])`;
    `TickerReview(ticker, event_class: Literal["trimestrale_sopra_attese","trimestrale_sotto_attese","guidance_su","guidance_giu","taglio_dividendo","aumento_dividendo","acquisizione","legale","macro","nessuno"], summary: str, explanation: str, news: list[NewsItem], proposals: list[Proposal])`;
    `ClaudeReview(generated_at: datetime, tickers: list[TickerReview], glossary: list[GlossaryItem(term, definition)], market_note: str)`.
  - `apply(snapshot: Snapshot, review: ClaudeReview | None, state: State, now: datetime) -> FinalView`
    (`FinalView(snapshot, reviews: dict[str, TickerReview], applied: list[PlanRevision], rejected: list[PlanRevision], review_available: bool)`).
    Regole di ammissione:
    - `tighten_stop`: ammesso solo se il nuovo stop > stop attuale e < close; altrimenti rifiutato.
    - `shorten_deadline`: ammesso se la nuova data è < deadline attuale e ≥ oggi.
    - `extend_deadline`: ammesso solo con `reason_kind == "evento"` e al massimo +30 giorni.
    - `request_review`: azione → `RIVEDI` (per titoli posseduti).
    - `downgrade_buy`: `COMPRA`/`ACCUMULA` → `NESSUNA_AZIONE` con il motivo di Claude.
    - `watch`: solo un'etichetta "da guardare".
    - Proposta su ticker non presente nello snapshot, o con `value` non interpretabile → rifiutata.
    - Nessun tipo di proposta può creare un `COMPRA`, aumentare un importo o abbassare uno stop.
    Tutte le revisioni (accettate e rifiutate) finiscono in `state.revisions`; ogni `TickerReview` con
    `event_class != "nessuno"` diventa una `JournalEntry` con `source="claude"`.
  - CLI `uv run python -m stocks.apply_review` → legge `out/snapshot.json`, `out/claude_review.json` (assente o non
    valido ⇒ `review=None`, `review_available=False`, Review Focus 4), `out/state.json`; scrive `out/final.json` e lo stato.

- [ ] **Step 1: test** — uno per regola di ammissione (accettato e rifiutato), più
  `test_cannot_lower_stop`, `test_cannot_create_buy`, `test_unknown_ticker_rejected`,
  `test_malformed_review_falls_back`, `test_missing_review_falls_back`, `test_extra_fields_rejected`,
  `test_rejected_are_logged`.
- [ ] **Step 2:** FAIL. **Step 3:** implementare. **Step 4:** PASS.
- [ ] **Step 5:** commit `Azioni: schema della revisione di Claude e validatore`.

### Task 10: Pagina HTML

**Files:**
- Create: `stocks/render.py`, `stocks/templates/dashboard.html` (template con segnaposto `string.Template`),
  `tests/stocks/test_render.py`

Prima di scrivere il template caricare le skill `artifact-design` e `dataviz` e seguirne il contratto (titolo di 2–4
parole "Dashboard azioni", token colore su `:root` con tema scuro, larghezza 360 px senza scroll orizzontale, niente
script esterni se non da CDN ammessi).

**Interfaces:**
- Consumes: `FinalView`, `State`.
- Produces: `render_html(view: FinalView, state: State, settings: StocksSettings) -> str`; CLI
  `uv run python -m stocks.render` → `out/dashboard.html`.

Sezioni, nell'ordine della spec §8: intestazione (ora di aggiornamento in Europe/Rome, stato dati, prossimo evento),
"Cosa fare", portafoglio (netto stimato, piano, storico revisioni), notizie spiegate, calendario (trimestrali e stacco
dividendi dei titoli seguiti), "Il modello" (in Fase A: testo fisso "arriva a dicembre"), glossario, proposte scartate,
avviso fisso "Strumento informativo, non consulenza finanziaria. Gli ordini li decidi e li esegui tu." Ogni numero ha
l'etichetta `misurato` o `stima`; ogni `COMPRA`/`ACCUMULA` ha "regola non ancora validata"; ogni piano opportunity/event
mostra "Inserisci su Trade Republic un ordine stop-loss a X €". Tutto il testo che arriva da Claude passa da
`html.escape`; i link delle notizie solo se `https://`.

- [ ] **Step 1: test** — `test_render_contains_disclaimer`, `test_buy_has_not_validated_label`,
  `test_claude_text_is_escaped` (`<script>` nel summary ⇒ `&lt;script&gt;`), `test_non_https_links_dropped`,
  `test_review_unavailable_banner`, `test_data_missing_banner`, `test_stop_order_hint_for_opportunity`,
  `test_times_shown_in_rome_tz`.
- [ ] **Step 2:** FAIL. **Step 3:** implementare. **Step 4:** PASS.
- [ ] **Step 5:** generare la pagina dai dati reali del Task 8 e controllarla con Playwright a 360 px e a 1280 px, tema
  chiaro e scuro (screenshot a `deviceScaleFactor: 1` per controllo interno).
- [ ] **Step 6:** commit `Azioni: pagina HTML della dashboard`.

### Task 11: Sessione programmata, pubblicazione e documentazione

**Files:**
- Create: `stocks/PROMPT_REFRESH.md`
- Modify: `CLAUDE.md` (sezione "Dashboard azioni": regole + comandi), `README.md`, `docs/DECISIONI.md`

`PROMPT_REFRESH.md` — istruzioni complete e autosufficienti per una sessione nuova, nell'ordine dell'architettura
(spec §5): clonare il repo se manca, `uv sync`; leggere lo stato dal database dell'artifact (`ArtifactData`, doc
`state/current`) e scriverlo in `out/state.json`; `uv run python -m stocks.refresh --slot <mattina|sera>`; leggere
`out/snapshot.json`; per ogni titolo posseduto, con evento entro 5 giorni, con `COMPRA` o con variazione ≥ 3%,
cercare notizie (WebSearch, fonti datate ultime 48 h) e scrivere `out/claude_review.json` secondo lo schema di
`stocks/review.py` (spiegazioni per principianti, nessun numero non presente nello snapshot, voci di glossario per i
termini nuovi); `uv run python -m stocks.apply_review`; `uv run python -m stocks.render`; ripubblicare l'artifact
(leggerlo prima, poi pubblicare `out/dashboard.html` sullo stesso URL); salvare `out/state.json` in `state/current`;
inviare l'avviso push solo se c'è un'azione ∉ {TIENI, ATTENDI, NESSUNA_AZIONE}. Nessun commit, nessuna modifica al
codice o alla configurazione da parte della sessione programmata.

- [ ] **Step 1:** scrivere `PROMPT_REFRESH.md`; aggiornare `CLAUDE.md`, `README.md` e `docs/DECISIONI.md` (scelte di
  questo piano: watchlist con titoli proposti da Claude, regole di ingresso non validate, orari, persistenza).
- [ ] **Step 2:** `uv run pytest` → tutta la suite verde.
- [ ] **Step 3:** eseguire a mano il ciclo completo del prompt in questa sessione e pubblicare il primo Artifact
  (`capabilities: {db: {}, user: {}}`), scrivere `state/current`; verificare con `ArtifactData list`.
- [ ] **Step 4:** unire il branch al branch predefinito del repository (`claude/new-session-mtnp2x`, approvato da
  Riccardo) e fare push.
- [ ] **Step 5:** creare le due attività programmate (`create_trigger`, `create_new_session_on_fire: true`,
  `notifications: {push: true}`): `CRON_TZ=Europe/Rome 52 6 * * 1-5` ("mattina") e `CRON_TZ=Europe/Rome 17 23 * * 1-5`
  ("sera"); prompt = "Esegui stocks/PROMPT_REFRESH.md con slot <mattina|sera> e artifact <url>".
- [ ] **Step 6:** `fire_trigger` sulla "sera" e verificare con `list_events` che la pagina sia stata ripubblicata
  e lo stato salvato.
- [ ] **Step 7:** commit `Azioni: sessione programmata, documentazione e comandi`.

### Task 12: Verifica di accettazione della Fase A

- [ ] **Step 1:** `uv run pytest` → verde (incluso `bot/`).
- [ ] **Step 2:** sulla pagina pubblicata: NVDA `ATTENDI` con trimestrale mar 17/11, due scenari, movimento atteso e
  storico; ogni titolo della watchlist presente; disclaimer presente; dati e ora corretti; leggibile da telefono.
- [ ] **Step 3:** dopo il primo run programmato reale: pagina aggiornata, stato salvato, revisioni registrate.
- [ ] **Step 4:** annotare la chiusura della Fase A in `docs/DECISIONI.md` con ciò che è misurato e ciò che è ipotesi;
  commit e push.
