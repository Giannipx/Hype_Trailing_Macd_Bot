import time
import datetime

import config
import indicators
from hl import Hyperliquid
from trailMacd import StopTrail

class CryptoBot:
    def __init__(self, market, stopSize, interval, timeframe, multiSize, percStable, percCoin, real, stoplossorder, limit=100, rsi_period=14, sma1_period=20, sma2_period=50):
        # Istanza per il wallet (reale) / simulato (paper)
        self.wallet_binance = Hyperliquid(real=real, market=market)
        # Istanza per i dati di mercato (sempre live)
        self.data_binance = Hyperliquid(real=real, market=market)
        self.market = market
        self.timeframe = timeframe
        self.limit = limit
        self.rsi_period = rsi_period
        self.sma1_period = sma1_period
        self.sma2_period = sma2_period
        self.stopSize = stopSize
        self.interval = interval
        self.multiSize = multiSize
        self.percStable = percStable
        self.percCoin = percCoin
        self.real = real
        self.stoplossorder = stoplossorder
        self.previous_histogram = None
        self.trailBuy = False
        self.trailSell = False
        self.walletType = "Null"  # Reale o test
        self.wallet_binance.reset_balance()  # ripristina il balance all'avvio
        self.wallet()

    # BANNER*********************************************
    def print_banner(self):
        rete = "MAINNET" if config.NETWORK == "mainnet" else "TESTNET"
        print("=" * 55)
        print("HYPE TRAILING MACD BOT - Hyperliquid")
        print("=" * 55)
        if self.real == "y":
            print("Modalità:          ORDINI REALI (ordini veri su %s)" % rete)
        else:
            print("Modalità:          PAPER TRADING (dati reali %s, ordini simulati)" % rete)
        print("-" * 55)
        print("PARAMETRI STRATEGIA")
        print("Coin:              %s" % self.market)
        print("Timeframe:         %s" % self.timeframe)
        print("Trail Stop Size:   $%.2f" % self.stopSize)
        print("Sell Stop Trigger: $%.2f sotto il prezzo (stopz x %gx)" % (self.stopSize * self.multiSize, self.multiSize))
        print("Intervallo Trail:  %gs" % self.interval)
        print("Invest. Stable:    %.0f%% per buy" % (self.percStable * 100))
        print("Vendita Coin:      %.0f%% per sell" % (self.percCoin * 100))
        print("StopLoss Ordine:   %s" % ("attivo" if self.stoplossorder == "y" else "non attivo"))
        print("Filtro RSI:        buy < 60 | sell > 40")
        print("Leva:              %gx (%s)" % (config.LEVERAGE, "isolated" if config.ISOLATED == "y" else "cross"))
        print("Fee (simulata):    %.3f%%" % (config.FEE_PCT * 100))
        print("Capitale Paper:    $%.2f" % config.START_BALANCE_USD)
        print("  Wallet iniziale (%s):" % "walletIniziale.txt")
        for tok, val in self.wallet_binance.read_wallet_iniziale().items():
            print("    %s: %s" % (tok, val))
        print("  Balance (%s): %s" % ("fileBalance.txt", self.wallet_binance.balance_string()))
        print("=" * 55)
        if self.real == "y":
            print("      Modalità REALE attiva: ordini veri su Hyperliquid %s - verifica chiavi e bilanci in config.py" % rete)
            print("   [REALE] Saldo conto (USDC): %.2f$" % self.stableCoin)
        else:
            print("      Modalità PAPER TRADING attiva: dati reali da %s, nessun ordine reale, nessuna chiave privata richiesta" % rete)
            print("   [PAPER] Saldo simulato: %.2f$" % self.stableCoin)
        print("")

    def run(self):
        self.print_banner()
        while True:
            now = datetime.datetime.now()

            # scelgo il timeframe da 1 minuto o da 1 ora
            if self.timeframe == "1m":
                seconds_until_next_time = 60 - now.second
            elif self.timeframe == "5s":
                # Hyperliquid non supporta 5s: si usa 1m
                seconds_until_next_time = 60 - now.second
            elif self.timeframe == "15m":
                quindici_minuti = now.minute + 15 - (now.minute % 15)
                seconds_until_next_time = ((quindici_minuti - now.minute) * (60)) - now.second
            elif self.timeframe == "30m":
                trenta_minuti = now.minute + 30 - (now.minute % 30)
                seconds_until_next_time = ((trenta_minuti - now.minute) * (60)) - now.second
            elif self.timeframe == "1h":  # 1 hour
                seconds_until_next_time = 3600 - (now.minute * 60 + now.second)
            else:
                seconds_until_next_time = 0  # Impostato a 0 per evitare errori

            time.sleep(seconds_until_next_time)

            if self.stoplossorder == "y":
                # verifico se ci sono ordini aperti e se corrispondono a quelli inseriti nel file ordini
                # se si verifica un sell stop loss bisogna eliminare l'ordine dal file
                self.wallet_binance.verifica_ordini(self.market)

            now = datetime.datetime.now().replace(microsecond=0)

            ohlcv = self.data_binance.ohlcv_data(self.market, self.timeframe, limit=self.limit)
            closes = [c[4] for c in ohlcv]

            macd_line, signal_line, histogram = indicators.macd_series(closes)
            rsi_values = indicators.rsi(closes, self.rsi_period)
            sma1 = indicators.sma(closes, self.sma1_period)
            sma2 = indicators.sma(closes, self.sma2_period)

            self.last_macd = round(macd_line[-1], 5)
            self.last_signal = round(signal_line[-1], 5)
            self.last_histogram = round(histogram[-1], 5)
            self.last_rsi = round(rsi_values, 2)
            self.sma1 = round(sma1, 2)
            self.sma2 = round(sma2, 2)

            self.wallet()

            self.data_binance.cronoMacdString(
                f"TRAIL MULTIPLER x{self.multiSize} | Price: {self.price} | "
                f"MACD: {self.last_macd} | Signal: {self.last_signal} | Histo: {self.last_histogram} | "
                f"RSI: {self.last_rsi} | SMA20: {self.sma1} | SMA50: {self.sma2} | "
                f"{self.stableName}: {self.stableCoin:.2f} | {self.cryptoName}: {self.cryptoCoin:.4f} | "
                f"trailBuy: {self.trailBuy} | trailSell: {self.trailSell}")

            print(f"********** WALLET [{self.walletType}] *************")
            print(f"TRAIL NEW MULTIPLER [{self.multiSize}]")
            print(f"Timeframe: [{self.timeframe}]")
            print("UTC:", now)

            print(f"  HISTOGRAM: {self.last_histogram} ({self.previous_histogram}) - MACD: {self.last_macd} - SIGNAL: {self.last_signal} ")
            print(f"  RSI ({self.rsi_period} periodi): {self.last_rsi} update 40/60 - SMA20: {self.sma1}  - SMA50: {self.sma2}")

            print(f"Last price: {self.price} - Prezzo Minimo: {self.priceMin} ")

            print(f"  {self.walletType} Wallet {self.stableName}: {self.stableCoin:.3f} - {self.cryptoName}: {self.cryptoCoin:.4f}")
            print("  " + self.wallet_binance.balance_string())
            print(f"  Bot Wallet {self.stableName}: {self.stableBot} - {self.cryptoName}: {self.coinBot} [{self.cryptoCoinOrder}]")

            print(f"BUY: {self.trailBuy} - SELL: {self.trailSell}")
            print("*********************************************")
            print("")

            # aumento positivo - no SELL****************************************************
            if self.previous_histogram is not None:
                diff = round((self.last_histogram - self.previous_histogram), 2)

                if self.last_macd > self.last_signal and self.last_histogram > self.previous_histogram:
                    trend = 'MACD: Aumento positivo'
                    print(trend, "[no]")

                # diminuzione positivo - SELL
                elif self.last_macd > self.last_signal and self.last_histogram < self.previous_histogram:
                    print(f"Differenza: {diff}")
                    trend = 'MACD: Diminuzione positivo'
                    print(trend, "[ok]")
                    if self.last_rsi > 40:
                        print("RSI positivo [ok]")
                        if self.cryptoCoin > 1:
                            print("Wallet Crypto [ok]")
                            if (self.price - self.stopSize) > self.priceMin:
                                print("Prezzo medio superato [ok]")
                                if self.trailSell == True:
                                    self.data_binance.cronoMacd(now, self.last_histogram, self.last_macd, self.last_signal, trend, self.last_rsi, "sell")
                                    task = StopTrail(self.market, "sell", self.stopSize, self.interval, self.multiSize, self.percStable, self.percCoin, self.real, self.stoplossorder)
                                    task.run()

                                    self.wallet()
                                else:
                                    print("trailSell False")
                            else:
                                print("Prezzo minimo non superato")

                        else:
                            print("zero coin")
                    else:
                        print("RSI negativo [no]")

                # diminuzione negativo - BUY
                elif self.last_macd < self.last_signal and self.last_histogram > self.previous_histogram:
                    print(f"Differenza: {diff}")
                    trend = 'MACD: Diminuzione Negativo'
                    print(trend, "[ok]")
                    if self.last_rsi < 60:
                        print("RSI negativo [ok]")
                        if self.stableCoin > 10:  # 10 usdt - invece di zero per minimo operativo
                            print("Wallet Stable >10 [ok]")
                            if self.trailBuy == True:
                                self.data_binance.cronoMacd(now, self.last_histogram, self.last_macd, self.last_signal, trend, self.last_rsi, "buy")
                                task = StopTrail(self.market, "buy", self.stopSize, self.interval, self.multiSize, self.percStable, self.percCoin, self.real, self.stoplossorder)
                                task.run()

                                self.wallet()
                            else:
                                print("trailBuy False")
                        else:
                            print("zero stableCoin [no]")
                    else:
                        print("RSI positivo [no]")

                # aumento negativo - no buy - no sell***********************************************************
                elif self.last_macd < self.last_signal and self.last_histogram < self.previous_histogram:
                    print(f"Differenza: {diff}")
                    trend = 'MACD: Aumento negativo'
                    print(trend, "[no]")

            else:
                trend = 'N/A'

            self.previous_histogram = self.last_histogram

    # WALLET*************************************************************
    def wallet(self):
        self.price = self.data_binance.get_price(self.market)
        self.cryptoName = self.market           # perpetual = solo nome coin (es. "SOL")
        self.stableName = "USDC"                # collaterale perp Hyperliquid

        if self.real == "y":
            self.walletType = "Real"
            self.stableCoin = self.wallet_binance.get_balance("USDC")
            self.cryptoCoin = self.wallet_binance.get_balance(self.cryptoName)
            self.cryptoCoinOrder = self.wallet_binance.get_balance_order(self.cryptoName)
        else:
            self.walletType = "Test"
            self.stableCoin = self.wallet_binance.read_balance(0)
            self.cryptoCoin = self.wallet_binance.read_balance(1)
            self.cryptoCoinOrder = self.wallet_binance.get_balance_order(self.cryptoName)

        self.stableBot = round(self.stableCoin * self.percStable, 2)
        self.coinBot = round(self.cryptoCoin * self.percCoin, 2)

        self.priceMin = self.wallet_binance.read_ciclostart(0)

        # verifico i cicli
        if self.price > self.priceMin and self.cryptoCoin > 0:
            self.trailSell = True
        else:
            self.trailSell = False

        if self.stableBot > 10:
            self.trailBuy = True
        else:
            self.trailBuy = False
