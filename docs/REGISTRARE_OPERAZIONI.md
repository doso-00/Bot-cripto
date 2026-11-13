# Registrare un acquisto o una vendita (dashboard azioni)

Trade Republic non ha API: Riccardo compra e vende a mano sull'app e poi lo scrive a Claude in una chat. Questa
pagina è la procedura che la sessione Claude segue per registrare l'operazione nello stato della dashboard, senza
perdere nulla di ciò che c'è già. Il codice non si collega mai al broker: registra solo operazioni già fatte.

## Cosa scrive Riccardo nella chat

- **titolo** (come in pagina, es. `NVDA`, `ENEL.MI`);
- **acquisto o vendita**;
- **quantità** (anche frazioni di azione, es. `1,3`);
- **prezzo per azione in euro**, quello pagato (o incassato) su Trade Republic: l'app mostra e addebita in euro
  anche i titoli USA, quindi la valuta è quasi sempre `EUR`;
- **data e ora** dell'operazione (se manca si usa il momento in cui la registri);
- la commissione solo se diversa da 1 €.

## Che tipo di posizione è (`--kind`)

Si sceglie dal segnale che la pagina mostrava quando Riccardo ha comprato:

| Segnale in pagina | `--kind` |
|---|---|
| `COMPRA` con tipo **base** | `core` |
| `COMPRA` con tipo **occasione** | `opportunity` |
| posizione legata a una trimestrale (tipo **evento**, al massimo 100 €) | `event` |

Una **vendita** usa lo stesso tipo dell'acquisto (lo mostra `uv run python -m stocks.trade list`). Se non è chiaro,
chiedilo a Riccardo: il tipo decide le regole di uscita (base: soglie −15%/−20% dal prezzo medio; occasione ed evento:
stop, obiettivo e data limite) e non si può cambiare dopo.

## Procedura per la sessione Claude

Ogni blocco Bash parte con `cd /home/user/Bot-cripto &&` (la cartella non si conserva tra un comando e l'altro).
`<ARTIFACT_URL>` è l'indirizzo della dashboard, lo stesso usato dalle attività programmate. Non toccare né codice né
configurazione: si lavora solo in `out/`.

1. **Leggi lo stato** con `ArtifactData` azione `get`: `url` = `<ARTIFACT_URL>`, `collection` = `state`,
   `doc_id` = `current`, `out_dir` = `/home/user/Bot-cripto/out/db`. Annota la **versione del documento** dalla riga
   del risultato (`version N`; non è il campo `version` dello stato, che vale 1). Se la lettura fallisce, fermati:
   senza lo stato attuale rischieresti di cancellare le operazioni già registrate.
2. **Copia lo stato** in `out/state.json` (stesso script della sezione 1 di `stocks/PROMPT_REFRESH.md`):

   ```bash
   cd /home/user/Bot-cripto && uv run python - out/db/state/current.json <<'EOF'
   import json, sys
   from stocks.state import State
   raw = json.load(open(sys.argv[1], encoding="utf-8"))
   wrapped = isinstance(raw, dict) and isinstance(raw.get("data"), dict) and "trades" not in raw
   fields = raw["data"] if wrapped else raw
   State.model_validate(fields)  # errore = stato non valido: fermati e segnalalo
   json.dump(fields, open("out/state.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
   print("operazioni:", len(fields.get("trades", [])), "- piani:", len(fields.get("plans", {})))
   EOF
   ```

3. **Registra l'operazione** (esempio: acquisto di 1,3 NVDA a 140,50 € come occasione):

   ```bash
   cd /home/user/Bot-cripto && uv run python -m stocks.trade add --ticker NVDA --side buy --qty 1.3 \
     --price 140.50 --currency EUR --kind opportunity --date 2026-11-18T15:40
   ```

   Il comando controlla quantità e prezzo maggiori di zero, la commissione (default 1 €), il tipo, la valuta e che
   una vendita non superi la quantità posseduta; una data senza fuso è in ora italiana. Se rifiuta l'operazione
   (exit 2, motivo stampato), **non** salvare nulla: riporta il motivo a Riccardo e chiedi il dato corretto.
   Controlla il riepilogo stampato (operazione e posizioni aperte) e confermalo a Riccardo.
4. **Salva lo stato** con `ArtifactData` azione `set`: `url` = `<ARTIFACT_URL>`, `collection` = `state`,
   `doc_id` = `current`, `file_path` = `/home/user/Bot-cripto/out/state.json`, `if_version` = la versione annotata
   al passo 1. Se lo strumento segnala un conflitto di versione (un aggiornamento programmato ha scritto nel
   frattempo), ripeti dal passo 1: non forzare mai la scrittura.
5. **Aggiorna la pagina**: avvia a mano l'attività programmata **"Dashboard azioni — sera"** oppure aspetta il
   prossimo aggiornamento (06:52 o 23:17). Al primo aggiornamento dopo un acquisto il codice crea il piano di uscita
   ricalcolato sul prezzo di carico: per un'occasione o un evento la pagina dice a quale livello inserire (o
   correggere) l'ordine stop-loss su Trade Republic.

## Da sapere

- Il prezzo medio usa solo i prezzi di acquisto; le commissioni entrano nella liquidità e nell'utile netto.
- Una posizione in euro su un titolo in dollari è il caso normale: soglie e utile si confrontano in euro, i livelli del
  piano sono nella valuta del titolo con l'equivalente in euro (stima al cambio del giorno).
- Per vedere cosa c'è nello stato locale: `uv run python -m stocks.trade list`.
