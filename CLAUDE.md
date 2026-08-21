# CLAUDE.md — Changelog analisi e fix (Hype_Trailing_Macd_Bot)

Documento di lavoro sulle modifiche apportate al bot in seguito ad analisi del
codice (repo: `Giannipx/Hype_Trailing_Macd_Bot`). Al momento dell'analisi il
bot era in modalità paper trading con file `.txt` di stato contenenti solo
valori placeholder, nessuna chiave reale attiva.

Stato: **paper trading**. Nessuna delle modifiche qui elencate è stata testata
contro l'API reale di Hyperliquid — solo verifica sintattica (`py_compile`).
Prima di passare a `REAL=y` vanno fatti run di paper trading su più cicli e
controllati i log in `cronoMacd.txt`.

---

## 1. Fix applicati (file già modificati nel repo)

### 1.1 — Crash su dati insufficienti dalle candele
**File:** `botMacd.py` (righe 118-161)
**Severità:** Critica

**Bug:** `indicators.macd_series()` ritorna `[], [], []` se le candele ricevute
sono meno di `slow + signal` (35). `indicators.sma()` e `indicators.rsi()`
ritornano `None` se lo storico è più corto del periodo richiesto. Il codice
originale faceva `round(histogram[-1], 5)` e `round(sma2, 2)` senza controllo:
`IndexError` su lista vuota, `TypeError` su `round(None, 2)`. Il bot muore.
Scenario non raro: asset appena listato, gap dell'API, risposta troncata da
Hyperliquid su `candles_snapshot`.

**Fix:** prima di calcolare gli indicatori si verifica `len(closes)` contro un
minimo (`max(sma2_period, rsi_period+1, 35)`). Se insufficiente, si salta il
ciclo (`continue`) con log su `cronoMacdString`, senza crashare. Dopo il
calcolo, controllo aggiuntivo che nessun valore sia `None`/vuoto prima degli
usi successivi.

### 1.2 — Nessuna gestione errori nel loop principale
**File:** `botMacd.py` (righe 118-161), `trailMacd.py` (righe 163-178)
**Severità:** Critica

**Bug:** l'intero `while True` di `CryptoBot.run()` e `StopTrail.run()` non
aveva alcun `try/except` attorno alle chiamate di rete (`get_price`,
`ohlcv_data`, ordini). Un timeout o un errore temporaneo dell'API Hyperliquid
termina il processo. Per un bot pensato per girare 24/7 (come da systemd usato
sull'altro progetto Hyperliquid) è un problema serio, soprattutto se succede
mentre `StopTrail` ha una posizione aperta senza stop attivo.

**Fix:** l'intero corpo del ciclo è avvolto in `try/except Exception`. In caso
di errore: log su console e su `cronoMacdString`, poi si prosegue al ciclo
successivo (`continue` in `botMacd.py`, prosecuzione naturale del `while` in
`trailMacd.py`) invece di terminare il processo.

**Limite noto:** è una rete di sicurezza, non una vera retry logic. Se l'API è
giù per minuti, il bot continua a "girare a vuoto" loggando errori invece di
fermarsi — non implementa backoff né notifica esterna (vedi §3).

### 1.3 — Soglia di vendita hardcoded in unità di coin, non in USD
**File:** `botMacd.py` (riga 209)
**Severità:** Grave (blocca la vendita su asset con size piccole)

**Bug:** `if self.cryptoCoin > 1:` decide se il wallet ha "abbastanza" crypto
per vendere. Funziona per HYPE (wallet iniziale ~20 unità) ma è tarato in
**unità di token**. Su un asset come BTC (`walletIniziale.txt` con
`BTC: 0.003`) questa condizione non è mai vera: 0.003 non supera mai 1, quindi
il ramo SELL non scatterebbe mai qualunque fosse il segnale MACD/RSI. Il bot
resterebbe bloccato con posizione aperta e nessuna via di uscita via logica di
segnale (solo lo stop loss del trail la chiuderebbe, se attivo).

**Fix:** condizione sostituita con `(self.cryptoCoin * self.price) > 10`,
coerente con la soglia già usata per `stableCoin > 10` sul lato BUY. Ora la
verifica è sul controvalore in USD, indipendente dall'asset.

### 1.4 — Prezzo di stop-loss calcolato sul prezzo sbagliato
**File:** `trailMacd.py` (righe 102-136, ramo BUY di `update_stop`)
**Severità:** Grave

**Bug:** dopo l'esecuzione della buy, il codice chiamava `self.wallet()`
(che rifetcha `self.price` dal mercato live) e SOLO DOPO calcolava
`triggerPrice` usando il valore aggiornato di `self.price` — non quello a cui
l'ordine era stato effettivamente eseguito. Su un asset volatile, tra
l'esecuzione dell'ordine e questo refresh possono passare secondi con
movimento di prezzo non trascurabile: lo stop-loss risultava scentrato
rispetto all'entry reale, e i log riportavano un prezzo diverso da quello
realmente pagato.

**Fix:** il prezzo di fill (`fill_price`) viene estratto dal risultato
dell'ordine (`res.get("fill_price", self.price)`) **prima** del refresh del
wallet, e usato per calcolare `triggerPrice`, `sellPrice` e per tutti i log
successivi (inclusa `cronoTradeMacd`).

**Nota:** in modalità reale, `res["fill_price"]` da Hyperliquid Exchange SDK
va verificato — l'attuale `buy()` in `hl.py` non lo estrae esplicitamente dal
risultato di `market_open()` per il ramo reale (lo fa solo per il ramo paper).
Da controllare/completare se si passa a `REAL=y` (vedi §3.4).

### 1.5 — Scritture su file non atomiche
**File:** `hl.py` (`_save_paper`, righe 193-201; `write_ciclostart`,
righe 245-254)
**Severità:** Grave (rischio di corruzione silenziosa dello stato)

**Bug:** sia `paper_wallet.json` che `fileCicloStart.txt` venivano scritti con
un semplice `open(..., "w")` + `write`/`json.dump`, senza atomicità. Un
crash o kill del processo a metà scrittura lascia il file corrotto o
troncato. Conseguenze:
- `_load_paper()` intercetta l'eccezione di parsing JSON e richiama
  silenziosamente `_init_paper()`, **azzerando PnL, fee e storico trade
  simulati senza nessun avviso visibile** a schermo.
- `read_ciclostart()` fa `file.readlines()[cc]`: su file vuoto/troncato
  solleva `IndexError` e crasha il bot (aggravato dal fatto che, prima del fix
  §1.2, questo terminava il processo).

**Fix:** entrambe le funzioni ora scrivono su file temporaneo
(`<file>.tmp`) e fanno `os.replace()` per lo swap atomico solo a scrittura
completata. Stesso pattern già in uso nell'order-flow bot (Streamlit) con le
"atomic state file writes".

---

## 2. Verifica effettuata

- `python3 -m py_compile botMacd.py trailMacd.py hl.py indicators.py jbmainMacd.py`
  → sintassi valida su tutti i file toccati.
- **Non** eseguito alcun test contro l'API Hyperliquid reale o testnet, né
  test unitari (il repo non ne ha). I fix vanno validati con un run di paper
  trading reale prima di fidarsene in produzione.

---

## 3. NON corretto — richiede una tua decisione o non è automatizzabile in sicurezza

### 3.1 — `HL_SECRET_KEY` in chiaro in `config.py`, tracciato da git
**Severità:** Critica, bloccante prima di `REAL=y`

`.gitignore` attuale ignora solo `__pycache__/`, `*.pyc` e (in modo
commentato) `paper_wallet.json`. `config.py` è tracciato e già pushato su un
repository che sembra pubblico. Se un giorno valorizzi `HL_SECRET_KEY` per
operare in reale e fai un commit prima di escluderlo, la chiave privata del
wallet finisce nella cronologia git (anche rimuovendola dopo, resta nella
history a meno di rewrite).

**Cosa implementare (non ho toccato nulla qui, è una scelta di gestione
segreti che riguarda il tuo deploy):**

1. Aggiungere al progetto un file `.env` (escluso da git) con:
   ```
   HL_SECRET_KEY=...
   HL_ACCOUNT_ADDRESS=...
   ```
2. In `config.py`, sostituire i valori hardcoded con lettura da variabili
   d'ambiente, es. con `python-dotenv`:
   ```python
   from dotenv import load_dotenv
   import os

   load_dotenv()

   HL_SECRET_KEY = os.environ.get("HL_SECRET_KEY", "")
   HL_ACCOUNT_ADDRESS = os.environ.get("HL_ACCOUNT_ADDRESS", "")
   ```
3. Aggiungere a `.gitignore`:
   ```
   .env
   ```
4. Aggiungere un `.env.example` (questo sì da committare) con le chiavi vuote,
   a documentazione delle variabili richieste.
5. Se `config.py` con la chiave in chiaro è già stato pushato: la chiave va
   considerata compromessa. Ruotala (nuovo wallet/nuova API key su
   Hyperliquid) — non basta rimuoverla da un commit successivo, resta nella
   history di git a meno di un `git filter-repo`/rewrite della history e
   force-push, e comunque chiunque l'abbia già clonata prima ce l'ha.

Non l'ho implementato io in automatico perché tocca la tua gestione
segreti/deploy (dove giri il bot, come gestisci le env sull'istanza OCI, se
usi già un pattern per gli altri bot Hyperliquid) e voglio che la scelta di
come strutturare `.env` sia coerente con quello che già usi altrove.

### 3.2 — Stop-loss reale piazzato come limit order, non market
**File:** `hl.py`, `crea_ordine_sell_stop()` (righe 329-356)
**Severità:** Grave, solo in modalità reale

`"isMarket": False` con `limit_px` fisso (`sellPrice = triggerPrice -
stopsize/2`). Su un gap di prezzo veloce, se il prezzo salta sotto
`sellPrice` prima che l'ordine si riempia, il limit non esegue e la posizione
resta **senza protezione** nonostante `STOPLOSS=y`.

**Non l'ho corretto perché è una decisione di trade-off che spetta a te:**
- Opzione A — `"isMarket": True`: garantisce l'esecuzione, accetti slippage
  indefinito in cambio di certezza di chiusura.
- Opzione B — mantenere limit ma allargare il buffer (`stopsize/2` →
  dinamico, es. basato su ATR/volatilità recente) per aumentare la probabilità
  di riempimento senza passare a market pieno.

Dimmi quale preferisci e lo implemento.

### 3.3 — Altri punti "medi" identificati ma non toccati (a scopo di
backlog, non bloccanti)

- **Istanze `Hyperliquid` ridondanti**: `CryptoBot` crea 2 istanze
  (`wallet_binance`, `data_binance`), `StopTrail` altre 2 ad ogni trade. Ogni
  istanza apre un client `Info` separato — overhead REST inutile. Inoltre
  `_leverage_set` (cache per evitare di richiamare `update_leverage` ad ogni
  trade) è per-istanza e quindi inefficace, dato che l'istanza viene
  ricreata ad ogni ciclo `StopTrail`.
- **`get_balance_order()` sempre `0.0`** anche in reale (`hl.py:148-151`):
  se attivi `STOPLOSS=y` e l'ordine di stop parzialmente si riempie o resta
  pending, il campo `cryptoCoinOrder` mostrato in log/dashboard non riflette
  mai la size effettivamente bloccata.
- **`STOPSIZE` è un valore assoluto in dollari**, non percentuale né basato
  su ATR/volatilità — non si adatta al regime di mercato.
- **`PERC_COIN=1` fisso** in `hype.txt`: strategia "all-in/all-out", nessun
  profit-taking parziale.
- **Segnali su variazione dell'istogramma MACD** (anticipazione
  dell'incrocio) invece che sull'incrocio confermato: scelta legittima ma più
  esposta a falsi segnali in mercati laterali — da backtestare su HYPE 15m
  prima di reale.

### 3.4 — Da verificare prima di `REAL=y`
Il fix §1.4 assume che `res.get("fill_price", ...)` sia disponibile anche sul
ramo reale di `buy()` in `hl.py`. Attualmente `buy()` ritorna direttamente il
risultato grezzo di `self.exchange.market_open(...)` per il ramo reale (righe
274-278 di `hl.py`), che potrebbe non avere una chiave `"fill_price"` con
quel nome esatto — va controllato contro la risposta reale
dell'SDK `hyperliquid-python-sdk` (o parsata da `result["response"]["data"]
["statuses"]`) e normalizzata, altrimenti il fallback `self.price` resta
quello pre-fix (il bug di §1.4 si ripresenterebbe solo in modalità reale).

---

## 5. Secondo giro di fix — valutazione proposte di un agente esterno (GPT)

Giovanni ha fatto analizzare il progetto (post-fix del §1) a un agente esterno,
che ha prodotto una nuova lista di bug/miglioramenti. Ogni punto è stato
verificato riga per riga sul codice HEAD attuale (non sulle citazioni del
documento esterno, alcune riferite a un commit precedente) prima di essere
applicato o scartato.

### 5.1 — Punti dell'analisi esterna rivelatisi GIÀ RISOLTI (falsi sul codice attuale)

- **"Istanze Hyperliquid condivise non usate"**: falso. `botMacd.py` passa
  `self.wallet_binance, self.data_binance` a `StopTrail` **posizionalmente**
  (non come keyword `wallet_instance=...`), per questo l'agente esterno
  (basato su pattern-matching testuale) non le ha riconosciute, ma
  funzionalmente l'istanza viene già riusata dal fix del §1.
- **"`cryptoCoin > 1` ancora presente"**: falso, già corretto nel §1.3 in
  `(self.cryptoCoin * self.price) > 10`.
- **"Nessuna validazione dati OHLCV insufficienti"**: falso, già corretto nel
  §1.1.

Di conseguenza 3 delle "5 priorità assolute" indicate dall'analisi esterna
erano già chiuse prima di iniziare questo secondo giro.

### 5.2 — Fix applicati in questo giro (confermati veri sul codice attuale)

**Candela ancora in formazione inclusa nel calcolo indicatori**
File: `hl.py`, `ohlcv_data()`. Prima si passava `endTime=now_ms` senza
verificare se l'ultima candela restituita fosse già chiusa: con un
timeframe 15m, a metà intervallo l'ultima candela nell'array è ancora in
formazione, e MACD/RSI/SMA la includevano nel calcolo — comportamento che
cambia ad ogni ciclo e non è riproducibile in un backtest futuro (che vedrà
solo candele chiuse). Fix: nuovo parametro `closed_only=True` (default) che
scarta l'ultima candela se il suo `T` (close time) è ancora nel futuro
rispetto ad `now`, richiedendo una candela in più per compensare.

**Fee sottratta due volte sullo stesso trade**
File: `hl.py`, `usd_to_size()`. Calcolava `sz = (usd/price) * (1-FEE_PCT)`
e poi `buy()` sottraeva di nuovo `fee = sz*price*FEE_PCT` dal cash. Fix:
`usd_to_size()` non applica più la fee; viene applicata una sola volta in
`buy()`/`sell()`.

**Ordine rifiutato/non eseguito trattato come eseguito**
File: `hl.py` (`_normalize_order_result()`, `buy()`, `sell()`),
`trailMacd.py` (rami BUY e SELL). `_normalize_order_result()` ritornava
sempre un dict con `fill_price` valorizzato (sul fallback se non trovava
`avgPx`), e ritornava `None` solo se la risposta non era un dict — un
ordine rifiutato da Hyperliquid arriva comunque come dict, quindi veniva
considerato un fill. Fix: la funzione espone ora esplicitamente `"filled":
True/False` e `"error"`; sia il ramo reale che quello paper di `buy()`/
`sell()` restituiscono questo campo; `trailMacd.py` controlla `filled`
prima di chiudere il trail, scrivere `fileCicloStart.txt` o loggare il
trade — se l'ordine non è eseguito, il trail resta attivo e riprova al
prossimo intervallo.

**`priceMin` (fileCicloStart.txt) disallineato dalla media ponderata reale**
File: `hl.py` (nuova funzione `get_entry_price()`), `botMacd.py`,
`trailMacd.py`. Il gate "vendo solo sopra il prezzo d'acquisto" leggeva
`fileCicloStart.txt`, valorizzato con l'ultimo singolo prezzo di buy
(`priceMin = self.price`), mentre il wallet paper calcola correttamente una
media ponderata (`entry_px`) quando si aggiunge a una posizione esistente.
Dopo più buy consecutive le due grandezze potevano divergere (es. 10@70 poi
10@60: entry reale 65, ma fileCicloStart.txt restava 60). Fix:
`get_entry_price()` è ora la fonte unica di verità — paper legge `entry_px`
dal wallet simulato, reale legge `entryPx` della posizione da Hyperliquid.
`fileCicloStart.txt` resta scritto solo per compatibilità con la dashboard
(non eliminato, come da indicazione di non rompere compatibilità), ma non è
più letto per le decisioni di trading.

**SELL non usava il prezzo di fill reale**
File: `trailMacd.py`, ramo SELL. Usava `self.price` (mid corrente) per
log/pnl invece del prezzo di fill effettivo restituito dall'ordine — stessa
correzione già fatta sul ramo BUY nel giro precedente, mancava sul SELL.
Fix: `fill_price = res.get("fill_price", self.price)` anche qui, usato per
log e `cronoTradeMacd`.

**Arrotondamento dei valori usati nelle decisioni**
File: `botMacd.py`. MACD/signal/histogram/RSI venivano arrotondati (5
decimali gli indicatori, 2 il RSI) **prima** di essere usati nei confronti
decisionali (`last_macd > last_signal`, `last_histogram > previous_histogram`,
soglie RSI 40/60). Su variazioni submillesimali dell'istogramma questo può
nascondere un vero aumento/diminuzione. Fix: i valori interni restano a
piena precisione; l'arrotondamento si applica solo nei print/log
(`:.5f`/`:.2f` espliciti nelle f-string).

**Timestamp non uniformi in UTC**
File: `botMacd.py`, `trailMacd.py`, `hl.py`. `datetime.now()`/
`datetime.datetime.now()` erano naive (ora locale del sistema), mentre le
candele Hyperliquid sono in epoch ms UTC. Fix: tutti i timestamp di ciclo e
di log (`cronoMacdString`, `cronoTradeMacd`, i due `now` nel loop di
`botMacd.py`) usano ora `datetime.now(timezone.utc)`. La funzione
`timeCET()` in `hl.py` non è stata toccata: è intenzionalmente per il
display in Europe/Rome.

### 5.3 — Punti dell'analisi esterna valutati VALIDI ma NON applicati (bassa priorità o richiedono una decisione)

- Reconcile della posizione dopo l'ordine reale (verifica su Hyperliquid
  invece di assumere l'esito) — rilevante solo in modalità reale/testnet,
  non per il paper trading attuale.
- Rinominare `get_balance("USDC")` in `get_account_equity()` — naming, zero
  impatto funzionale, già documentato via commento nel codice.
- Gestione esplicita di `STOP TRIGGERED` lato reale — Fase D (testnet),
  non ora.
- `bot_state.json` per la dashboard (stato vero del trail invece di
  reinferirlo) — miglioramento di osservabilità, Fase B.
- Arricchire il wallet paper con `equity`, `unrealized_pnl_usd`,
  `peak_equity`, `max_drawdown_usd` — Fase B.
- Modellare spread/slippage nel paper trading — Fase B/C, il trailing
  stretto ($0.20 su HYPE ~$70, ~0.28%) rende questo rilevante ma non
  bloccante subito.
- Verificare il MACD contro una libreria standard prima del backtest —
  Fase C per definizione.

### 5.4 — Verifica effettuata

`python3 -m py_compile botMacd.py trailMacd.py hl.py indicators.py
jbmainMacd.py` e controllo `ast.parse` su tutti i file toccati: sintassi
valida. Verificato a mano che non restino riferimenti alla vecchia variabile
locale `priceMin` (ora tutto `self.priceMin`, popolato da
`get_entry_price()`). **Non testato contro l'API Hyperliquid reale**: prima
di un nuovo run va rifatto un paper trading di verifica, in particolare per
osservare i log `BUY NON ESEGUITO`/`SELL NON ESEGUITO` (nuovi) e confermare
che `priceMin` nei log corrisponda a `entry_px` del wallet paper.

## 6. Riepilogo priorità (aggiornato dopo il secondo giro)

| # | Problema | Stato | Priorità prima di REAL=y |
|---|----------|-------|---------------------------|
| 1.1 | Crash su candele insufficienti | ✅ Corretto | — |
| 1.2 | Nessun try/except nel loop | ✅ Corretto | — |
| 1.3 | Soglia sell hardcoded in unità coin | ✅ Corretto | — |
| 1.4 | Stop-loss su prezzo disallineato (BUY) | ✅ Corretto | — |
| 1.5 | Scritture file non atomiche | ✅ Corretto | — |
| 5.2 | Candela ancora in formazione nel calcolo indicatori | ✅ Corretto | — |
| 5.2 | Fee sottratta due volte | ✅ Corretto | — |
| 5.2 | Ordine rifiutato trattato come eseguito | ✅ Corretto | Verificare in reale/testnet |
| 5.2 | `priceMin` disallineato da `entry_px` | ✅ Corretto | — |
| 5.2 | SELL senza fill_price reale | ✅ Corretto | — |
| 5.2 | Arrotondamento nelle decisioni MACD/RSI | ✅ Corretto | — |
| 5.2 | Timestamp non uniformi UTC | ✅ Corretto | — |
| 3.1 | Secret key in chiaro / tracciata da git | ⚠️ Verificare stato attuale (`.env.example` già presente in repo) | **Bloccante se non ancora fatto** |
| 3.2 | Stop-loss reale come limit order | ✅ Risolto (isMarket=True, verificato in `hl.py` riga 374) | — |
| 3.3 | Istanze ridondanti / design | ✅ Risolto (shared instance già in uso, §5.1) | — |
| 3.4 | fill_price sul ramo reale | ✅ Risolto (`_normalize_order_result` con `filled`/`avgPx`, §5.2) | Verificare in reale/testnet |

**Nota sul punto 3.1**: verificato in questo giro, incluso uno scan di
`git log --all -- config.py` su tutti i commit passati. Il repo attuale ha
`.env.example`, `config.py` legge le chiavi da variabili d'ambiente
(`os.environ.get("HL_SECRET_KEY", "")`), `.gitignore` contiene `.env`, e
**nessun commit precedente di `config.py` contiene una chiave privata in
chiaro**. Il punto è quindi chiuso senza riserve.

**Cosa resta aperto, non bloccante per il paper trading**: reconcile
posizione dopo ordine reale, naming `get_balance`, `bot_state.json` per la
dashboard, arricchimento wallet paper (equity/drawdown), modello
spread/slippage nel paper, verifica MACD contro libreria standard prima del
backtest — vedi §5.3 per il dettaglio.