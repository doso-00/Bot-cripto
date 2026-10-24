# Risultati della Fase 3 — 2026-10-24

Fonte: `reports/fase3_confronto_20261024_172013.html`, generato sul PC di Riccardo (commit `8845979`) con i dati
reali verificati in Fase 1. Tutti i numeri sono **misurati** sulla variante di valutazione (risk manager senza stop
per drawdown, decisione A), al netto di commissioni (0,1%) e slippage (0,05%). Il periodo di test (2025–oggi)
**non è stato letto**.

## Esito

**Nessuna delle 6 strategie supera i quattro criteri della Fase 3 sulla validazione (2024).** Per la regola di
promozione (docs/DECISIONI.md) nessuna va al test finale e nessuna passa alla Fase 4. I criteri non sono stati
abbassati.

## Validazione 2024, BTC/USDT 1h (buy & hold: +119,6%, max drawdown 32,3%)

| Strategia | Parametri scelti sul training | Rendimento | Max drawdown | Operazioni | Criteri 1-4 |
|---|---|---|---|---|---|
| sma_cross | fast=10, slow=200 | +53,2% | 21,8% | 43 | ✗ ✓ ✓ ✓ |
| donchian_breakout | n_entry=200, n_exit=100 | +59,3% | 30,5% | 15 | ✗ ✗ ✓ ✗ |
| rsi_mean_reversion | period=14, low=20, high=70, max_hold=72 | +3,4% | 16,0% | 11 | ✗ ✗ ✓ ✗ |
| sma_cross_trend_filter | fast=30, slow=200, sma_period=4800 | +44,0% | 22,5% | 35 | ✗ ✓ ✓ ✓ |
| donchian_breakout_trend_filter | n_entry=100, n_exit=100, sma_period=4800 | +76,4% | 27,7% | 27 | ✗ ✗ ✓ ✗ |
| rsi_mean_reversion_trend_filter | period=14, low=20, high=60, max_hold=72, sma_period=4800 | +2,8% | 8,7% | 5 | ✗ ✗ ✓ ✗ |

Criteri: 1 batte il buy & hold (o rendimento entro 5 punti con drawdown −30%), 2 almeno 30 operazioni, 3 positiva
con slippage 0,1%, 4 coerente su ETH/USDT.

## Cosa dicono i numeri

1. **Nessuna strategia si avvicina al buy & hold nel 2024.** La migliore (donchian_breakout_trend_filter, +76%)
   resta 43 punti sotto. Il criterio 1 fallisce per tutte.
2. **Le strategie di trend riducono il drawdown ma rinunciano a molto rendimento.** Sul training (2020–2023) il
   max drawdown scende dal 77% del buy & hold al 35–49%, ma il rendimento resta sotto (es. sma_cross_trend_filter
   +299% contro +487%). Nel 2024, anno quasi tutto rialzista o laterale, il ritardo nell'entrare costa più di
   quanto il filtro protegga.
3. **Il walk-forward conferma la debolezza, con un'eccezione.** Fuori campione (2021–2023, 12 finestre) cinque
   strategie su sei rendono meno del buy & hold sulle stesse finestre (+37,6%). Solo
   donchian_breakout_trend_filter fa meglio: +68,7%. È un indizio, non una prova: la stessa strategia nel 2024
   non soddisfa i criteri 1, 2 e 4.
4. **La mean reversion con RSI non ha vantaggio misurabile.** Sharpe negativo su tutta la griglia del training,
   anche con il filtro di trend.
5. **Casi al limite, registrati senza arrotondare.** donchian_breakout su ETH riduce il drawdown del 29,98%
   contro il 30% richiesto; donchian_breakout_trend_filter ha 27 operazioni contro le 30 richieste. Entrambi
   falliscono comunque il criterio 1.
6. **La robustezza tra timeframe è debole.** Gli stessi parametri in candele significano orizzonti diversi: sul
   4h, sma_period=4800 vuol dire 800 giorni e la strategia quasi non opera (2 operazioni sul training). I
   risultati sul 4h non vanno letti come conferma né come smentita.
7. **La regola di drawdown massimo con riavvio dopo 7 giorni** (in paper sarebbe uno stop definitivo) scatta da 1
   a 3 volte nel solo 2024. Su sma_cross dimezza il rendimento (+21% invece di +53%).

## Ipotesi e limiti (non misurati)

- Un solo anno di validazione, prevalentemente rialzista (57% dei giorni), favorisce il buy & hold.
- La griglia è piccola (132 combinazioni) per scelta: griglie più grandi aumentano il rischio di overfitting.
- Il limite del 25% per acquisto ritarda le entrate di 3 candele: penalizza le strategie veloci.
- Filtri dell'exchange attuali applicati al passato; slippage costante.

## Cosa NON fare adesso

- Abbassare i criteri o riscegliere i parametri guardando la validazione.
- Lanciare `bot.evaluate` "per vedere": il test resta l'unico periodo mai visto, ed è il solo giudizio pulito
  su qualsiasi ipotesi futura.
- Considerare le osservazioni di questo documento come scoperte: ogni nuova idea nata guardando questi
  risultati è un'ipotesi, da definire per iscritto prima di provarla.
