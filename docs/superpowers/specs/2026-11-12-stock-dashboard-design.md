# Dashboard azioni — documento di progetto

Data: 2026-11-12 — Stato: **approvato** il 2026-11-12 — Proprietario: Riccardo

## 0. Decisioni di Riccardo (12/11)

- Budget: **1000 €** disponibili. Nessuna posizione aperta (NVIDIA non posseduta).
- Approvate: dipendenze `yfinance` e `scikit-learn`, accesso di rete agli host Yahoo nell'ambiente cloud, unione del
  codice al branch principale (necessaria perché le esecuzioni programmate lo clonano da lì).
- NVIDIA prima della trimestrale: Riccardo non ha preferenze. **Scelta di Claude:** nessun acquisto prima dei risultati;
  la pagina mostra un piano con due scenari (sopra/sotto attese), l'aggiornamento delle 23:15 del 17/11 li valuta e la
  mattina del 18/11 la pagina propone l'azione. Motivo: una trimestrale è un evento binario (±10% possibile in una notte),
  e con un budget piccolo conviene rinunciare a parte del guadagno potenziale piuttosto che scommettere.
- Uscite in perdita: **scelta di Claude** (raccomandazione accettata con "approvo tutto"): per le occasioni il piano
  indica un ordine stop-loss da inserire su Trade Republic, con il livello in EUR; per la base lo stop resta un
  livello di revisione sulla pagina.

## 1. Intento

Riccardo vuole investire 500–1000 € in azioni tramite Trade Republic e non conosce ancora bene la borsa. Vuole una
dashboard che, **due volte al giorno (mattina e sera)**, gli dica cosa comprare, per quanto tenerlo e quando vendere,
con un orizzonte che si aggiorna in base alle notizie (es. la trimestrale NVIDIA del 17/11/2026). L'obiettivo
dichiarato è **"provare a non perdere soldi"** ed essere sempre pronti per il giorno dopo. Preferisce titoli con
dividendo, se ha senso. Vuole anche **capire**: ogni segnale deve arrivare con una spiegazione in parole semplici.

Perché adesso: NVIDIA è scesa, Riccardo vorrebbe comprarne una parte e decidere cosa fare dopo i risultati del 17/11.
La prima versione deve quindi funzionare **entro lunedì 16/11/2026**.

Lo spirito è quello del resto del repository (`CLAUDE.md`): **misurare onestamente**. Se un segnale o il modello di
ML non aggiunge informazione rispetto a un riferimento semplice, la dashboard lo dice.

## 2. Obiettivi, non-obiettivi, anti-obiettivi

**Obiettivi**
- Pagina privata, leggibile dal telefono, aggiornata alle ~07:00 e alle ~23:00 (ora italiana).
- Per ogni titolo seguito: azione consigliata, piano scritto (uscita in perdita, obiettivo, data limite), motivazione.
- Strategia "base + occasioni": ~80% del budget in 3–5 titoli solidi tenuti per mesi, ~20% per occasioni di giorni o
  settimane (es. NVIDIA attorno alla trimestrale).
- Lista di titoli scelti da Riccardo + filtro automatico su un universo ampio che propone 3–5 candidati a settimana.
- Lettura delle notizie e spiegazioni didattiche in italiano, con glossario che cresce.
- Un piccolo modello di ML che stima probabilità, **valutato fuori campione contro riferimenti semplici** e mostrato
  con la sua affidabilità misurata.
- Avviso sul telefono solo quando c'è un'azione da fare.
- Registro di tutto (segnali, piani, revisioni, previsioni, giudizi di Claude) per misurare dopo qualche mese se aiuta.

**Non-obiettivi**
- Nessun collegamento a Trade Republic e nessun ordine automatico: gli ordini li fa Riccardo a mano.
- Niente trading intraday, opzioni, leva, CFD, vendite allo scoperto.
- Niente previsioni del prezzo puntuale ("domani varrà 123 €").

**Anti-obiettivi (fallimento anche se "funziona")**
- Numeri inventati: ogni prezzo, data o dato di bilancio deve venire da una fonte dati via codice, mai dal testo di un
  modello linguistico.
- Consigli che ignorano i costi (1 € a ordine, tasse) o che spingono a operare spesso.
- Un'interfaccia che suona più sicura di quanto i numeri giustifichino (es. "probabilità 80%" senza dire quanto il
  modello ha sbagliato in passato).
- Claude che scavalca le regole di rischio (allarga uno stop, aumenta un importo) perché "la notizia è buona".

## 3. Vincoli e fatti verificati

| Fatto | Stato | Fonte / nota |
|---|---|---|
| Trimestrale NVIDIA Q3 FY2027: martedì 17/11/2026, call alle 14:00 PT (23:00 in Italia), dopo la chiusura USA | verificato 12/11 | comunicato NVIDIA |
| Trade Republic: 1 € a ordine, piani d'accumulo senza commissioni | fonti terze, da confermare sull'app | justetf, promarketer |
| Trade Republic Italia (IBAN italiano) in regime amministrato dal 2025: trattiene 26% su plusvalenze e dividendi, bollo 0,20% | fonti terze, da confermare | rankia, aziendabanca |
| Dividendi USA: ritenuta USA 15% + 26% italiano sul netto (~37% totale); dividendi italiani: solo 26% | **ipotesi**, da verificare prima di mostrarla come fatto | — |
| Trade Republic non ha API pubbliche | noto | — |
| Questa sessione cloud non raggiunge Yahoo/Stooq/SEC: rete limitata | verificato 12/11 | serve autorizzare gli host nell'ambiente |
| yfinance è non ufficiale, Yahoo limita le richieste senza limiti pubblicati (errori 429) | verificato 12/11 | servono cache, pause e retry |

Altri vincoli:
- Regole del repository (`CLAUDE.md`): lingua italiana per commenti/log/doc, nomi in inglese, test `pytest` sui moduli
  di calcolo, niente look-ahead, chiedere prima di aggiungere dipendenze.
- Il progetto cripto (`bot/`) resta intatto: la Fase 3 è chiusa senza strategie promosse.
- Il brief cripto escludeva l'LLM dal ciclo decisionale. Qui Claude **spiega e segnala**, ma le decisioni
  (azione, importi, stop) restano del codice deterministico (§6.4).

## 4. Approcci considerati

1. **Tutto in codice, aggiornato da GitHub Actions.** Gratuito, misurabile, ma capisce male le notizie (solo titoli e
   parole chiave) e le spiegazioni sono testi fissi. Scartato perché non soddisfa la richiesta principale: leggere le
   notizie e spiegarle a un principiante.
2. **Codice per i numeri + Claude per notizie e spiegazioni, con un'attività programmata.** ← **Scelto da Riccardo.**
   Il codice Python calcola dati, segnali, ML e limiti di rischio (testati). Due volte al giorno una sessione Claude
   programmata esegue il codice, legge le notizie, propone revisioni dei piani entro limiti fissati dal codice, scrive le
   spiegazioni e pubblica la pagina. Costo: consumo del piano Claude a ogni aggiornamento.
3. Scartati prima della scelta: **PC di casa con attività pianificata** (se il PC è spento non aggiorna, scomodo da
   telefono) e **solo Claude senza codice** (non misurabile, rischio di numeri inventati).

## 5. Architettura

```
 attività programmata Claude (07:00 e 23:00, sessione nuova ogni volta)
   │
   ├─ 1. legge lo stato (posizioni registrate da Riccardo, piani, registro)    ← database della pagina
   ├─ 2. `uv run python -m stocks.refresh`  → dati, indicatori, motore regole, ML, rischio
   │        scrive  out/snapshot.json  (solo numeri + segnali deterministici)
   ├─ 3. Claude legge le notizie (ricerca web) sui titoli rilevanti
   │        scrive  out/claude_review.json  (sintesi, eventi, proposte di revisione, spiegazioni)
   ├─ 4. `uv run python -m stocks.apply_review` → valida le proposte contro le regole, scarta quelle vietate
   ├─ 5. `uv run python -m stocks.render`  → pagina HTML
   ├─ 6. pubblica la pagina (Artifact, stesso URL) e salva stato e registro
   └─ 7. avviso sul telefono se c'è almeno un'azione da fare
```

Codice nuovo in un pacchetto separato `stocks/` nello stesso repository, test in `tests/stocks/`. Nessun import da
`bot/` verso `stocks/` e viceversa, salvo utilità generiche se davvero identiche.

### 5.1 Moduli

| Modulo | Responsabilità |
|---|---|
| `stocks/config` | Carica `config/stocks/*.yaml` (budget, limiti, watchlist, universo) e valida con pydantic |
| `stocks/data` | Download prezzi giornalieri, dividendi, date delle trimestrali, dati fondamentali, cambio EUR/USD; cache e retry |
| `stocks/indicators` | Funzioni pure: rendimenti, volatilità, ATR, distanza dai massimi, medie mobili |
| `stocks/screener` | Filtro settimanale sull'universo: qualità, dividendo, valutazione, trend → candidati |
| `stocks/signals` | Motore regole deterministico: azione per titolo + piano (stop, obiettivo, data limite) |
| `stocks/risk` | Sizing in EUR, importo minimo per ordine, limiti per titolo e portafoglio, blocchi |
| `stocks/costs` | Commissioni, tasse stimate, soglia di convenienza di un'operazione |
| `stocks/ml` | Feature, addestramento walk-forward, riferimenti, metriche, registro previsioni |
| `stocks/review` | Schema JSON della revisione di Claude e validatore (cosa Claude può e non può cambiare) |
| `stocks/state` | Lettura/scrittura di posizioni, piani, registro (§7) |
| `stocks/render` | Genera la pagina HTML da snapshot + revisione validata |

## 6. Comportamento

### 6.1 Titoli seguiti

- **Watchlist** di Riccardo in `config/stocks/watchlist.yaml` (all'inizio: NVDA; Riccardo ne aggiunge altri).
- **Universo per il filtro**: S&P 500 + grandi europee (Euro Stoxx 50) + FTSE MIB, da un elenco CSV statico versionato
  in `config/stocks/universe.csv` e aggiornato a mano ogni trimestre. Limitato ai titoli disponibili su Trade Republic
  (verifica a cura del builder: se non esiste un elenco affidabile, si accetta l'universo e si segnala in pagina
  "verifica che sia acquistabile su Trade Republic").
- **Aggiornamento giornaliero** (2 volte): posizioni aperte + watchlist + candidati attivi (~20–50 titoli).
- **Filtro sull'universo**: una volta a settimana (lunedì mattina), per non stressare Yahoo.

### 6.2 Base (~80%) — titoli tenuti per mesi

Criteri del filtro (proposta, soglie in YAML): utili positivi, debito sotto controllo, almeno 5 anni di dividendi
senza tagli (preferenza, non obbligo), payout sostenibile, valutazione non estrema rispetto alla propria storia, trend
non in caduta libera. Output: punteggio e motivazione per criterio.

Azioni possibili: `COMPRA`, `ACCUMULA`, `TIENI`, `ATTENDI` (evento imminente: piano pronto, nessuna azione prima
dell'evento), `RIVEDI` (qualcosa si è rotto: va letta la spiegazione), `RIDUCI`, `VENDI`.

**Regole di ingresso (scelta del builder, da validare).** In Fase A le regole sono semplici e trasparenti: titolo che
supera i criteri di qualità del §6.2, sopra la media mobile a 200 giorni, e in ritracciamento (es. −5/−15% dal massimo
a 3 mesi). Finché non sono state misurate su dati storici (Fase B), ogni `COMPRA`/`ACCUMULA` porta l'etichetta
**"regola non ancora validata"**. In Fase B un backtest con costi e senza look-ahead confronta le regole con il "compra
e tieni" dell'indice; se non lo battono, la pagina lo dice e le regole restano etichettate. Orizzonte indicativo 6–24 mesi, rivalutato a ogni trimestrale. Uscita: soglia di revisione a −15% e
uscita a −20% dal prezzo medio di carico, salvo che il motivo dell'investimento sia intatto **e** lo dica una regola
scritta (non un giudizio di Claude).

### 6.3 Occasioni (~20%) — giorni o settimane

- Al massimo 2 occasioni aperte insieme.
- Proposta solo se, al netto dei costi (2 € tra entrata e uscita + tasse stimate), il rapporto tra guadagno fino
  all'obiettivo e perdita fino allo stop è almeno 2 (scelta del builder, in YAML). L'obiettivo si fissa a un multiplo
  dell'ATR o al massimo recente più vicino, il più basso dei due. Non è una probabilità: è un rapporto rischio/rendimento
  dichiarato, etichettato come **stima**.
- Ogni occasione nasce con un **piano scritto prima dell'acquisto**: uscita in perdita (basata sull'ATR, mai oltre −8%),
  obiettivo, data limite (es. 10–30 giorni di borsa).
- **Eventi binari** (trimestrale entro N giorni): l'occasione è marcata "evento", con importo ridotto (max 100 € con
  budget 1000 €), e mostra il **movimento atteso** (dalla volatilità implicita delle opzioni, se disponibile) e i
  **movimenti storici del titolo dopo le ultime 8 trimestrali**, entrambi etichettati come misurati.
- Caso NVIDIA: la pagina mostra il piano prima del 17/11 e, all'aggiornamento serale del 17/11 (dopo le 23:00), Claude
  legge i risultati e propone la revisione; il codice decide se la revisione è ammessa.

### 6.4 Cosa può fare Claude (livello notizie)

Claude produce `out/claude_review.json` secondo uno schema fisso. Per ogni titolo può:

| Può | Non può |
|---|---|
| Riassumere le notizie con fonti (link) | Inserire prezzi, date o numeri di bilancio non presenti nello snapshot |
| Classificare un evento (trimestrale sopra/sotto attese, guidance, taglio dividendo, acquisizione, causa legale, macro) | Allargare uno stop o abbassarne il livello |
| Stringere uno stop, accorciare la data limite, chiedere `RIVEDI` | Aumentare un importo o creare un `COMPRA` |
| Allungare la data limite **solo** con un motivo di tipo evento esplicito | Togliere un blocco del risk manager |
| Segnalare un titolo "da guardare" | Cambiare soglie, limiti o configurazione |
| Scrivere spiegazioni e voci di glossario per principianti | |

Il validatore (`stocks/review`) applica solo le proposte ammesse; quelle vietate vengono scartate e **registrate**
come tali (visibili in pagina in una sezione "proposte scartate"). Ogni giudizio di Claude viene salvato con data,
titolo, prezzo in quel momento e classificazione, per poterlo valutare a posteriori.

### 6.5 Rischio e importi (proposta, tutto in YAML)

- Budget configurabile (default 1000 €). Importo minimo per ordine 100 € (commissione ≤ 1%). **In caso di conflitto
  vince l'importo minimo**: si riduce il numero di posizioni, non si scende sotto 100 €.
- Base: 3–5 posizioni (con meno budget: meno posizioni), max 30% del budget per titolo. Occasioni: in tutto max 20%
  del budget, ognuna di almeno 100 € (con 500 € → una sola occasione alla volta).
- Portafoglio: se il valore totale scende del 15% dal massimo → stop ai nuovi acquisti e messaggio di revisione.
- Ogni acquisto proposto ha già il piano di uscita. Nessuna proposta senza piano.
- Tasse e commissioni sempre incluse nel guadagno/perdita mostrato ("netto stimato").

### 6.6 Machine learning

Scopo: dare una probabilità onesta, non un oracolo.

- **Obiettivo da prevedere**: il rendimento del titolo nei prossimi 20 giorni di borsa supera quello del suo indice di
  riferimento (S&P 500 o Euro Stoxx 50)? Classificazione binaria con probabilità.
- **Feature** calcolate solo con dati disponibili alla chiusura del giorno t (momentum 1/3/6/12 mesi, volatilità,
  distanza dai massimi, RSI, giorni alla prossima trimestrale, rendimento da dividendo, regime di mercato). Il segnale
  si considera eseguibile all'apertura di t+1. Test dedicato anti look-ahead.
- **Modelli**: regressione logistica e gradient boosting di scikit-learn; niente reti neurali.
- **Valutazione walk-forward** con pausa di 20 giorni tra addestramento e test (per non sovrapporre gli obiettivi).
- **Riferimenti da battere**: frequenza storica (prevedere sempre la media) e una regola di momentum 12-1 mesi.
- **Metriche**: Brier score, AUC, curva di calibrazione, differenza di rendimento tra il 20% più alto e quello più
  basso delle previsioni.
- **Soglia di utilità scritta prima di guardare i risultati**: il modello entra nel motore regole solo se batte entrambi
  i riferimenti fuori campione (soglia in YAML, decisa in Fase C prima del primo addestramento). Altrimenti la pagina
  lo mostra con l'etichetta "non batte il riferimento: non usato".
- **Registro previsioni live**: ogni previsione salvata e valutata dopo 20 giorni; la pagina mostra l'accuratezza
  reale accumulata.
- Limite noto, da dichiarare in pagina: l'universo usa i titoli di oggi (bias di sopravvivenza).

## 7. Stato e persistenza

Ogni aggiornamento gira in una sessione cloud nuova (repository clonato da zero, `data/` vuota). Serve quindi uno stato
che sopravviva:

- **Posizioni registrate da Riccardo** (compra/vendi: titolo, quantità, prezzo, data, commissione): inserite dal
  telefono con un modulo nella pagina, salvate nel database della pagina (capacità `db` dell'Artifact). La sessione
  programmata le legge con `ArtifactData`.
- **Piani, revisioni, registro previsioni e giudizi di Claude**: nel database della pagina, documenti piccoli e
  strutturati.
- **Prezzi storici**: riscaricati a ogni aggiornamento per i ~50 titoli seguiti; filtro settimanale con risultati salvati
  nel database della pagina.
- **Export per SQL**: comando `uv run python -m stocks.export` che scarica il registro in DuckDB/CSV locale, così
  Riccardo può interrogarlo.

Il builder verifica per primo che una sessione programmata possa leggere e scrivere il database della pagina. Se non è
possibile, il ripiego è un branch dedicato `stocks-state` con file JSON piccoli, aggiornato con commit a ogni run.

## 8. La pagina

Una sola pagina, prima il telefono:

```
┌────────────────────────────────────────┐
│ Aggiornata: gio 12/11 23:04 · dati OK  │
│ Prossimo evento: NVDA trimestrale      │
│   mar 17/11 ~22:20 (dopo chiusura USA) │
├────────────────────────────────────────┤
│ COSA FARE DOMANI                       │
│ ▸ NVDA  ATTENDI evento · piano pronto  │
│ ▸ ENEL  ACCUMULA 100 € · perché? ›     │
├────────────────────────────────────────┤
│ PORTAFOGLIO   +12,40 € netto stimato   │
│ NVDA 2,1 az · stop 98 € · fino 05/12   │
│   storico piano ›                       │
├────────────────────────────────────────┤
│ CANDIDATI DELLA SETTIMANA (filtro)     │
│ NOTIZIE SPIEGATE                       │
│ CALENDARIO: trimestrali, dividendi     │
│ IL MODELLO: quanto ha indovinato       │
│ IMPARA: glossario                      │
│ [+ Registra un'operazione]             │
└────────────────────────────────────────┘
```

(Esempio di layout con numeri finti, solo per mostrare la struttura.)

- Ogni numero ha un'etichetta **misurato** o **stima**.
- Ogni azione ha un "perché?" che apre la spiegazione semplice.
- Sezione fissa: "Strumento informativo, non consulenza finanziaria. Gli ordini li decidi e li esegui tu."
- Tema chiaro/scuro, leggibile a 360 px.

## 9. Pianificazione e avvisi

- Attività programmata Claude, sessione nuova a ogni esecuzione, fuso `Europe/Rome`: ~06:50 (prima dell'apertura del
  mercato su Trade Republic, 07:30) e ~23:15 (dopo la chiusura USA delle 22:00 e dopo le trimestrali serali).
- Avviso push alla fine dell'esecuzione solo se c'è almeno un'azione nuova (non `TIENI`/`ATTENDI`).
- Se i dati mancano o una fonte fallisce: la pagina lo dice in alto e **non** emette nuovi `COMPRA`.

## 10. Ordine di consegna

| Fase | Contenuto | Scadenza |
|---|---|---|
| A | **Minimo entro lun 16/11:** accesso di rete ai dati, watchlist + posizioni, motore regole e piani, rischio e costi, calendario trimestrali, livello Claude notizie con validatore, pagina, attività programmata 2×/giorno con avviso push. Piano NVIDIA pronto. **Può slittare di qualche giorno:** il modulo "registra operazione" nella pagina (nel frattempo le operazioni si comunicano a Claude in chat e finiscono nello stato). | lun 16/11 |
| B | Filtro settimanale sull'universo, qualità e dividendi, candidati in pagina | fine novembre |
| C | ML: dataset, walk-forward, riferimenti, soglia di utilità, registro previsioni | dicembre |
| D | Dopo ~3 mesi: bilancio misurato (segnali, piani, giudizi di Claude, ML) contro "compra e tieni" un indice | febbraio 2027 |

Ogni fase si chiude con i test verdi e i criteri verificati, come nel progetto cripto.

## 11. Dipendenze nuove (da approvare)

- `yfinance` — prezzi, dividendi, date trimestrali, fondamentali, opzioni (non ufficiale: cache + retry obbligatori).
- `scikit-learn` — modelli e metriche ML (solo Fase C).

Host di rete da autorizzare nell'ambiente cloud: quelli usati da yfinance (`query1.finance.yahoo.com`,
`query2.finance.yahoo.com`, `fc.yahoo.com` e gli eventuali altri che il builder verifica).

## 12. Lasciato al builder

- Struttura interna dei moduli, schema esatto del JSON di revisione, nomi dei documenti nel database.
- Scelta delle soglie iniziali del filtro (in YAML, documentate in `docs/DECISIONI.md`).
- Come ottenere l'elenco dei titoli disponibili su Trade Republic, o il ripiego descritto in §6.1.
- Gestione dei titoli europei (suffissi Yahoo `.MI`, `.DE`, `.PA`…) e conversione in EUR.
- Aggiornare `CLAUDE.md` e `README.md` con una sezione "Dashboard azioni" (comandi e regole).
