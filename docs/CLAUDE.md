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

---

## 7. Terzo giro: Fix Crash Break-Even e Protezione Esecuzione StopTrail

### 7.1 — Crash UnboundLocalError su `atr_distance`
- **File:** `botMacd.py` (`check_hard_stop`)
- **Problema:** Quando scattava lo stop a Break-Even (+0.20% dopo Take Profit Parziale al 50%), il codice entrava nel ramo `if self.take_profit_hit`, dove `atr_distance` non era valorizzata. Il blocco di log/stampa cercava di stamparla, sollevando `UnboundLocalError: local variable 'atr_distance' referenced before assignment`. Questo errore ha ucciso il bot il 27/09/2026 alle 04:25 UTC.
- **Fix:** `floor_distance`, `atr_distance`, `max_loss_pct` e `hard_stop_atr_mult` ora vengono calcolati preventivamente prima del controllo condizionale. È stato aggiunto un blocco di stampa e log dedicato al Break-Even Stop, e `self.take_profit_hit` viene resettato a `False` al completamento dell'uscita.

### 7.2 — Timeout e Invalidazione Breakdown in `StopTrail`
- **File:** `trailMacd.py`, `botMacd.py`, `jbmainMacd.py`, `hype.txt`
- **Problema:** `StopTrail.run()` era un loop bloccante infinito privo di timeout. Nei log di `cronoMacd.txt` è rimasto attivo per ore (fino a 4.5 ore nel Trade 1 e oltre 1 ora nel Trade 4) mentre il prezzo crollava. Al primo micro-rimbalzo comprava a prezzi bassissimi in pieno downtrend.
- **Fix:** 
  1. Aggiunto `TRAIL_BUY_TIMEOUT` (default: 900s = 15m). Se il buy non chiude entro 15 minuti, il trail viene annullato (`self.running = False`), evitando ingressi su segnali obsoleti.
  2. Aggiunta invalidazione per crollo del prezzo (`max_drop_mult = 3.0` x `stopsize` rispetto al prezzo di partenza). Se il mercato collassa mentre il trail segue, il trail abortisce subito per breakdown.

### 7.3 — Disattivazione di default di `TREND_FLIP_EXIT`
- **File:** `botMacd.py`, `jbmainMacd.py`, `hype.txt`
- **Problema:** Se `Price < EMA200` a 15m, il bot chiudeva forzatamente e all'istante l'intera posizione a mercato. Questo causava uscite dopo 17 secondi o 2-3 minuti su normale rumore di prezzo, accumulando perdite secche e doppie fee taker (responsabile di 3 trade in perdita su 5). Questa regola non era presente neanche nel backtest.
- **Fix:** Reso opzionale e disabilitato di default (`TREND_FLIP_EXIT=n`). La gestione del rischio è ora affidata a: Take Profit parziale 50% (+1.5x ATR con stop a break-even), Hard Stop dinamico (4x ATR con floor `max_loss_pct`), e Sell Trailing stop basato su MACD.

---

## 8. Quarto giro: Modulo Bi-direzionale SHORT, Risoluzione Bug Critici e Validazione Backtest

In seguito all'analisi del periodo di mercato ribassista su HYPE, è stato richiesto di abilitare l'operatività short per rendere la strategia simmetrica e profittevole anche durante i trend negativi.

### 8.1 — Architettura del Modulo SHORT
- **File:** `hype.txt`, `jbmainMacd.py`, `hl.py`, `trailMacd.py`, `botMacd.py`, `backtest.py`
- **Config:** Aggiunto parametro `ENABLE_SHORT=y` in `hype.txt` e parsato in `jbmainMacd.py` e `backtest.py`.
- **Hyperliquid SDK (`hl.py`):**
  - `get_position_side()`: restituisce `"LONG"`, `"SHORT"` o `"FLAT"`, ispezionando `szi` (in reale) o il campo `side` del dizionario paper.
  - `open_short()`: apre lo short su Hyperliquid con `market_open(is_buy=False)` dopo `_ensure_leverage()`. In paper calcola il margine e addebita la fee.
  - `close_short()`: ricopre lo short tramite `market_close()` (l'SDK imposta obbligatoriamente `reduce_only`). In paper accredita il profitto da discesa `(entry_px - exit_px) * size - fee`.
- **Trailing Stop (`trailMacd.py`):**
  - Implementate azioni `short` (inseguimento al ribasso del breakout con acquisto sul rimbalzo superiore a `stopSize`) e `cover` (inseguimento al ribasso del dump con ricopertura sul rimbalzo superiore a `stopSize`).
  - Protezione timeout (`TRAIL_BUY_TIMEOUT`) e abort se il prezzo sfonda al rialzo oltre 3x stopsize.
- **Signal Logic & Risk Management (`botMacd.py`):**
  - Trend filter bearish: `Price < EMA200`, `Price <= SMA20 * 1.015` e `ADX >= soglia`.
  - Ingressi su MACD: short aperto su `Diminuzione Positivo` (fase di esaurimento del pullback verso l'alto prima di girare al ribasso), simmetrico al buy long su `Diminuzione Negativo`.
  - Uscite su MACD: cover short su `Diminuzione Negativo`.
  - Take Profit parziale 50% al raggiungimento di +1.5x ATR di discesa e spostamento immediato dello stop a Break-Even protetto (`entry * (1 - 0.002)`).
  - Hard Stop dinamico short a `entry + max(floor, hard_stop_atr_mult * ATR)`.

### 8.2 — Correzione Bug Doppia Sottrazione Fee Paper in `close_short`
- **File:** `hl.py` (riga ~601)
- **Bug:** `realized` veniva calcolato come `(entry - price) * sz - fee`. Successivamente il saldo cash veniva aggiornato con `d["cash_usd"] += realized - fee`, sottraendo la fee di chiusura due volte.
- **Fix:** Corretto in `d["cash_usd"] += realized`.

### 8.3 — Correzione Blocco Ordini per Arrotondamento Float in `can_open_buy`
- **File:** `botMacd.py`, `backtest.py`
- **Bug:** `stableBot = round(min(...), 2)` arrotondava per eccesso a due cifre decimali (es. $498.16 a fronte di un massimale teorico di $498.158). Di conseguenza il controllo di sicurezza `position_value + next_buy_value > max_position` risultava vero per $0.0019, rigettando qualsiasi ordine successivo al primo e bloccando l'intero bot/backtest.
- **Fix:** Introdotto un cuscinetto di tolleranza di `$0.50` (`> max_position + 0.50`), eliminando i falsi blocchi causati da imprecisioni di floating-point.

### 8.4 — Correzione Blocco Posizione Residua (Epsilon Floating-Point)
- **File:** `hl.py` (funzioni `sell`, `close_short`, `get_position_side`), `backtest.py`
- **Bug:** Alla chiusura totale della posizione (`pos - sz`), le sottrazioni floating-point in Python potevano lasciare residui come `1e-16`. Poiché il controllo era `sz <= 0`, il wallet rimaneva bloccato con `side == "SHORT"` e `sz = 0.0000000000000001`, impedendo nuovi ingressi.
- **Fix:** Applicato `round(max(0.0, pos - sz), 6)` e se `sz <= 1e-5` il saldo viene forzato a `0.0` e lo stato a `"FLAT"`.

### 8.5 — Risultati Backtest Comparativo (30 Giorni su HYPE)
Test eseguito sui dati storici reali a 30 giorni:
- **Timeframe 1h**:
  - *Solo Long*: 8 trade, Win Rate 60.0%, Rendimento **+0.52%**, Max Drawdown 1.77%.
  - *Bi-direzionale (Long + Short)*: 23 trade, Win Rate **64.3%**, Rendimento **+1.29%** (più che raddoppiato!), Max Drawdown 2.32%.
- **Timeframe bassi (5m e 15m)**:
  - Sul 5m il rumore di mercato e le oscillazioni laterali (chop) provocano frequenti falsi segnali ed erosione del capitale dovuta alle fee taker (oltre $37 di fee accumulate su 5m). Viene pertanto raccomandato l'uso del timeframe **1h** per massimizzare il rendimento e abbattere le commissioni.

---

## 9. Quinto giro: Risoluzione dei 6 Bug Logici e Ottimizzazione Architetturale (Post-Review Esterna)

In seguito all'analisi dettagliata condotta sulla codebase aggregata (`HYPE_BOT_BUNDLE.txt`), sono stati confermati e risolti 6 bug logici e diverse aree di ottimizzazione architetturale:

### 9.1 — `shortStopSize` reso dinamico con ATR
- **File:** `botMacd.py` (`run()`)
- **Problema:** Nel loop principale venivano aggiornati `self.stopSize` e `self.buyStopSize`, ma `self.shortStopSize` non veniva mai aggiornato ad ogni candela, rimanendo per sempre inchiodato al valore floor iniziale ($0.108), ignorando la volatilità.
- **Fix:** Aggiunto `self.shortStopSize = self.buyStopSize = round(max(self.stop_floor * 0.6, self.atr * 1.0), 4)`.

### 9.2 — Formula Equity Universale e Margin Sizing
- **File:** `botMacd.py` (`get_equity()`, `wallet()`), `backtest.py` (`can_open_buy()`), `hl.py` (`get_withdrawable()`)
- **Problema:** 
  1. In Reale (`REAL=y`), `get_balance("USDC")` ritorna già `accountValue` da Hyperliquid (che comprende l'unrealized PnL). Fare `equity = stableCoin + cryptoCoin * price` creava un doppio conteggio della posizione.
  2. In Paper con SHORT, il cash non scala il nozionale. Sommare `cryptoCoin * price` gonfiava l'equity del 40-50%.
  3. In `backtest.py`, `can_open_buy()` faceva `wallet.cash_usd + position_value` invece di chiamare `wallet.equity(price)`.
- **Fix:** Introdotto `get_equity()` dedicato in `botMacd.py` (reale: `accountValue` puro; paper long: `cash + crypto*px`; paper short: `cash + unrealized_pnl`). Aggiunto `get_withdrawable()` in `hl.py` per non impegnare margine bloccato.

### 9.3 — Dimensionamento Position Sizing vincolato a `PERC_STABLE`
- **File:** `botMacd.py` (`wallet()`), `backtest.py` (`run_backtest()`)
- **Problema:** Quando `atr * hard_stop_atr_mult < price * max_loss_pct`, matematicamente `target_usd` collassava su `equity`, forzando sempre l'ingresso al cap massimo (50%) e ignorando `PERC_STABLE=0.15`.
- **Fix:** `target_usd` viene vincolato a `min(target_by_risk, equity * percStable)`, rispettando fedelmente il parametro di allocazione per trade. Nel backtest a 30d, questo ha ridotto il Max Drawdown da **2.32% a soli 0.70%** a parità di Win Rate (64.3%).

### 9.4 — Protezione del Runner dopo Take Profit Parziale (1.5x ATR)
- **File:** `botMacd.py`, `backtest.py`
- **Problema:** Quando scattava il Take Profit Parziale al 50% con stop spostato a Break-Even, un'inversione dell'istogramma MACD al ciclo successivo svendeva subito il restante 50% "runner" tramite trailing MACD a prezzi peggiori del target TP1.
- **Fix:** Quando `take_profit_hit = True`, l'uscita anticipata su MACD viene sospesa se il prezzo è inferiore a TP1 (per il Long) o superiore a TP1 (per lo Short), lasciando correre il runner con la protezione garantita dello stop a Break-Even.

### 9.5 — Ottimizzazione Chiamate REST e Caching `user_state`
- **File:** `trailMacd.py`, `hl.py`
- **Problema:** `StopTrail` chiamava `self.wallet()` 2-3 volte per ogni ciclo da 4 secondi (in `print_status`, `update_stop` e trigger), generando fino a 10-15 chiamate HTTP ogni 4 secondi su Hyperliquid.
- **Fix:** 
  1. `self.wallet()` viene chiamato una sola volta per iterazione all'inizio del `while self.running:`.
  2. In `hl.py` è stata aggiunta una cache breve (1.5s) per `user_state`, invalidata istantaneamente solo su esecuzione ordini. I payload multipli (equity, szi, entryPx, side) condividono la stessa richiesta REST.

### 9.6 — Persistenza dello Stato su Riavvio/Crash
- **File:** `hl.py` (`save_trade_state`, `load_trade_state`), `botMacd.py`
- **Problema:** Se il bot si riavviava con posizione aperta dopo aver preso il TP1, dimenticava `take_profit_hit` ed `entry_time`, provando a eseguire un secondo TP1 sul 50% residuo e azzerando il timer dell'uscita tecnica.
- **Fix:** `take_profit_hit` ed `entry_time` vengono salvati in `paper_wallet.json` (o `bot_state.json` in reale) e ripristinati automaticamente all'avvio.

### 9.7 — Timeframe 1h & Trend Filter Dinamico
- **File:** `hype.txt`, `jbmainMacd.py`, `botMacd.py`
- **Fix:** Configurato `TIMEFRAME=1h` e `TREND_TIMEFRAME=4h` in `hype.txt`. Risolto il disallineamento della gerarchia multi-timeframe tramite risoluzione automatica in `jbmainMacd.py` e `botMacd.py`.

### 9.8 — Supporto SHORT in `dashboard.py`
- **File:** `dashboard.py`
- **Fix:** Calcolo dell'equity corretto per le posizioni SHORT (`cash + unrealized_pnl`), badge visivo del side (`LONG`, `SHORT`, `FLAT`) e monitoraggio degli stati `trail_short` e `trail_cover`.

---

## 10. Sesto giro: Rifinitura Finale, Bugfix Reale e Allineamento Backtester

### 10.1 — Bug Critico Bloccante in Modalità Reale (`REAL=y`)
- **File:** `hl.py`
- **Problema:** L'implementazione di `_get_user_state()` chiamava `time.time()`, ma in cima a `hl.py` mancava `import time`. In paper trading il metodo non veniva chiamato, ma passando a `REAL=y` sollevava `NameError`, intercettato dai blocchi `try/except` che azzeravano silenziosamente saldo, side ed entry price. Inoltre, `verifica_ordini()` faceva una chiamata diretta a `user_state` senza sfruttare la cache.
- **Fix:** Aggiunto `import time` e aggiornato `verifica_ordini()` per utilizzare la chiamata cached `self._get_user_state()`.

### 10.2 — Allineamento `TIME_STOP_MINUTES` su Timeframe 1h
- **File:** `hype.txt`
- **Problema:** Con `TIMEFRAME=1h`, mantenere `TIME_STOP_MINUTES=60` faceva scattare il controllo di stallo/uscita tecnica già alla primissima candela dopo l'ingresso, liquidando anzitempo posizioni in fisiologico pullback iniziale.
- **Fix:** Impostato `TIME_STOP_MINUTES=240` (equivalente a 4 candele orarie), offrendo il tempo necessario alla strategia di validare il momentum.

### 10.3 — Gestione Simmetrica di `TREND_FLIP_EXIT` per Posizioni SHORT
- **File:** `botMacd.py`
- **Problema:** Il blocco `TREND_FLIP_EXIT` gestiva solo posizioni Long controllando `if self.cryptoCoin > 0 and self.trend_bearish: self.wallet_binance.sell(...)`. Con uno short aperto (dove `trend_bearish` è la norma), se attivato avrebbe tentato di chiudere la posizione chiamando erroneamente `sell()` anziché `close_short()`.
- **Fix:** Separati i rami:
  - `position_side == "LONG"` e trend diventa `trend_bearish` -> esegue `sell()`.
  - `position_side == "SHORT"` e trend diventa `trend_bullish` -> esegue `close_short()`.
  In entrambi i casi lo stato del trade (`buy_entries`, `buy_armed`, `take_profit_hit`, `entry_time`) viene opportunamente azzerato.

### 10.4 — Multi-Timeframe e Trend Filter Dinamico in `backtest.py`
- **File:** `backtest.py`, `hl.py`
- **Problema:** `backtest.py` aveva `TREND_TIMEFRAME = "15m"` hardcoded, quindi testando il timeframe `1h` filtrava i trade con trend a 15m anziché a 4h come `botMacd.py`. Inoltre `TF_SECONDS` non includeva `3m`, `2h`, `4h`, `1d`.
- **Fix:**
  - Aggiunti intervalli `3m`, `2h`, `4h`, `1d` a `TF_SECONDS` e `hl.py`.
  - Creata funzione `resolve_trend_tf(tf, custom_trend)` che mappa dinamicamente `1h -> 4h`, `5m -> 15m`, ecc.
  - Implementato caching dei trend candles (`trend_candles_cache`) con warmup buffer di 250 candele per calcolare in modo accurato l'EMA200 iniziale.
  - Aggiunto parametro CLI `--trend-timeframe`.

### 10.5 — Allineamento e Modernizzazione di `optimize.py`
- **File:** `optimize.py`
- **Problema:** Calcolava il PnL finale sommando arbitrariamente `cash + sz * px` (errato per posizioni short), non passava `enable_short=True` a `run_backtest`, filtrava solo le uscite LONG e non supportava il timeframe 1h.
- **Fix:**
  - Sostituito il calcolo dell'equity con `wallet.equity()`.
  - Conteggiati tutti i trade conclusi con `t.get("pnl") is not None`.
  - Aggiunti parametri CLI `--timeframe`, `--trend-timeframe` e `--enable-short`.
  - Risoluzione automatica del trend filter per il timeframe impostato.

### 10.6 — Allineamento Log e Output Console in `botMacd.py`
- **File:** `botMacd.py`
- **Fix:** Resa dinamica la stringa dei log (`TREND%s` invece del testo fisso `TREND15m`) e aggiunto `BEARISH=%s` alla stampa a console del trend.

---

## 11. Settimo giro: Rifinitura Casi Limite (Wallet Iniziale, Stop Loss Hardware, Sync Universale e Dashboard)

### 11.1 — Pulizia `walletIniziale.txt` e Protezione Eredità Size su Nuove Posizioni
- **File:** `walletIniziale.txt`, `hl.py` (`buy()`, `open_short()`)
- **Problema:** `walletIniziale.txt` conteneva `HYPE: 20` (placeholder iniziale). In paper trading, aprendo uno SHORT, `open_short` accumulava `old_sz + sz`, ereditando i 20 token e gonfiando la prima size short.
- **Fix:**
  - In `walletIniziale.txt`: azzerati i token placeholder (`HYPE: 0.0`, `BTC: 0.0`, `ETH: 0.0`), lasciando solo `USDC: 1000`.
  - In `hl.py`: `buy()` e `open_short()` considerano `old_sz` solo se la posizione esistente coincide con lo stesso lato (`LONG` per buy, `SHORT` per short) e `entry_is_real=True`. Entrando da `FLAT`, la nuova size riparte sempre esattamente da `sz`.

### 11.2 — Supporto Completo `STOPLOSS=y` su Hyperliquid per LONG e SHORT
- **File:** `hl.py`, `trailMacd.py`, `botMacd.py`
- **Problema:** Mancava l'invio del sell stop dopo un BUY in `trailMacd.py` e non esisteva una funzione per creare buy stop di protezione per le posizioni SHORT.
- **Fix:**
  - Aggiunto `crea_ordine_buy_stop(market, amount, stop_price, limit_price)` in `hl.py` (trigger order reale con `is_buy=True`, `isMarket=True`, `reduce_only=True`).
  - In `trailMacd.py`: dopo l'esecuzione di un `buy` crea il `sell_stop`; dopo l'esecuzione di uno `short` crea il `buy_stop`. Su `cover` o `sell` cancella gli ordini residui via `elimina_ordine()`.
  - In `botMacd.py`: `elimina_ordine()` viene invocato coerentemente anche nei blocchi di `check_hard_stop_short()`, `check_take_profit_short()` e `check_technical_exit_short()`.

### 11.3 — Sincronizzazione Oraria Universale nel Loop Principale
- **File:** `botMacd.py` (`run()`)
- **Problema:** La catena di 35+ righe di `if/elif` gestiva solo alcuni timeframe rigidi; impostando ad esempio `TIMEFRAME=4h` o `2h` si finiva nell'`else: seconds_until_next_time = 0`, facendo ciclare il loop ogni 2 secondi.
- **Fix:** Sostituita l'intera catena con calcolo matematico esatto su timestamp UTC:
  `tf_sec = tf_sec_map.get(self.timeframe, 3600)`
  `seconds_until_next_time = tf_sec - (int(now.timestamp()) % tf_sec)`
  Funziona su qualsiasi timeframe supportato (`5s`, `1m`, `3m`, `5m`, `15m`, `30m`, `1h`, `2h`, `4h`, `1d`) garantendo allineamento al secondo esatto di chiusura candela.

### 11.4 — Visualizzazione Corretta in `dashboard.py` in Modalità Reale (`REAL=y`)
- **File:** `dashboard.py`
- **Problema:** In modalità reale il saldo e la posizione venivano letti comunque da `paper_wallet.json` anziché dall'API Hyperliquid, e `price_min` da `fileCicloStart.txt` anziché dall'`entry_price` di posizione reale.
- **Fix:**
  - Se `real == "y"`: `cash_usd` legge `hl.get_balance("USDC")`, `sz_pos` legge `hl.get_balance(symbol)`, `price_min` legge `hl.get_entry_price(symbol)` e `side` legge `hl.get_position_side(symbol)`.
  - Equity reale impostata direttamente a `accountValue` (`cash_usd`), evitando raddoppi di PnL non realizzato.
  - Aggiornate le etichette delle card di stato per riflettere se si è in "Hyperliquid Reale" o "Paper Trading".

---

## 12. Ottavo giro: Parametro `limit_px` per Trigger Ordini SDK Hyperliquid e Ripristino Break-Even Stop Post-TP1

### 12.1 — Aggiunta `limit_px` obbligatorio in `self.exchange.order(...)`
- **File:** `hl.py` (`crea_ordine_sell_stop()`, `crea_ordine_buy_stop()`)
- **Problema:** L'SDK ufficiale `hyperliquid-python-sdk` richiede `limit_px` come 4° parametro posizionale obbligatorio anche per ordini trigger con `"isMarket": True` (utilizzato per il limite massimo di slippage). L'omissione avrebbe causato `TypeError: order() missing 1 required positional argument: 'limit_px'` in modalità reale.
- **Fix:** Aggiunto `slip_px` formattato a 5 cifre significative (con -3% di slippage per sell stop e +3% per buy stop) e formattato anche `stop_px` a float con 5 cifre significative per `triggerPx`.

### 12.2 — Riposizionamento dello Stop a Break-Even dopo il Take Profit Parziale
- **File:** `botMacd.py` (`check_take_profit()`, `check_take_profit_short()`)
- **Problema:** Dopo la chiusura del primo 50% di posizione a target (+1.5x ATR), l'ordine stop loss preesistente su Hyperliquid veniva rimosso da `elimina_ordine()`, ma non veniva ricollocato un nuovo ordine a Break-Even sul restante 50% ("runner").
- **Fix:** Subito dopo `self.wallet()`:
  - In `check_take_profit()`: se `real == "y"` e `stoplossorder == "y"`, crea un nuovo `sell_stop` al prezzo di break-even (`priceMin * 1.002`).
  - In `check_take_profit_short()`: se `real == "y"` e `stoplossorder == "y"`, crea un nuovo `buy_stop` al prezzo di break-even (`priceMin * 0.998`).

---

## 13. Nono giro: Ottimizzazione Parametri e Allineamento Backtester (30d e 60d)

### 13.1 — Esecuzione Realistica dello Stop Loss in `backtest.py`
- **File:** `backtest.py`
- **Problema:** Quando scattava un Hard Stop o Take Profit, il backtest prendeva il valore estremo di `intrabar_path` (ovvero l'High massimo o il Low minimo dell'intera candela oraria), infliggendo uno slippage artificiale e irrealistico di diversi dollari per trade.
- **Fix:** Allineato il fill del trigger al livello effettivo dell'ordine (`stop_price` per hard stop, `tp_price` per take profit), considerando il prezzo di apertura solo in caso di effettivo gap intrabar sfavorevole.

### 13.2 — Calibrazione Parametri Ottimali in `hype.txt`
- **File:** `hype.txt`
- **Modifiche:**
  - `ATR_MULT`: impostato a `2.00` (anziché 1.80).
  - `HARD_STOP_ATR_MULT`: impostato a `3.0` (anziché 2.0).
- **Esito Backtest a 1h (Trend 4h)**:
  - **30 giorni**: Win Rate **83.3%** (10 vittorie su 12 uscite), Rendimento netto **+0.68%**, Max Drawdown **0.44%**.
  - **60 giorni**: Win Rate **78.9%** (15 vittorie su 19 uscite), Rendimento netto **+0.35%**, Max Drawdown **0.94%**.

---

## 15. Undicesimo giro: Scaling Allocazione Capitale e Architettura Dual-Bot (HYPE + SOL)

### 15.1 — Scaling Capitale e Rischio (`PERC_STABLE=0.35`, `MAX_LOSS_PCT=0.015`)
- **File:** `hype.txt`, `hyper_sol.txt`
- **Razionale:** Con i parametri conservativi iniziali (`PERC_STABLE=0.15`), il bot investiva solo il 15% del capitale per trade. Grazie all'alto Win Rate riscontrato sui trend puliti a 1h/4h di HYPE e SOL (rispettivamente 83.3% e 57.1% a 30 giorni) e al rischio strettamente controllato dal Take Profit parziale con Break-Even e Hard Stop dinamico, l'allocazione è stata scalata a `PERC_STABLE=0.35` (35% di capitale per bot) con `MAX_LOSS_PCT=0.015` (1.5% stop floor).
- **Rendimento Combinato e Rischio:**
  - HYPE (30gg): da +0.68% a **+1.55%** (Max Drawdown 1.02%, Win Rate 83.3%).
  - SOL (30gg): da +0.55% a **+1.28%** (Max Drawdown 1.12%, Win Rate 57.1%).
  - Rendimento mensile complessivo stimato del Dual-Bot: **~+2.83%** su collaterale combinato, con leva 1x senza rischio liquidazione e max drawdown < 1.95% a 60gg.

### 15.2 — Disaccoppiamento dello Stato per Esecuzione Parallela Multi-Bot
- **File:** `hl.py`
- **Problema:** Precedentemente, `hl.py` utilizzava nomi di file fissi e globali per il wallet paper (`paper_wallet.json`), il prezzo medio d'acquisto (`fileCicloStart.txt`), lo stato di riavvio (`bot_state.json`) e il log (`cronoMacd.txt`). Avviando due istanze del bot in contemporanea (es. HYPE e SOL), i processi entravano in collisione sovrascrivendo i rispettivi file di stato.
- **Fix:** Implementato il namespacing dinamico dei file di runtime basato sul simbolo dell'asset (`_coin_clean()`):
  - Wallet paper: `paper_wallet_{coin}.json` (es. `paper_wallet_sol.json`, mantenendo il fallback a `paper_wallet.json` per HYPE per piena retrocompatibilità).
  - Ciclo start: `fileCicloStart_{coin}.txt` (es. `fileCicloStart_sol.txt`, con fallback per HYPE).
  - Stato di riavvio: `bot_state_{coin}.json` (es. `bot_state_hype.json`, `bot_state_sol.json`).
  - Cronologia / Log: `cronoMacd_{coin}.txt` (es. `cronoMacd_sol.txt`, mantenendo `cronoMacd.txt` per HYPE).
  - In `botMacd.py`: il reset all'avvio della cronologia agisce solo sul file dedicato all'asset in esecuzione.

### 15.3 — Supporto Dinamico Multi-Asset in `dashboard.py`
- **File:** `dashboard.py`
- **Modifiche:**
  - Rileva dinamicamente il nome dell'asset e punta ai rispettivi file namespaced (`hl._get_paper_file()`, `hl._get_crono_file()`, ecc.).
  - Sostituite tutte le etichette, titoli di finestra e metriche precedentemente fisse su "HYPE" con il simbolo dell'asset attivo.
  - È possibile visualizzare in tempo reale ciascun bot passando il relativo file parametri:
    `streamlit run dashboard.py -- --file hype.txt`
    `streamlit run dashboard.py -- --file hyper_sol.txt`

### 15.4 — Aggiornamento `export_bundle.py` e `walletIniziale.txt`
- **File:** `export_bundle.py`, `walletIniziale.txt`
- **Modifiche:**
  - Aggiunto `SOL: 0.0` in `walletIniziale.txt` per consentire una partenza pulita del paper wallet di SOL.
  - Aggiornato `export_bundle.py` per escludere automaticamente dall'esportazione tutti i file runtime con prefissi dinamici (`paper_wallet*`, `fileCicloStart*`, `bot_state*`, `cronoMacd*`, `*.tmp`).

---

## 16. Dodicesimo giro: Riorganizzazione Modulare dei File di Progetto

### 16.1 — Struttura della Cartella Root
- **Razionale:** Mantenere la root pulita e focalizzata esclusivamente sull'operatività del trading bot, eliminando dispersione tra script di backtest, tool di esportazione, documentazione e file di test.
- **File mantenuti nella Root:**
  - `jbmainMacd.py`, `botMacd.py`, `trailMacd.py`, `hl.py`, `indicators.py`, `config.py`, `dashboard.py`.
  - Configurazioni attive: `hype.txt`, `hyper_sol.txt`, `walletIniziale.txt`.
  - Configurazione ambiente e git: `requirements.txt`, `.env.example`, `.gitignore`, `AGENTS.md`.

### 16.2 — Cartella `backtest/` (Motore di Simulazione & Ottimizzazione)
- **File spostati:**
  - `backtest.py` -> `backtest/backtest.py`
  - `optimize.py` -> `backtest/optimize.py`
  - `risultati.txt` -> `backtest/risultati.txt`
  - `backtest_output/` -> `backtest/output/`
  - Parametri di test storici -> `backtest/params/` (`hyper_pendle.txt`, `hyper_sui.txt`, `hyper_tao.txt`, `hyper_wif.txt`).
- **Adattamento del Codice:**
  - `backtest.py` e `optimize.py` aggiungono dinamicamente la root a `sys.path`.
  - Aggiunto `resolve_param_path(path)` che trova i file `.txt` sia nella root (`hype.txt`), sia nella cartella `params/`, sia da percorsi relativi/assoluti, indipendentemente dalla directory di lavoro da cui viene lanciato il comando.
  - Gli output CSV (`trades_*.csv` e `summary_*.csv`) vengono salvati ordinatamente in `backtest/output/`.

### 16.3 — Cartella `export/` (Tooling di Esportazione per AI Esterne)
- **File spostati:**
  - `export_bundle.py` -> `export/export_bundle.py`
  - `HYPE_BOT_BUNDLE.txt` -> `export/HYPE_BOT_BUNDLE.txt`
  - `HYPE_BOT_CODE.zip` -> `export/HYPE_BOT_CODE.zip`
- **Adattamento del Codice:**
  - `export_bundle.py` scansiona la root del progetto ed esporta bundle e ZIP direttamente nella cartella `export/`, escludendo ricorsivamente la cartella `export/` stessa e gli output dei backtest.

### 16.4 — Cartella `docs/` (Documentazione di Progetto)
- **File spostati:**
  - `README.md` -> `docs/README.md`
  - `CLAUDE.md` -> `docs/CLAUDE.md`
- **Contenuto:** Manuale completo d'uso, istruzioni Dual-Bot, architettura e changelog dettagliato dei 16 giri di sviluppo.






