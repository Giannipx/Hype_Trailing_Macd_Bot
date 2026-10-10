# HYPE Trailing MACD Bot

Bot di trading in Python per **perpetual futures su Hyperliquid**, basato su segnali **MACD/RSI** con **trailing stop** per l'esecuzione dei trade. Adattato da un bot spot Binance.

## Caratteristiche

- Segnali MACD + RSI per decidere compra/vendi, con doppio controllo sul trend dell'istogramma.
- Trailing stop (`StopTrail`): dopo un segnale, il bot segue il prezzo a intervalli regolari e aggiorna lo stop loss solo quando il prezzo si muove a favore; il trade parte quando lo stop viene toccato.
- **Paper trading** di default: prezzi reali da mainnet, wallet simulato in `paper_wallet.json` (ripristinabile a ogni avvio dai valori di `walletIniziale.txt`). Nessuna chiave richiesta.
- **Trading reale** su mainnet (perpetual HYPE) con `market_open` / `market_close`.
- Indicatori in puro Python (niente pandas/ta).

## Requisiti

- Python 3.9+
- Dipendenze in `requirements.txt`:

```
pip install -r requirements.txt
```

## Configurazione

I parametri si passano con un file testo `KEY=VALUE` (nessuno spazio attorno a `=`):

```
SYMBOL=HYPE
STOPSIZE=0.20
INTERVAL=4
TIMEFRAME=15m
MSIZE=3
PERC_STABLE=0.10
PERC_COIN=1
REAL=n
STOPLOSS=n
MAX_LOSS_PCT=0.01
```

| Parametro | Significato |
|-----------|-------------|
| `SYMBOL` | Coin del perpetual (es. `HYPE`) |
| `STOPSIZE` | Ampiezza dello stop (in dollari, fa da pavimento minimo per ATR) |
| `INTERVAL` | Secondi tra un check del trailing stop e l'altro |
| `TIMEFRAME` | `1m`, `5m`, `15m`, `30m`, `1h`, ... (`5s` non supportato, mappato su `1m`) |
| `MSIZE` | Moltiplicatore di `STOPSIZE` per il trigger del sell stop loss |
| `PERC_STABLE` | Quota della parte stable usata ad ogni buy (es. `0.15`) |
| `PERC_COIN` | Quota della posizione coin venduta ad ogni sell (es. `1`) |
| `REAL` | `n` = paper trading (default) \| `y` = ordini reali |
| `STOPLOSS` | `y`/`n`: attiva il sell stop loss (trigger HL) dopo ogni buy |
| `ATR_PERIOD` | Periodo per il calcolo dell'ATR dinamico (default `14`) |
| `ATR_MULT` | Moltiplicatore ATR per il trailing stop (es. `2.50`) |
| `HARD_STOP_ATR_MULT` | Moltiplicatore ATR per l'hard stop loss (default `4.0`) |
| `MAX_LOSS_PCT` | Pavimento hard stop dal prezzo medio, come frazione: `0.005` = 0.5% |
| `ADX_THRESHOLD` | Soglia minima ADX per confermare il trend bullish a 15m (default `20`) |
| `TRAIL_BUY_TIMEOUT` | Timeout in secondi per invalidare un trailing buy (default `900` = 15m) |
| `TREND_FLIP_EXIT` | `y`/`n`: uscita d'emergenza se trend 15m gira bearish (default `n`, evita uscite premature) |

Le altre impostazioni (leva, margine, rete, fee) sono in `config.py`:

- `REAL = "n"` per default; con `REAL = "y"` servono `HL_ACCOUNT_ADDRESS` e `HL_SECRET_KEY`.
- `NETWORK = "mainnet"` / `"testnet"` — anche in paper i prezzi sono live da mainnet.
- `LEVERAGE` e `ISOLATED` ("y" = isolato, altrimenti cross) usati solo in reale.
- `START_BALANCE_USD` e `FEE_PCT` per il wallet simulato.

## Chiavi per il trading reale (.env)

In modalità paper (`REAL=n`) non serve nulla. Per il reale (`REAL=y`) le chiavi
vanno messe in un file `.env` locale (escluso da git), mai in `config.py`:

```
cd <cartella del bot>
copy .env.example .env     # poi editalo con un editor
```

```
HL_ACCOUNT_ADDRESS=0x...
HL_SECRET_KEY=...
```

`config.py` le legge all'avvio con `python-dotenv` (`load_dotenv()`). Se un
giorno una chiave finisse per errore in un commit, considerala compromessa:
resta nella storia di git anche dopo la rimozione.

## Avvio

```
python jbmainMacd.py --file hype.txt
```

Il bot gira in un loop infinito (termina con Ctrl+C). All'avvio chiede se si vuole resettare il wallet paper (`paper_wallet.json`) partendo dai valori di `walletIniziale.txt`; `fileCicloStart.txt` e `cronoMacd.txt` sono file runtime committati (valori solo simulati), mentre `paper_wallet.json` è gitignored (`gitignore`, de-tracciato da git).

## Dashboard web (Streamlit)

```
pip install -r requirements.txt   # include streamlit
streamlit run dashboard.py -- --file hype.txt
```

La dashboard mostra in tempo reale (auto-refresh regolabile nella sidebar):

- Parametri strategia e configurazione (`hype.txt` + `config.py`);
- Saldi e wallet: `walletIniziale.txt` (riferimento iniziale) e dettagli `paper_wallet.json` con valore USD di USDC e posizione a prezzo live;
- Prezzo, candele, indicatori (MACD/Signal/Histogram, RSI, SMA20/50);
- Log attività (`cronoMacd.txt`) con filtro libero.

## Come funziona

1. `botMacd.py` calcola MACD/RSI/SMA sull'ultima candela 5m e verifica il trend a 15m (prezzo > EMA200 e ADX >= soglia).
2. Al segnale di acquisto parte `StopTrail`: segue il prezzo abbassando lo stop loss. Se il trail supera `TRAIL_BUY_TIMEOUT` (15m) o se il prezzo subisce un breakdown oltre 3x stopsize, il segnale viene annullato senza eseguire l'acquisto.
3. Se lo stop viene toccato, l'ordine BUY viene eseguito.
4. **Gestione del rischio della posizione aperta**:
   - **Take Profit Parziale (50%)**: scatta a +1.5x ATR dall'ingresso; chiude il 50% della size e sposta automaticamente lo stop della posizione residua a **Break-Even** (+0.20%).
   - **Hard Stop Loss Dinamico**: rete di sicurezza a `max(pavimento %, 4x ATR)` sotto il prezzo medio di carico.
   - **Trailing Sell MACD**: al segnale contrario (MACD decrescente positivo e RSI > 40), `StopTrail` liquida la posizione al rialzo.
5. In reale gli ordini di stop usano `isMarket: True` per garantire l'esecuzione anche con volatilità elevata.

## Struttura

- `jbmainMacd.py` — entrypoint e parsing dei parametri.
- `botMacd.py` — loop principale sui segnali, decide buy/sell, TP e Hard Stop.
- `trailMacd.py` — trailing stop dopo il segnale con protezione timeout e invalidazione breakdown.
- `indicators.py` — MACD/RSI/SMA in Python puro.
- `config.py` — tutte le impostazioni di trading.
- `hl.py` — unico wrapper per Hyperliquid (saldi, candele, ordini, log).
- `dashboard.py` — dashboard web Streamlit (parametri, saldi, indicatori, log).

## Avvertenze

- I futures su Hyperliquid sono **levatizzati e rischiosi**: usa solo capitale che puoi permetterti di perdere.
- Il paper trading simula l'esecuzione al prezzo corrente; non garantisce gli stessi risultati del reale (slippage, commissioni, liquidazione).
- Il progetto è nato come adattamento di un bot spot Binance: controlla sempre la logica prima di usarlo in produzione.
