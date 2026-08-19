import time
from datetime import datetime

from hl import Hyperliquid

# ADATTAMENTO HYPERLIQUID: niente più ccxt/binance.
# Il trail mantiene la stessa logica: segue il prezzo ogni `interval`
# secondi e quando lo stop viene toccato compra/vende (paper simulato,
# reale via market_open/market_close).

class StopTrail:
    def __init__(self, market, type, stopsize, interval, multiSize, percStable, percCoin, real, stoplossorder):
        self.wallet_binance = Hyperliquid(real=real, market=market)
        self.data_binance = Hyperliquid(real=real, market=market)
        self.market = market
        self.type = type
        self.stopsize = stopsize
        self.interval = interval
        self.multiSize = multiSize
        self.percStable = percStable
        self.percCoin = percCoin
        self.real = real
        self.stoplossorder = stoplossorder
        self.sellOrderStopLoss = False  # order stop loss after buy
        self.running = False
        self.stoploss = self.initialize_stop()
        self.wallet()

    def initialize_stop(self):
        print("inizialize stop")
        if self.type == "buy":
            return (self.data_binance.get_price(self.market) + self.stopsize)
        else:
            return (self.data_binance.get_price(self.market) - self.stopsize)

    # UPDATE STOP******************************************************
    def update_stop(self):
        self.wallet()

        # sell-------------------------------
        if self.type == "sell":
            # Se il prezzo sale allora sale anche il trail
            if (self.price - self.stopsize) > self.stoploss:
                self.stoploss = self.price - self.stopsize
                print("New high observed: Updating stop loss to %.2f" % self.stoploss)
            # Se il prezzo diminuisce e supera in diminuzione lo stoploss allora vendo
            elif self.price <= self.stoploss:
                print("Prezzo inferiore allo stop loss - vendo [ok]")
                if self.price > self.priceMin:
                    print("Prezzo di vendita superiore al prezzo medio d'acquisto [ok]")
                    if self.stoplossorder == "y":
                        self.wallet_binance.elimina_ordine(self.market)

                    self.wallet()
                    stableSell = self.stableBot
                    coinSell = self.coinBot
                    priceMin = 0

                    print("++++++++++++++++++++++++++++++++++++++++++")
                    print("Selling | Amount: %.2f | Price: %.2f" % (coinSell, self.price))
                    # SELL (reale: market_close reduce_only | paper: wallet szi/USDC)
                    if self.real == "y" and self.cryptoCoin > 0:
                        self.wallet_binance.sell(self.market, coinSell, self.price)
                        self.wallet_binance.cronoMacdString("Sell triggered Real | Amount: %.2f | Price: %.2f" % (coinSell, self.price))
                    else:
                        self.wallet_binance.sell(self.market, coinSell, self.price)

                    self.data_binance.cronoMacdString("Sell triggered | Price: %.2f | Stop loss: %.2f" % (self.price, self.stoploss))
                    self.data_binance.cronoMacdString("stable : %.2f | crypto : %.2f | pricemin : %.2f" % (self.stableBot, self.coinBot, priceMin))

                    self.wallet_binance.write_ciclostart(priceMin)  # vendo

                    now = datetime.now()
                    self.data_binance.cronoTradeMacd(now, "SELL", self.market, stableSell, self.price)

                    print("Torno a MACD")
                    self.running = False  # Torno a Macd

                else:
                    print("prezzo minimo non superato [no]")
                    self.type = "sell"
                    self.running = True

        # buy------------------------------
        elif self.type == "buy":
            if (self.price + self.stopsize) < self.stoploss:
                # Se il prezzo scende allora scende anche il trail
                self.stoploss = self.price + self.stopsize
                print("New low observed: Updating stop loss to %.2f" % self.stoploss)
            # Se il prezzo aumenta e supera lo stoploss allora compro
            elif self.price >= self.stoploss:
                self.wallet()
                stableBuy = self.stableBot
                amount = self.wallet_binance.usd_to_size(self.market, stableBuy, self.price)  # size perp (szDecimals, fee inclusa)
                priceMin = self.price
                if self.stoplossorder == "y":
                    self.sellOrderStopLoss = True  # ordine prevenzione stop loss

                print("++++++++++++++++++++++++++++++++++++++++++")
                print("Buying | Amount: %.2f | Price: %.2f" % (amount, self.price))
                # BUY (reale: market_open long | paper: wallet szi/USDC)
                if self.real == "y" and self.stableCoin > 10:
                    self.wallet_binance.buy(self.market, amount, self.price)
                else:
                    self.wallet_binance.buy(self.market, amount, self.price)

                # Piazzo un ordine Sell Stop Loss (trigger HL, reduce_only)
                triggerPrice = round(self.price - (self.stopsize * self.multiSize), 2)
                sellPrice = round(self.price - (self.stopsize * self.multiSize) - (self.stopsize / 2), 2)
                amountOrder = self.wallet_binance.round_size(self.market, self.cryptoCoin)
                if self.stoplossorder == "y":
                    self.wallet_binance.crea_ordine_sell_stop(self.market, amountOrder, triggerPrice, sellPrice)
                    self.wallet_binance.cronoMacdString("Creo Ordine Sell Stop", self.market, amountOrder, triggerPrice, sellPrice)
                self.wallet_binance.cronoMacdString("Buy triggered | Price: %.2f | Stop loss: %.2f" % (self.price, self.stoploss))
                self.wallet_binance.cronoMacdString("stable : %.2f | crypto : %.2f | pricemin : %.2f" % (self.stableBot, self.coinBot, priceMin))

                self.wallet_binance.write_ciclostart(priceMin)  # compro

                now = datetime.now()
                self.wallet_binance.cronoTradeMacd(now, "BUY", self.market, stableBuy, self.price)

                print("Torno a MACD")
                self.running = False  # torno a MACD

    # PRINT************************************************************************
    def print_status(self):
        self.wallet()

        print("---------------------")
        print("Time: %s" % self.data_binance.timeCET())
        print("Trail type: %s" % self.type)
        print("  Market: %s" % self.market)
        print("Last price: %.2f" % self.price)
        print("Stop loss: %.2f" % self.stoploss)
        print("Stop size: %.2f" % self.stopsize)
        print("Diff: %.2f" % (self.stoploss - self.price))
        print("  Prezzo Minimo: %.2f" % self.priceMin)
        print("****** Wallet " + self.walletType)
        print(str(self.stableName) + ": %.2f" % self.stableCoin)
        print(str(self.cryptoName) + ": %.2f" % self.cryptoCoin + "[%.2f" % self.cryptoCoinOrder + "]")
        print("wallet bot-----")
        print(str(self.stableName) + ": %.2f" % self.stableBot)
        print(str(self.cryptoName) + ": %.4f" % self.coinBot)
        print("---------------------")

    # RUN*******************************************
    def run(self):
        self.running = True
        while self.running:
            self.print_status()
            self.update_stop()
            time.sleep(self.interval)

    # WALLET*************************************************************
    def wallet(self):
        self.price = self.data_binance.get_price(self.market)
        self.stableName = "USDC"                # collaterale perp Hyperliquid
        self.cryptoName = self.market           # perpetual = solo nome coin

        if self.real == "y":
            self.walletType = "Real"
            self.stableCoin = self.wallet_binance.get_balance("USDC")
            self.cryptoCoin = self.wallet_binance.get_balance(self.cryptoName)
            self.cryptoCoinOrder = self.wallet_binance.get_balance_order(self.cryptoName)
            self.cryptoCoinTotali = round(self.cryptoCoin + self.cryptoCoinOrder, 2)
        else:
            self.walletType = "Test"
            self.stableCoin = self.wallet_binance.read_balance(0)
            self.cryptoCoin = self.wallet_binance.read_balance(1)
            self.cryptoCoinOrder = self.wallet_binance.get_balance_order(self.cryptoName)
            self.cryptoCoinTotali = round(self.cryptoCoin + self.cryptoCoinOrder, 2)

        self.stableBot = round(self.stableCoin * self.percStable, 2)
        self.coinBot = round(self.cryptoCoin * self.percCoin, 2)

        self.priceMin = self.wallet_binance.read_ciclostart(0)
