from botMacd import CryptoBot
from pathlib import Path
import argparse

def main(options):
    # Tutte le stringhe chiave/valore nel file di testo non devono avere spazi: REAL=n
    myObject = {}
    with open(options.file) as f:  # param.txt
        for line in f.readlines():
            line = line.rstrip("\n")
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            myObject[key.strip()] = value.strip()
    symbol = str(myObject['SYMBOL'])  # SOL/USDT (usato solo per estrarre la coin)
    stopSize = float(myObject['STOPSIZE'])  # 10
    interval = float(myObject['INTERVAL'])  # 4
    timeframe = str(myObject['TIMEFRAME'])  # 1m - 1h
    multiSize = float(myObject['MSIZE'])  # 3 - moltiplicatore stopSize per pbuy - pExit
    percStable = float(myObject['PERC_STABLE'])  # 0.1 - PERCENTUALE DI INVESTIMENTO DELLA STABLE COIN
    percCoin = float(myObject['PERC_COIN'])  # 1 - PERCENTUALE DI INVESTIMENTO DELLA CRYPTO COIN
    real = str(myObject['REAL'])  # y/n - bot di test oppure reale
    stoplossorder = str(myObject.get('STOPLOSS', 'n'))  # y/n - stoploss per evitare perdite (opzionale)

    # STOPSIZE adattivo via ATR: opzionali, se assenti da hype.txt usano
    # i default (14 periodi, moltiplicatore 1.5x). STOPSIZE resta letto
    # come prima e diventa il "pavimento" minimo dello stop dinamico.
    atr_period = int(float(myObject.get('ATR_PERIOD', 14)))
    atr_mult = float(myObject.get('ATR_MULT', 1.5))
    max_loss_pct = float(myObject.get('MAX_LOSS_PCT', 0.01))
    if not 0 < max_loss_pct < 1:
        raise ValueError('MAX_LOSS_PCT deve essere una frazione tra 0 e 1 (es. 0.01 = 1%)')

    bot = CryptoBot(symbol, stopSize, interval, timeframe, multiSize, percStable, percCoin, real, stoplossorder,
                     atr_period=atr_period, atr_mult=atr_mult, max_loss_pct=max_loss_pct)
    bot.run()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--file', type=Path, help='File dei parametri (SOL o CAKE)', required=True)
    options = parser.parse_args()
    main(options)

# python jbmainMacd.py --file solM.txt
