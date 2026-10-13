import time
from datetime import datetime, timezone

from hl import Hyperliquid


class StopTrail:
    def __init__(self, market, type, stopsize, interval, multiSize, stableBuyUsd, percCoin, real, stoplossorder,
                 wallet_instance=None, data_instance=None, max_duration=900, max_drop_mult=3.0):
        if wallet_instance is not None and data_instance is not None:
            self.wallet_binance = wallet_instance
            self.data_binance = data_instance
        else:
            self.wallet_binance = Hyperliquid(real=real, market=market)
            self.data_binance = Hyperliquid(real=real, market=market)
        self.market = market
        self.type = type
        self.stopsize = stopsize
        self.interval = interval
        self.multiSize = multiSize
        self.stableBuyUsd = stableBuyUsd
        self.percCoin = percCoin
        self.real = real
        self.stoplossorder = stoplossorder
        self.sellOrderStopLoss = False  # order stop loss after buy
        self.running = False
        self.max_duration = float(max_duration) if max_duration is not None else 900.0
        self.max_drop_mult = float(max_drop_mult) if max_drop_mult is not None else 3.0
        self.initial_price = self.data_binance.get_price(self.market)
        self.stoploss = self.initialize_stop()
        self.wallet()

    def initialize_stop(self):
        print("inizialize stop")
        px = getattr(self, "initial_price", None) or self.data_binance.get_price(self.market)
        if self.type == "buy":
            return px + self.stopsize
        else:
            return px - self.stopsize

    # UPDATE STOP******************************************************
    def update_stop(self):
        self.wallet()

        # sell-------------------------------
        if self.type == "sell":
            # Se il prezzo sale allora sale anche il trail
            if (self.price - self.stopsize) > self.stoploss:
                self.stoploss = self.price - self.stopsize
                print("New high observed: Updating stop loss to %.4f" % self.stoploss)
            # Se il prezzo diminuisce e supera in diminuzione lo stoploss allora vendo
            elif self.price <= self.stoploss:
                print("Prezzo inferiore allo stop loss - vendo [ok]")
                if True:
                    print("Vincolo prezzo d'acquisto rimosso, vendo. [ok]")
                    if self.stoplossorder == "y":
                        self.wallet_binance.elimina_ordine(self.market)

                    self.wallet()
                    coinSell = self.coinBot
                    if coinSell <= 0:
                        self.running = False
                        return

                    print("++++++++++++++++++++++++++++++++++++++++++")
                    print("Selling | Amount: %.4f | Price: %.4f" % (coinSell, self.price))
                    if self.real == "y" and self.cryptoCoin > 0:
                        res = self.wallet_binance.sell(self.market, coinSell, self.price)
                        self.wallet_binance.cronoMacdString("Sell triggered Real | Amount: %.4f | Price: %.3f" % (coinSell, self.price))
                    else:
                        res = self.wallet_binance.sell(self.market, coinSell, self.price)

                    if not isinstance(res, dict) or not res.get("filled"):
                        err = res.get("error") if isinstance(res, dict) else "risposta ordine assente"
                        print(f"SELL NON ESEGUITO: {err}")
                        self.wallet_binance.cronoMacdString(f"SELL NON ESEGUITO: {err}")
                        return

                    fill_price = res.get("fill_price", self.price)

                    self.running = False

                    self.wallet()  # aggiorna i saldi dopo la vendita
                    fee_t = res.get("fee", 0.0)
                    pnl_t = res.get("pnl", 0.0)
                    self.data_binance.cronoMacdString("SELL | Fill: %.3f | Stop loss: %.3f | pnl: %.2f | fee: %.4f | USDC: %.2f | %s: %.4f"
                                                      % (fill_price, self.stoploss, pnl_t, fee_t, self.stableCoin, self.cryptoName, self.cryptoCoin))

                    self.wallet_binance.write_ciclostart(self.priceMin)
                    self.data_binance.cronoMacdString("pricemin : %.3f" % self.priceMin)

                    now = datetime.now(timezone.utc)
                    self.data_binance.cronoTradeMacd(now, "SELL", self.market, coinSell, fill_price)

                    print("Torno a MACD")

        # buy------------------------------
        elif self.type == "buy":
            if (self.price + self.stopsize) < self.stoploss:
                # Se il prezzo scende allora scende anche il trail
                self.stoploss = self.price + self.stopsize
                print("New low observed: Updating stop loss to %.4f" % self.stoploss)
            # Se il prezzo aumenta e supera lo stoploss allora compro
            elif self.price >= self.stoploss:
                self.wallet()
                stableBuy = self.stableBot
                amount = self.wallet_binance.usd_to_size(self.market, stableBuy, self.price)
                if self.stoplossorder == "y":
                    self.sellOrderStopLoss = True

                print("++++++++++++++++++++++++++++++++++++++++++")
                print("Buying | Amount: %.4f | Price: %.3f" % (amount, self.price))
                if self.real == "y" and self.stableCoin > 10:
                    res = self.wallet_binance.buy(self.market, amount, self.price)
                else:
                    res = self.wallet_binance.buy(self.market, amount, self.price)

                if not isinstance(res, dict) or not res.get("filled"):
                    err = res.get("error") if isinstance(res, dict) else "risposta ordine assente"
                    print(f"BUY NON ESEGUITO: {err}")
                    self.wallet_binance.cronoMacdString(f"BUY NON ESEGUITO: {err}")
                    return

                fill_price = res.get("fill_price", self.price)

                self.running = False

                self.wallet()  # aggiorna i saldi (e priceMin via get_entry_price)

                triggerPrice = round(fill_price - (self.stopsize * self.multiSize), 2)
                amountOrder = self.wallet_binance.round_size(self.market, self.cryptoCoin)
                if self.stoplossorder == "y":
                    self.wallet_binance.crea_ordine_sell_stop(self.market, amountOrder, triggerPrice, triggerPrice)
                    self.wallet_binance.cronoMacdString("Creo Ordine Sell Stop", self.market, amountOrder, triggerPrice, triggerPrice)
                fee_t = res.get("fee", 0.0)
                self.wallet_binance.cronoMacdString("BUY | Fill: %.3f | Stop loss: %.3f | fee: %.4f | USDC: %.2f | %s: %.4f"
                                                    % (fill_price, self.stoploss, fee_t, self.stableCoin, self.cryptoName, self.cryptoCoin))

                self.wallet_binance.write_ciclostart(self.priceMin)
                self.wallet_binance.cronoMacdString("pricemin : %.3f" % self.priceMin)

                now = datetime.now(timezone.utc)
                self.wallet_binance.cronoTradeMacd(now, "BUY", self.market, amount, fill_price)

                print("Torno a MACD")

    # PRINT************************************************************************
    def print_status(self):
        self.wallet()

        print("---------------------")
        print("Time: %s" % self.data_binance.timeCET())
        print("Trail type: %s" % self.type)
        print("  Market: %s" % self.market)
        print("Last price: %.4f" % self.price)
        print("Stop loss: %.4f" % self.stoploss)
        print("Stop size: %.4f" % self.stopsize)
        print("Diff: %.4f" % (self.stoploss - self.price))
        print("  Prezzo Minimo: %.4f" % self.priceMin)
        print("****** Wallet " + self.walletType)
        print(f"{self.stableName}: {self.stableCoin:.2f} - {self.cryptoName}: {self.cryptoCoin} [{self.cryptoCoinOrder}]")
        print("wallet bot-----")
        print(f"{self.stableName}: {self.stableBot:.2f} - {self.cryptoName}: {self.coinBot}")
        print("---------------------")

    # RUN*******************************************
    def run(self):
        self.running = True
        self.start_time = time.time()
        while self.running:
            try:
                if self.type == "buy":
                    elapsed = time.time() - self.start_time
                    if self.max_duration > 0 and elapsed > self.max_duration:
                        print("++++++++++++++++++++++++++++++++++++++++++")
                        print(
                            "TRAIL BUY ANNULLATO: timeout %.0fs superato. Segnale MACD scaduto."
                            % self.max_duration
                        )
                        self.wallet_binance.cronoMacdString(
                            "TRAIL BUY ANNULLATO per timeout (%.0fs) | Prezzo: %.4f"
                            % (self.max_duration, self.price)
                        )
                        self.running = False
                        break

                    max_drop = self.stopsize * self.max_drop_mult
                    if self.initial_price > 0 and (self.initial_price - self.price) > max_drop:
                        print("++++++++++++++++++++++++++++++++++++++++++")
                        print(
                            "TRAIL BUY ANNULLATO: prezzo sceso oltre soglia (%.4f -> %.4f, drop -$%.4f > soglia $%.4f). Breakdown in corso."
                            % (self.initial_price, self.price, self.initial_price - self.price, max_drop)
                        )
                        self.wallet_binance.cronoMacdString(
                            "TRAIL BUY ANNULLATO per drop eccessivo | Inizio: %.4f | Attuale: %.4f | Drop: -$%.4f"
                            % (self.initial_price, self.price, self.initial_price - self.price)
                        )
                        self.running = False
                        break

                self.print_status()
                self.update_stop()
            except Exception as e:
                print(f"Errore nel trail ({self.type}): {e}")
                try:
                    self.data_binance.cronoMacdString(f"ERRORE trail {self.type}: {e}")
                except Exception:
                    pass
            time.sleep(self.interval)

    # WALLET*************************************************************
    def wallet(self):
        self.price = self.data_binance.get_price(self.market)
        self.stableName = "USDC"
        self.cryptoName = self.market

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

        coin_symbol = self.market.split("/")[0]
        self.cryptoCoinTotali = self.wallet_binance.round_size(
            coin_symbol,
            self.cryptoCoin + self.cryptoCoinOrder,
        )
        self.stableBot = self.stableBuyUsd
        self.coinBot = self.wallet_binance.round_size(
            coin_symbol,
            self.cryptoCoin * self.percCoin,
        )
        self.priceMin = self.wallet_binance.get_entry_price(self.market)