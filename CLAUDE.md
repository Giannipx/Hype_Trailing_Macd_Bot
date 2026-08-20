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

## 4. Riepilogo priorità

| # | Problema | Stato | Priorità prima di REAL=y |
|---|----------|-------|---------------------------|
| 1.1 | Crash su candele insufficienti | ✅ Corretto | — |
| 1.2 | Nessun try/except nel loop | ✅ Corretto | — |
| 1.3 | Soglia sell hardcoded in unità coin | ✅ Corretto | — |
| 1.4 | Stop-loss su prezzo disallineato | ✅ Corretto (paper) | Verificare §3.4 in reale |
| 1.5 | Scritture file non atomiche | ✅ Corretto | — |
| 3.1 | Secret key in chiaro / tracciata da git | ❌ Da fare | **Bloccante** |
| 3.2 | Stop-loss reale come limit order | ❌ Da fare (serve tua scelta) | **Bloccante** |
| 3.3 | Istanze ridondanti / design | ❌ Backlog | Non bloccante |
| 3.4 | fill_price sul ramo reale | ❌ Da verificare | **Bloccante** |