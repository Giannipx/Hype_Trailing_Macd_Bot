import time
from datetime import datetime, timezone

from hl import Hyperliquid

# ADATTAMENTO HYPERLIQUID: niente più ccxt/binance.
# Il trail mantiene la stessa logica: segue il prezzo ogni `interval`
# secondi e quando lo stop viene toccato compra/vende (paper simulato,
# reale via market_open/market_close).

class StopTrail:
    def __init__(self, market, type, stopsize, interval, multiSize, percStable, percCoin, real, stoplossorder,
                 wallet_instance=None, data_instance=None):
        # FIX: le istanze Hyperliquid vengono condivise con quelle di CryptoBot
        # invece di crearne di nuove ad ogni trade. Prima se ne aprivano 2 qui +
        # 2 in CryptoBot (overhead REST inutile) e _leverage_set era per-istanza,
        # quindi update_leverage veniva richiamato a ogni ciclo StopTrail.
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
                    coinSell = self.coinBot

                    print("++++++++++++++++++++++++++++++++++++++++++")
                    print("Selling | Amount: %.2f | Price: %.2f" % (coinSell, self.price))
                    # SELL (reale: market_close reduce_only | paper: wallet szi/USDC)
                    if self.real == "y" and self.cryptoCoin > 0:
                        res = self.wallet_binance.sell(self.market, coinSell, self.price)
                        self.wallet_binance.cronoMacdString("Sell triggered Real | Amount: %.4f | Price: %.3f" % (coinSell, self.price))
                    else:
                        res = self.wallet_binance.sell(self.market, coinSell, self.price)

                    # FIX: prima si assumeva sempre l'esecuzione (res poteva
                    # essere un dict con status di errore e veniva comunque
                    # trattato come fill). Ora si controlla esplicitamente
                    # "filled": se l'ordine è stato rifiutato/non eseguito, il
                    # trail NON si chiude (self.running resta True) e riprova
                    # al prossimo intervallo, senza toccare wallet/ciclostart/log
                    # come se la vendita fosse andata a buon fine.
                    if not isinstance(res, dict) or not res.get("filled"):
                        err = res.get("error") if isinstance(res, dict) else "risposta ordine assente"
                        print(f"SELL NON ESEGUITO: {err}")
                        self.wallet_binance.cronoMacdString(f"SELL NON ESEGUITO: {err}")
                        return

                    # FIX: prima si usava self.price (mid corrente) per log/pnl
                    # invece del prezzo di fill reale restituito dall'ordine —
                    # stessa correzione già fatta sul ramo BUY.
                    fill_price = res.get("fill_price", self.price)

                    self.running = False

                    self.wallet()  # aggiorna i saldi dopo la vendita
                    fee_t = res.get("fee", 0.0)
                    pnl_t = res.get("pnl", 0.0)
                    self.data_binance.cronoMacdString("SELL | Fill: %.3f | Stop loss: %.3f | pnl: %.2f | fee: %.4f | USDC: %.2f | %s: %.4f"
                                                      % (fill_price, self.stoploss, pnl_t, fee_t, self.stableCoin, self.cryptoName, self.cryptoCoin))

                    # FIX: priceMin non più forzato a 0 qui, ma sincronizzato
                    # con get_entry_price() (0.0 per una posizione chiusa,
                    # coerente con self.wallet() dopo la vendita). Il valore
                    # scritto in fileCicloStart.txt resta solo per compatibilità
                    # con la dashboard: la fonte di verità ora è get_entry_price().
                    self.wallet_binance.write_ciclostart(self.priceMin)
                    self.data_binance.cronoMacdString("pricemin : %.3f" % self.priceMin)

                    now = datetime.now(timezone.utc)
                    self.data_binance.cronoTradeMacd(now, "SELL", self.market, coinSell, fill_price)

                    print("Torno a MACD")

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
                amount = self.wallet_binance.usd_to_size(self.market, stableBuy, self.price)  # size perp (szDecimals)
                if self.stoplossorder == "y":
                    self.sellOrderStopLoss = True  # ordine prevenzione stop loss

                print("++++++++++++++++++++++++++++++++++++++++++")
                print("Buying | Amount: %.4f | Price: %.3f" % (amount, self.price))
                # BUY (reale: market_open long | paper: wallet szi/USDC)
                if self.real == "y" and self.stableCoin > 10:
                    res = self.wallet_binance.buy(self.market, amount, self.price)
                else:
                    res = self.wallet_binance.buy(self.market, amount, self.price)

                # FIX: come nel ramo sell, controllo esplicito di "filled"
                # prima di considerare l'ordine eseguito. Un ordine
                # rifiutato/errore non deve chiudere il trail né scrivere
                # wallet/ciclostart/log come se l'acquisto fosse riuscito.
                if not isinstance(res, dict) or not res.get("filled"):
                    err = res.get("error") if isinstance(res, dict) else "risposta ordine assente"
                    print(f"BUY NON ESEGUITO: {err}")
                    self.wallet_binance.cronoMacdString(f"BUY NON ESEGUITO: {err}")
                    return

                # FIX: prima si richiamava self.wallet() (che rifetcha il prezzo
                # live) e SOLO DOPO si calcolava triggerPrice usando il NUOVO
                # self.price - disallineato dal prezzo a cui l'ordine è stato
                # davvero eseguito. Ora si fissa fill_price PRIMA del refresh e
                # lo si usa per lo stop-loss e per i log, così restano coerenti
                # con l'esecuzione reale anche se il mercato si è mosso nel
                # frattempo.
                fill_price = res.get("fill_price", self.price)

                self.running = False

                self.wallet()  # aggiorna i saldi (e priceMin via get_entry_price)

                # Piazzo un ordine Sell Stop Loss (trigger HL, reduce_only,
                # isMarket=True: esegue anche su gap veloci)
                triggerPrice = round(fill_price - (self.stopsize * self.multiSize), 2)
                amountOrder = self.wallet_binance.round_size(self.market, self.cryptoCoin)
                if self.stoplossorder == "y":
                    self.wallet_binance.crea_ordine_sell_stop(self.market, amountOrder, triggerPrice, triggerPrice)
                    self.wallet_binance.cronoMacdString("Creo Ordine Sell Stop", self.market, amountOrder, triggerPrice, triggerPrice)
                fee_t = res.get("fee", 0.0)
                self.wallet_binance.cronoMacdString("BUY | Fill: %.3f | Stop loss: %.3f | fee: %.4f | USDC: %.2f | %s: %.4f"
                                                    % (fill_price, self.stoploss, fee_t, self.stableCoin, self.cryptoName, self.cryptoCoin))

                # FIX: priceMin locale (= self.price pre-fill) sostituito da
                # self.priceMin, ora popolato da get_entry_price() (media
                # ponderata reale/paper) dal self.wallet() qui sopra. Il file
                # fileCicloStart.txt resta scritto solo per compatibilità
                # dashboard, non è più la fonte di verità del gate di vendita.
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
        print("Last price: %.3f" % self.price)
        print("Stop loss: %.3f" % self.stoploss)
        print("Stop size: %.2f" % self.stopsize)
        print("Diff: %.3f" % (self.stoploss - self.price))
        print("  Prezzo Minimo: %.3f" % self.priceMin)
        print("****** Wallet " + self.walletType)
        print(f"{self.stableName}: {self.stableCoin:.2f} - {self.cryptoName}: {self.cryptoCoin} [{self.cryptoCoinOrder}]")
        print("wallet bot-----")
        print(f"{self.stableName}: {self.stableBot:.2f} - {self.cryptoName}: {self.coinBot}")
        print("---------------------")

    # RUN*******************************************
    def run(self):
        self.running = True
        while self.running:
            try:
                self.print_status()
                self.update_stop()
            except Exception as e:
                # FIX: come in botMacd.py, un errore di rete/API qui (get_price,
                # candele, ordine) non deve terminare il processo mentre il
                # trail è attivo e magari una posizione è aperta senza stop.
                print(f"Errore nel trail ({self.type}): {e}")
                try:
                    self.data_binance.cronoMacdString(f"ERRORE trail {self.type}: {e}")
                except Exception:
                    pass
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

        # FIX: stessa correzione di botMacd.py — get_entry_price() invece di
        # fileCicloStart.txt come fonte del prezzo medio d'ingresso.
        self.priceMin = self.wallet_binance.get_entry_price(self.market)