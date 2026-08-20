# HYPE Trailing MACD Bot

Bot di trading in Python per **perpetual futures su Hyperliquid**, basato su segnali **MACD/RSI** con **trailing stop** per l'esecuzione dei trade. Adattato da un bot spot Binance.

## Caratteristiche

- Segnali MACD + RSI per decidere compra/vendi, con doppio controllo sul trend dell'istogramma.
- Trailing stop (`StopTrail`): dopo un segnale, il bot segue il prezzo a intervalli regolari e aggiorna lo stop loss solo quando il prezzo si muove a favore; il trade parte quando lo stop viene toccato.
- **Paper trading** di default: prezzi reali da mainnet, wallet simulato in `fileBalance.txt` (ripristinato a ogni avvio da `walletIniziale.txt`) e dettagli in `paper_wallet.json`. Nessuna chiave richiesta.
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
```

| Parametro | Significato |
|-----------|-------------|
| `SYMBOL` | Coin del perpetual (es. `HYPE`) |
| `STOPSIZE` | Ampiezza dello stop (in dollari) |
| `INTERVAL` | Secondi tra un check del trailing stop e l'altro |
| `TIMEFRAME` | `1m`, `15m`, `30m`, `1h`, ... (`5s` non supportato, mappato su `1m`) |
| `MSIZE` | Moltiplicatore di `STOPSIZE` per il trigger del sell stop loss |
| `PERC_STABLE` | Quota della parte stable usata ad ogni buy (es. `0.10`) |
| `PERC_COIN` | Quota della posizione coin venduta ad ogni sell (es. `1`) |
| `REAL` | `n` = paper trading (default) \| `y` = ordini reali |
| `STOPLOSS` | `y`/`n`: attiva il sell stop loss (trigger HL) dopo ogni buy |

Le altre impostazioni (chiavi API, leva, margine, rete, fee) sono in `config.py`:

- `REAL = "n"` per default; con `REAL = "y"` servono `HL_ACCOUNT_ADDRESS` e `HL_SECRET_KEY`.
- `NETWORK = "mainnet"` / `"testnet"` — anche in carta i prezzi sono live da mainnet.
- `LEVERAGE` e `ISOLATED` ("y" = isolato, altrimenti cross) usati solo in reale.
- `START_BALANCE_USD` e `FEE_PCT` per il wallet simulato.

## Avvio

```
python jbmainMacd.py --file hype.txt
```

Il bot gira in un loop infinito (termina con Ctrl+C). Il balance paper (`fileBalance.txt`) viene ripristinato a ogni avvio dai valori di `walletIniziale.txt`; `fileCicloStart.txt` e `cronoMacd.txt` sono file runtime committati (valori solo simulati), mentre `paper_wallet.json` resta gitignored.

## Dashboard web (Streamlit)

```
pip install -r requirements.txt   # include streamlit
streamlit run dashboard.py -- --file hype.txt
```

La dashboard mostra in tempo reale (auto-refresh regolabile nella sidebar):

- Parametri strategia e configurazione (`hype.txt` + `config.py`);
- Saldi e wallet: `fileBalance.txt`, `walletIniziale.txt`, dettagli `paper_wallet.json` e valore USD di ogni token a prezzo live;
- Prezzo, candele, indicatori (MACD/Signal/Histogram, RSI, SMA20/50);
- Log attività (`cronoMacd.txt`) con filtro libero.

## Come funziona

1. `botMacd.py` calcola MACD/RSI/SMA sull'ultima candela e confronta l'istogramma con quello precedente (diminuzione positiva → vendi, diminuzione negativa → compra), con filtri RSI e wallet.
2. Al segnale parte `trailMacd.py`: aggiorna lo stop loss seguendo il prezzo finché non viene toccato, poi esegue buy/sell (e piazza un sell stop loss se `STOPLOSS=y`).
3. Il prezzo d'acquisto salvato in `fileCicloStart.txt` fa da gate: si vende solo sopra il prezzo medio.

## Struttura

- `jbmainMacd.py` — entrypoint e parsing dei parametri.
- `botMacd.py` — loop principale sui segnali, decide buy/sell.
- `trailMacd.py` — trailing stop dopo il segnale.
- `indicators.py` — MACD/RSI/SMA in Python puro.
- `config.py` — tutte le impostazioni di trading.
- `hl.py` — unico wrapper per Hyperliquid (saldi, candele, ordini, log).
- `dashboard.py` — dashboard web Streamlit (parametri, saldi, indicatori, log).

## Avvertenze

- I futures su Hyperliquid sono **levatizzati e rischiosi**: usa solo capitale che puoi permetterti di perdere.
- Il paper trading simula l'esecuzione al prezzo corrente; non garantisce gli stessi risultati del reale (slippage, commissioni, liquidazione).
- Il progetto è nato come adattamento di un bot spot Binance: controlla sempre la logica prima di usarlo in produzione.