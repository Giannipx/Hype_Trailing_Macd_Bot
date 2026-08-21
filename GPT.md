Il progetto è migliorato sensibilmente: hai già introdotto alcune correzioni importanti, tra cui scrittura atomica del wallet, normalizzazione del fill reale, gestione degli errori nel trailing e supporto al riuso delle istanze Hyperliquid. �
GitHub +1
C'è però un punto importante: alcune delle correzioni che hai inserito sono preparate nel codice ma non sono ancora effettivamente utilizzate dal bot. In particolare il riuso delle istanze Hyperliquid.
La mia valutazione attuale è quindi: buona base, ma prima del backtesting farei ancora una fase di "stabilizzazione paper".
1. Cosa hai già corretto bene
Rispetto all'analisi precedente, vedo queste migliorie:
✅ Scrittura atomica del wallet
Hai corretto:
json.dump(...)
usando file temporaneo + os.replace(). Questo evita che un crash durante la scrittura lasci paper_wallet.json troncato. �
GitHub
Ottimo.
✅ Scrittura atomica di fileCicloStart.txt
Anche questa è stata sistemata. �
GitHub
✅ Estrazione del vero avgPx
Hai aggiunto _normalize_order_result() per cercare:
response
 └── data
      └── statuses
           └── filled
                └── avgPx
Questa è una buona correzione per il ramo REAL. �
GitHub
✅ Stop loss calcolato sul fill
In trailMacd.py hai corretto il problema per cui il prezzo veniva aggiornato prima di calcolare lo stop.
Ora usi:
fill_price = res.get("fill_price", self.price)
e successivamente:
triggerPrice = round(
    fill_price - (self.stopsize * self.multiSize),
    2
)
Questo è decisamente meglio. �
GitHub
✅ Gestione eccezioni del trailing
Hai aggiunto:
try:
    ...
except Exception as e:
nel loop del trailing. Questo impedisce che un problema momentaneo API faccia terminare il processo. �
GitHub
2. BUG ancora presente: le istanze condivise NON vengono utilizzate
Questo è il primo che correggerei.
Hai modificato StopTrail per accettare:
wallet_instance=None,
data_instance=None
e hai scritto correttamente:
if wallet_instance is not None and data_instance is not None:
    self.wallet_binance = wallet_instance
    self.data_binance = data_instance
�
GitHub
Però botMacd.py non passa queste istanze.
Hai ancora:
task = StopTrail(
    self.market,
    "sell",
    self.stopSize,
    self.interval,
    self.multiSize,
    self.percStable,
    self.percCoin,
    self.real,
    self.stoplossorder
)
e analogamente per BUY. �
GitHub
Quindi la nuova funzionalità non viene utilizzata.
Da cambiare
task = StopTrail(
    self.market,
    "sell",
    self.stopSize,
    self.interval,
    self.multiSize,
    self.percStable,
    self.percCoin,
    self.real,
    self.stoplossorder,
    wallet_instance=self.wallet_binance,
    data_instance=self.data_binance
)
e idem BUY.
Questo elimina realmente le quattro istanze Hyperliquid per ogni operazione.
3. BUG importante: stai ancora usando la candela aperta
Questo è ancora presente.
In hl.py:
endTime=now_ms
e poi in botMacd.py:
closes = [c[4] for c in ohlcv]

macd_line, signal_line, histogram = indicators.macd_series(closes)
�
GitHub +1
Quindi l'ultima candela può essere ancora in formazione.
Per un timeframe:
15m
alle 18:07 stai potenzialmente utilizzando:
18:00 → 18:15
che non è ancora chiusa.
Questo è il problema che considero più importante prima del backtesting, perché altrimenti il comportamento live e quello storico potrebbero non essere equivalenti.
Soluzione
ohlcv_data() dovrebbe restituire esplicitamente solo candele chiuse oppure il bot dovrebbe eliminare l'ultima:
ohlcv = self.data_binance.ohlcv_data(...)

if len(ohlcv) < 2:
    continue

ohlcv = ohlcv[:-1]
Io preferisco fare la cosa in hl.py, introducendo:
closed_only=True
per evitare che qualcuno dimentichi questa regola nel futuro.
4. BUG importante: usd_to_size() continua a togliere la fee due volte
Questo problema è ancora presente.
In hl.py:
sz = (usd / price) * (1 - config.FEE_PCT)
�
GitHub
poi nel BUY paper:
fee = sz * price * config.FEE_PCT

d["cash_usd"] -= sz * price + fee
�
GitHub
Quindi:
budget
 ↓
usd_to_size()
 ↓
budget - fee
 ↓
BUY
 ↓
viene applicata nuovamente la fee
È da correggere.
Io farei così
usd_to_size():
def usd_to_size(self, coin, usd, price):
    if price <= 0 or usd <= 0:
        return 0.0

    sz = usd / price
    return self.round_size(coin, sz)
e poi il BUY calcola una sola fee.
Questo rende molto più chiaro il modello:
USD destinati all'operazione
        ↓
size = USD / prezzo
        ↓
notional
        ↓
fee
5. BUG: se l'ordine reale viene rifiutato, il bot può comunque considerarlo eseguito
Questo è molto importante.
Hai:
norm = self._normalize_order_result(...)
return norm
ma _normalize_order_result() restituisce comunque un dizionario. �
GitHub
Poi trailMacd.py fa:
fill_price = res.get("fill_price", self.price)
e continua.
Quindi bisogna distinguere chiaramente:
FILLED
RESTING
REJECTED
ERROR
Per questo bot, visto che usi market orders, il comportamento corretto dovrebbe essere:
FILLED → continua
ERROR → non modificare stato
REJECTED → non modificare stato
Questo è particolarmente importante per:
write_ciclostart(...)
Perché non dobbiamo mai scrivere un nuovo prezzo di ingresso se il BUY non è stato eseguito.
6. BUG logico: fileCicloStart.txt non rappresenta realmente il prezzo medio
Questo è probabilmente il secondo problema strategico più importante.
Nel BUY fai:
priceMin = self.price
e poi:
write_ciclostart(priceMin)
�
GitHub
Ma il wallet invece calcola correttamente una media ponderata:
d["entry_px"] = (
    (entry * old_sz) + (price * sz)
) / (old_sz + sz)
�
GitHub
Esempio:
BUY 1:
10 HYPE @ 70

BUY 2:
10 HYPE @ 60
Prezzo medio:
65
ma fileCicloStart.txt potrebbe contenere:
60
Quindi:
wallet entry = 65
cycle start = 60
Hai due definizioni diverse di prezzo d'ingresso.
Soluzione
Il gate deve essere basato su:
paper_wallet["entry_px"]
e nel REAL sul vero entryPx della posizione Hyperliquid.
Idealmente eliminerei completamente fileCicloStart.txt.
Però non lo farei ancora in questa fase se vuoi mantenere compatibilità con il bot attuale: prima possiamo sostituirlo con una funzione centralizzata get_entry_price().
7. BUG: cryptoCoin > 1
Hai ancora:
if self.cryptoCoin > 1:
�
GitHub
Questo non è corretto.
Per esempio:
posizione = 0.8 HYPE
potrebbe essere perfettamente valida.
La condizione dovrebbe essere:
if self.cryptoCoin > 0:
oppure, ancora meglio:
if self.cryptoCoin >= min_order_size:
dove min_order_size viene determinato in base all'asset.
8. Problema: stai arrotondando l'istogramma prima di confrontarlo
Attualmente:
self.last_histogram = round(histogram[-1], 5)
poi:
diff = round(
    self.last_histogram - self.previous_histogram,
    2
)
�
GitHub
Questo può generare falsi:
hist1 = 0.00104
hist2 = 0.00105
La differenza reale è:
0.00001
ma con:
round(..., 2)
diventa:
0.0
Il valore usato per la decisione dovrebbe essere quello pieno.
Regola
valore interno → precisione completa
valore visualizzato → arrotondato
Quindi:
self.last_histogram = histogram[-1]
e solo nel print:
round(self.last_histogram, 5)
9. Problema: controllo insufficiente dei dati indicatori
Se per un problema API ricevi:
ohlcv = []
oppure poche candele, fai:
macd_line[-1]
e puoi ottenere:
IndexError
Inoltre:
rsi_values
può essere None quando i dati non sono sufficienti. indicators.py lo prevede esplicitamente. �
GitHub
Metterei una validazione unica:
if len(closes) < required_bars:
    log(...)
    continue
10. MACD deve essere verificato prima del backtesting
Il tuo indicators.py usa:
ema = closes[0]
come seed. �
GitHub
Non dico che sia necessariamente sbagliato, ma prima del backtest dobbiamo stabilire esattamente quale MACD vogliamo replicare.
Altrimenti rischiamo:
TradingView
      ≠
Bot
      ≠
Backtester
e poi non sappiamo perché i segnali sono differenti.
Io farei un test di confronto contro una libreria standard prima di iniziare il backtest.
11. Problema importante: il paper trading usa ancora un prezzo ideale
Il paper BUY fa:
price
e SELL:
price
�
GitHub
Il prezzo arriva da:
all_mids()
quindi è il mid price.
Per il backtesting va bene avere un modello semplificato, ma prima del backtest io aggiungerei almeno:
bid
ask
spread
slippage
Perché la tua strategia ha un trailing molto stretto.
Se:
HYPE = $70
STOPSIZE = $0.20
la distanza è circa:
0,286%
Uno spread/slippage anche modesto può incidere parecchio.
12. Problema reale: SELL non usa necessariamente il fill per aggiornare lo stato
Hai corretto molto bene il BUY usando fill_price.
Nel SELL invece continui a usare:
self.price
per:
sell(...)
e per il log.
Il metodo sell() reale restituisce però il fill reale normalizzato. �
GitHub
Quindi dovremmo fare:
fill_price = res["fill_price"]
e usare quello per:
PnL
log
trade history
statistiche
13. Problema reale: controllo della posizione dopo l'ordine
Dopo:
market_close()
non dovremmo semplicemente assumere:
posizione = 0
Dovremmo verificare Hyperliquid.
Questo diventa fondamentale quando avremo:
partial fill
o errori API.
La sequenza ideale è:
ORDER
 ↓
response
 ↓
fill
 ↓
reconcile account
 ↓
aggiorna stato locale
14. get_balance("USDC") nel REAL è in realtà equity
Questo commento è corretto e già presente:
marginSummary.accountValue
�
GitHub
ma il nome:
get_balance("USDC")
è fuorviante.
Stai restituendo:
accountValue
non necessariamente:
USDC disponibile
Per il bot attuale questo può diventare importante quando entreranno in gioco:
leva
margine
PnL non realizzato
posizioni aperte
Per il futuro lo rinominerei:
get_account_equity()
e aggiungerei:
get_available_margin()
15. La leva per ora è 1x: bene
Hai:
LEVERAGE = 1.0
ISOLATED = "n"
�
GitHub
Per questa fase lo lascerei assolutamente così.
Non introdurrei la leva nel backtesting finché il motore base non è stabile.
16. Il sistema di stop reale va mantenuto separato dal paper
Hai fatto una scelta corretta:
PAPER
→ StopTrail locale

REAL
→ trailing locale
→ eventuale Sell Stop Hyperliquid
e crea_ordine_sell_stop() usa un trigger isMarket=True e reduce_only=True. �
GitHub
Questa parte la manterrei.
Però dobbiamo ancora aggiungere la gestione:
STOP TRIGGERED
        ↓
posizione = 0
        ↓
cancella eventuali ordini residui
        ↓
aggiorna stato bot
17. Un problema architetturale che correggerei ora
Attualmente:
botMacd
   │
   ├── Hyperliquid
   │
   ├── Hyperliquid
   │
   └── StopTrail
          │
          ├── Hyperliquid
          └── Hyperliquid
anche se StopTrail ora supporta il riuso.
Dobbiamo arrivare a:
CryptoBot
   │
   ├── hl_client
   │
   └── StopTrail
          │
          └── stesso hl_client
Una sola istanza.
18. Modifica che farei a botMacd.py
Le due chiamate:
task = StopTrail(...)
devono diventare:
task = StopTrail(
    self.market,
    "sell",
    self.stopSize,
    self.interval,
    self.multiSize,
    self.percStable,
    self.percCoin,
    self.real,
    self.stoplossorder,
    wallet_instance=self.wallet_binance,
    data_instance=self.data_binance
)
e:
task = StopTrail(
    self.market,
    "buy",
    self.stopSize,
    self.interval,
    self.multiSize,
    self.percStable,
    self.percCoin,
    self.real,
    self.stoplossorder,
    wallet_instance=self.wallet_binance,
    data_instance=self.data_binance
)
Questa è una modifica immediata.
19. Modifica che farei a indicators.py
Qui farei una piccola ma importante modifica: mai arrotondare i dati interni.
Il file può rimanere quasi identico.
In particolare:
def macd_series(closes, fast=12, slow=26, signal=9):
    if len(closes) < slow + signal:
        return [], [], []

    fast_ema = _ema_series(closes, fast)
    slow_ema = _ema_series(closes, slow)

    macd = [
        f - s
        for f, s in zip(fast_ema, slow_ema)
    ]

    signal_line = _ema_series(macd, signal)

    hist = [
        m - s
        for m, s in zip(macd, signal_line)
    ]

    return macd, signal_line, hist
La funzione attuale è sostanzialmente già così. �
GitHub
Il cambiamento principale va fatto nel consumer, non qui.
20. Modifica fondamentale in botMacd.py
Da:
self.last_histogram = round(histogram[-1], 5)
a:
self.last_histogram = histogram[-1]
e:
self.previous_histogram = self.last_histogram
senza arrotondamento.
Per il display:
print(
    f"HISTOGRAM: {self.last_histogram:.5f}"
)
21. Modifica fondamentale in hl.py
Cambierei:
def usd_to_size(self, coin, usd, price):
    if price <= 0:
        return 0.0

    sz = usd / price
    return self.round_size(coin, sz)
Quindi eliminiamo:
* (1 - config.FEE_PCT)
La fee viene calcolata una sola volta dentro buy().
22. Introduciamo una funzione comune per l'esito dell'ordine
In hl.py metterei qualcosa come:
def _is_filled_result(self, result):
    if not isinstance(result, dict):
        return False

    if result.get("paper"):
        return True

    status = str(result.get("status", "")).lower()

    return status in (
        "ok",
        "filled",
        "success",
    )
Ma qui voglio essere ancora più preciso prima di usarla nel progetto: controllerei esattamente tutte le forme di risposta della versione corrente dell'SDK Hyperliquid che hai installato.
23. Modifica al paper wallet che consiglio
Attualmente hai:
{
  "cash_usd": 1000,
  "sz": 0,
  "entry_px": 0,
  "realized_pnl_usd": 0,
  "fees_usd": 0,
  "n_trades": 0
}
Io lo porterei gradualmente verso:
{
  "cash_usd": 1000.0,
  "sz": 0.0,
  "entry_px": 0.0,
  "realized_pnl_usd": 0.0,
  "unrealized_pnl_usd": 0.0,
  "fees_usd": 0.0,
  "n_trades": 0,
  "equity": 1000.0,
  "peak_equity": 1000.0,
  "max_drawdown_usd": 0.0
}
Non serve ancora un database.
24. Ma NON farei ancora SQLite
Avevo proposto SQLite nella precedente analisi.
Ora, visto come hai evoluto il progetto, cambierei leggermente approccio.
Prima farei:
paper_wallet.json
+
trade_history.json
+
equity_history.csv
e solo successivamente, quando iniziamo il backtesting:
SQLite
In questo modo non mischiamo:
live paper engine
con:
historical backtester
25. Dashboard
La dashboard è stata migliorata ma continua a ricostruire lo stato trail:
trail_sell = price is not None and price > price_min and crypto_coin > 0
trail_buy = stable_bot > 10
�
GitHub
Questo significa che la dashboard non conosce il vero stato del trailing.
Per esempio:
MACD SELL
↓
StopTrail partito
↓
trail = 70.80
La dashboard non vede:
70.80
ma inferisce semplicemente:
trail_sell = True
Per ora va bene, ma nella prossima fase farei un file:
bot_state.json
con:
{
  "state": "TRAIL_SELL",
  "trail_stop": 70.80,
  "signal_price": 71.00,
  "updated_at": "..."
}
Questo sarebbe molto utile anche per il debugging.
26. Altro miglioramento: timestamp UTC
Hai:
datetime.datetime.now()
in botMacd.py e diversi:
datetime.now()
nel wrapper. �
GitHub +1
Dato che Hyperliquid lavora in UTC, userei ovunque:
datetime.now(timezone.utc)
e solo la dashboard converte eventualmente in:
Europe/Rome
Questo ci aiuterà tantissimo nel backtesting e nel confronto con i dati OHLCV.
27. Ordine delle modifiche che consiglio
Non farei tutte le modifiche insieme.
FASE A — stabilizzazione attuale
Da fare adesso:
✅ Candela chiusa
✅ Shared Hyperliquid instance realmente utilizzata
✅ Correzione fee doppia
✅ Eliminare cryptoCoin > 1
✅ Eliminare arrotondamento histogram prima della decisione
✅ Validazione dati OHLCV
✅ Gestione corretta FILLED/REJECTED
✅ Usare fill_price anche nel SELL
✅ Correggere priceMin vs entry_px
✅ UTC uniforme
FASE B — paper engine
Poi:
bot_state.json
trade history
equity history
spread
slippage
unrealized PnL
drawdown
win rate
FASE C — backtesting
Solo dopo:
download storico HYPE
stesso indicators.py
stesso algoritmo MACD
stesso trailing
stesso fee model
stesso slippage model
confronto paper/backtest
FASE D — testnet
Infine:
Hyperliquid testnet
ordini reali testnet
riconciliazione posizione
stop reali
recovery dopo riavvio
E solo dopo penseremo al mainnet.
28. I file che modificherei
Per la FASE A non toccherei ancora tutto il progetto.
File
Modifica
botMacd.py
🔴 importante
hl.py
🔴 importante
trailMacd.py
🔴 importante
indicators.py
🟠 piccola
config.py
🟡 piccola
dashboard.py
🟢 dopo
jbmainMacd.py
🟢 quasi invariato
La struttura attuale dei file è coerente con quanto documentato anche nel tuo AGENTS.md. �
GitHub
Una cosa che NON cambierei
La logica fondamentale della strategia:
MACD > Signal
Histogram diminuisce
RSI > 40
       ↓
SELL

MACD < Signal
Histogram aumenta
RSI < 60
       ↓
BUY
la lascerei esattamente com'è per ora. È importante non modificare contemporaneamente strategia + execution engine + dati, altrimenti nel successivo backtest non sapremmo cosa ha prodotto un eventuale miglioramento o peggioramento.
Priorità assoluta
Se dovessi scegliere solo 5 correzioni da fare adesso, sono queste:
1. CANDLE CHIUSA
2. FEE UNA SOLA VOLTA
3. FILLED / REJECTED CORRETTAMENTE
4. ENTRY PRICE REALE / MEDIA PESATA
5. SHARED HYPERLIQUID INSTANCE
Queste sono più importanti di dashboard, SQLite, ATR, nuovi indicatori o ottimizzazioni.
Nota importante
Ho anche verificato il repository rispetto a progetti Hyperliquid analoghi: l'approccio di separare signal engine, execution e trailing/risk management è una direzione architetturale sensata; è anche una separazione adottata da altri bot Hyperliquid più strutturati. �
GitHub +1
Per il prossimo passaggio io preparerei direttamente i 4 file completi botMacd.py, hl.py, trailMacd.py e indicators.py, già corretti per la FASE A, senza modificare ancora la strategia MACD. In questo modo puoi sostituirli, avviare il paper bot e verificare insieme i log prima di passare al backtesting.