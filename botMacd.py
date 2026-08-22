import time
import datetime

import config
import indicators
from hl import Hyperliquid
from trailMacd import StopTrail


class CryptoBot:
    """
    HYPE Trailing MACD Bot.

    Versione aggiornata con:
    - filtro trend 15m per i BUY
    - limite esposizione massima
    - massimo numero di ingressi
    - riarmo del setup BUY
    - hard stop loss
    """

    # ==========================================================
    # RISK MANAGEMENT
    # ==========================================================
    # Non serve modificare config.py per questi tre parametri.
    MAX_POSITION_PCT = 0.50       # max 50% dell'equity in HYPE
    MAX_BUY_ENTRIES = 2           # max 2 BUY per ciclo di posizione
    MAX_LOSS_PCT = 0.01           # hard stop: -1% dal prezzo medio

    def __init__(
        self,
        market,
        stopSize,
        interval,
        timeframe,
        multiSize,
        percStable,
        percCoin,
        real,
        stoplossorder,
        limit=100,
        rsi_period=14,
        sma1_period=20,
        sma2_period=50,
        trend_timeframe="15m",
        atr_period=14,
        atr_mult=1.5,
    ):
        # Wallet / ordini
        self.wallet_binance = Hyperliquid(
            real=real,
            market=market,
        )

        # Dati di mercato sempre live
        self.data_binance = Hyperliquid(
            real=real,
            market=market,
        )

        self.market = market
        self.timeframe = timeframe
        self.trend_timeframe = trend_timeframe
        self.limit = limit

        self.rsi_period = rsi_period
        self.sma1_period = sma1_period
        self.sma2_period = sma2_period

        # ------------------------------------------------------
        # STOPSIZE ADATTIVO (ATR)
        # ------------------------------------------------------
        # STOPSIZE da hype.txt diventa un PAVIMENTO di sicurezza, non più
        # il valore usato direttamente: ad ogni ciclo self.stopSize viene
        # ricalcolato come ATR(atr_period) * atr_mult sul timeframe
        # operativo, con un minimo di stop_floor (il vecchio STOPSIZE
        # statico) per evitare uno stop irrealisticamente stretto quando
        # la volatilità recente è quasi nulla (es. mercato piatto di notte).
        self.stop_floor = stopSize
        self.atr_period = atr_period
        self.atr_mult = atr_mult
        self.atr = None
        self.stopSize = stopSize  # valore iniziale, prima del primo calcolo ATR

        self.interval = interval
        self.multiSize = multiSize
        self.percStable = percStable
        self.percCoin = percCoin
        self.real = real
        self.stoplossorder = stoplossorder

        # MACD
        self.previous_histogram = None
        self.last_macd = None
        self.last_signal = None
        self.last_histogram = None
        self.last_rsi = None

        # SMA timeframe operativo
        self.sma1 = None
        self.sma2 = None

        # Trend timeframe superiore
        self.trend_sma1 = None
        self.trend_sma2 = None
        self.trend_price = None
        self.trend_bullish = False

        # Trailing
        self.trailBuy = False
        self.trailSell = False

        # Wallet
        self.walletType = "Null"

        # ------------------------------------------------------
        # RISK MANAGEMENT
        # ------------------------------------------------------
        self.buy_entries = 0

        # Dopo un BUY il setup deve prima "scaricarsi"
        # prima di consentire un nuovo BUY.
        self.buy_armed = True

        self._prompt_reset_paper()
        self.wallet()

    # ==========================================================
    # PAPER WALLET RESET
    # ==========================================================

    def _prompt_reset_paper(self):
        if self.real == "y":
            return

        try:
            ans = input(
                "Vuoi resettare il wallet paper "
                "(riparte da walletIniziale.txt)? [y/N]: "
            )
        except EOFError:
            ans = ""

        if ans.strip().lower() == "y":
            self.wallet_binance.reset_balance()

        try:
            ans = input(
                "Vuoi resettare anche la cronologia "
                "(cronoMacd.txt)? [y/N]: "
            )
        except EOFError:
            ans = ""

        if ans.strip().lower() == "y":
            with open(config.CRONO_FILE, "w"):
                pass
            print("Cronologia paper resettata: %s" % config.CRONO_FILE)

    # ==========================================================
    # BANNER
    # ==========================================================

    def print_banner(self):
        rete = (
            "MAINNET"
            if config.NETWORK == "mainnet"
            else "TESTNET"
        )

        print("=" * 60)
        print(
            "HYPE TRAILING MACD BOT - Hyperliquid - ver: %s"
            % config.VERSION
        )
        print("=" * 60)

        if self.real == "y":
            print(
                "Modalità:          ORDINI REALI "
                "(ordini veri su %s)" % rete
            )
        else:
            print(
                "Modalità:          PAPER TRADING "
                "(dati reali %s, ordini simulati)" % rete
            )

        print("-" * 60)
        print("PARAMETRI STRATEGIA")
        print("Coin:              %s" % self.market)
        print("Timeframe entry:   %s" % self.timeframe)
        print("Timeframe trend:   %s" % self.trend_timeframe)

        print(
            "Trail Stop Size:   $%.2f (pavimento) "
            "- adattivo via ATR(%d) x%.1f"
            % (
                self.stop_floor,
                self.atr_period,
                self.atr_mult,
            )
        )

        print(
            "Sell Stop Trigger: $%.2f sotto il prezzo "
            "(stopz x %gx)"
            % (
                self.stopSize * self.multiSize,
                self.multiSize,
            )
        )

        print(
            "Intervallo Trail:  %gs"
            % self.interval
        )

        print(
            "Invest. Stable:    %.0f%% per buy"
            % (self.percStable * 100)
        )

        print(
            "Vendita Coin:      %.0f%% per sell"
            % (self.percCoin * 100)
        )

        print(
            "StopLoss Ordine:   %s"
            % (
                "attivo"
                if self.stoplossorder == "y"
                else "non attivo"
            )
        )

        print("Filtro RSI:        buy < 60 | sell > 40")

        print(
            "Trend filter:      %s SMA20 > SMA50 + Price > SMA50"
            % self.trend_timeframe
        )

        print(
            "Risk max position: %.0f%% equity"
            % (self.MAX_POSITION_PCT * 100)
        )

        print(
            "Risk max BUY:      %d"
            % self.MAX_BUY_ENTRIES
        )

        print(
            "Hard stop:         -%.2f%%"
            % (self.MAX_LOSS_PCT * 100)
        )

        print(
            "Leva:              %gx (%s)"
            % (
                config.LEVERAGE,
                "isolated"
                if config.ISOLATED == "y"
                else "cross",
            )
        )

        print(
            "Fee (simulata):    %.3f%%"
            % (config.FEE_PCT * 100)
        )

        print(
            "Capitale Paper:    $%.2f"
            % config.START_BALANCE_USD
        )

        print("  Wallet iniziale (walletIniziale.txt):")

        for tok, val in (
            self.wallet_binance
            .read_wallet_iniziale()
            .items()
        ):
            print("    %s: %s" % (tok, val))

        pw = self.wallet_binance.paper_wallet_dump()

        print(
            "  Paper wallet: USDC: %.2f | %s: %.4f | "
            "entry: %.3f | PnL: %.2f | fee: %.2f | trade: %d"
            % (
                pw["cash_usd"],
                self.cryptoName,
                pw["sz"],
                pw["entry_px"],
                pw["realized_pnl_usd"],
                pw["fees_usd"],
                pw["n_trades"],
            )
        )

        print("=" * 60)

        if self.real == "y":
            print(
                "Modalità REALE attiva: ordini veri su "
                "Hyperliquid %s"
                % rete
            )
            print(
                "[REALE] Saldo conto (USDC): %.2f$"
                % self.stableCoin
            )
        else:
            print(
                "Modalità PAPER TRADING attiva: "
                "dati reali da %s, nessun ordine reale"
                % rete
            )
            print(
                "[PAPER] Saldo simulato: %.2f$"
                % self.stableCoin
            )

        print("")

    # ==========================================================
    # TREND FILTER
    # ==========================================================

    def calculate_trend_filter(self):
        trend_ohlcv = self.data_binance.ohlcv_data(
            self.market,
            self.trend_timeframe,
            limit=self.limit,
        )

        trend_closes = [c[4] for c in trend_ohlcv]

        min_required = max(
            self.sma1_period,
            self.sma2_period,
            50,
        )

        if len(trend_closes) < min_required:
            raise ValueError(
                "Dati insufficienti per trend %s: %d candele "
                "(minimo %d)"
                % (
                    self.trend_timeframe,
                    len(trend_closes),
                    min_required,
                )
            )

        trend_sma1 = indicators.sma(
            trend_closes,
            self.sma1_period,
        )

        trend_sma2 = indicators.sma(
            trend_closes,
            self.sma2_period,
        )

        if trend_sma1 is None or trend_sma2 is None:
            raise ValueError(
                "SMA trend non calcolabili sul timeframe %s"
                % self.trend_timeframe
            )

        self.trend_price = trend_closes[-1]
        self.trend_sma1 = trend_sma1
        self.trend_sma2 = trend_sma2

        self.trend_bullish = (
            self.trend_price > self.trend_sma2
            and self.trend_sma1 > self.trend_sma2
        )

        return self.trend_bullish

    # ==========================================================
    # HARD STOP LOSS
    # ==========================================================

    def check_hard_stop(self):
        """
        Chiude tutta la posizione se il prezzo scende di MAX_LOSS_PCT
        sotto il prezzo medio di carico.

        Questo stop è indipendente dal MACD e dal trailing.
        """

        if self.cryptoCoin <= 0:
            return False

        if self.priceMin <= 0:
            return False

        stop_price = (
            self.priceMin
            * (1.0 - self.MAX_LOSS_PCT)
        )

        if self.price > stop_price:
            return False

        print("")
        print("=" * 60)
        print("!!! HARD STOP LOSS !!!")
        print(
            "Prezzo medio:      %.4f"
            % self.priceMin
        )
        print(
            "Prezzo corrente:   %.4f"
            % self.price
        )
        print(
            "Stop:              %.4f (-%.2f%%)"
            % (
                stop_price,
                self.MAX_LOSS_PCT * 100,
            )
        )
        print(
            "Posizione:         %.4f HYPE"
            % self.cryptoCoin
        )
        print("=" * 60)

        try:
            # Se esiste uno stop reale già aperto, cancellalo
            # prima della chiusura manuale.
            if (
                self.real == "y"
                and self.stoplossorder == "y"
            ):
                try:
                    self.wallet_binance.elimina_ordine(
                        self.market
                    )
                except Exception as e:
                    print(
                        "Avviso cancellazione SL: %s"
                        % e
                    )

            amount = self.wallet_binance.round_size(
                self.market.split("/")[0],
                self.cryptoCoin,
            )

            if amount <= 0:
                return False

            result = self.wallet_binance.sell(
                self.market,
                amount,
                self.price,
            )

            if not result.get("filled", False):
                print(
                    "HARD STOP NON ESEGUITO: %s"
                    % result.get("error")
                )

                self.data_binance.cronoMacdString(
                    "HARD STOP NON ESEGUITO",
                    result.get("error"),
                )

                return False

            fill_price = result.get(
                "fill_price",
                self.price,
            )

            pnl = result.get(
                "pnl",
                0.0,
            )

            fee = result.get(
                "fee",
                0.0,
            )

            self.data_binance.cronoMacdString(
                "HARD STOP | Entry %.4f | "
                "Trigger %.4f | Fill %.4f | "
                "PnL %.4f | Fee %.4f"
                % (
                    self.priceMin,
                    stop_price,
                    fill_price,
                    pnl,
                    fee,
                )
            )

            print(
                "HARD STOP ESEGUITO | Fill %.4f | "
                "PnL %.4f | Fee %.4f"
                % (
                    fill_price,
                    pnl,
                    fee,
                )
            )

            self.buy_entries = 0
            self.buy_armed = True
            self.previous_histogram = None

            self.wallet()

            return True

        except Exception as e:
            print(
                "Errore HARD STOP: %s"
                % e
            )

            self.data_binance.cronoMacdString(
                "ERRORE HARD STOP",
                str(e),
            )

            return False

    # ==========================================================
    # BUY RISK FILTER
    # ==========================================================

    def can_open_buy(self):
        """
        Controlla:
        - massimo numero di BUY;
        - nuovo setup MACD;
        - massima esposizione.
        """

        if self.buy_entries >= self.MAX_BUY_ENTRIES:
            print(
                "RISK FILTER [NO BUY] | "
                "MAX_BUY_ENTRIES=%d"
                % self.MAX_BUY_ENTRIES
            )

            self.data_binance.cronoMacdString(
                "BUY BLOCCATO | MAX_BUY_ENTRIES | entries=%d"
                % self.buy_entries
            )

            return False

        if not self.buy_armed:
            print(
                "RISK FILTER [NO BUY] | "
                "SETUP NON RIARMATO"
            )

            self.data_binance.cronoMacdString(
                "BUY BLOCCATO | SETUP NON RIARMATO"
            )

            return False

        position_value = (
            self.cryptoCoin * self.price
        )

        equity = (
            self.stableCoin
            + position_value
        )

        max_position = (
            equity * self.MAX_POSITION_PCT
        )

        next_buy_value = self.stableBot

        if (
            position_value + next_buy_value
            > max_position
        ):
            print(
                "RISK FILTER [NO BUY] | MAX POSITION"
            )

            print(
                "  Posizione attuale: $%.2f"
                % position_value
            )

            print(
                "  Max posizione:     $%.2f"
                % max_position
            )

            self.data_binance.cronoMacdString(
                "BUY BLOCCATO | MAX_POSITION | "
                "position=%.2f | max=%.2f"
                % (
                    position_value,
                    max_position,
                )
            )

            return False

        return True

    # ==========================================================
    # RIARMO BUY
    # ==========================================================

    def rearm_buy_setup_if_needed(self):
        """
        Un BUY disarma il setup.
        Quando l'istogramma torna a peggiorare, il setup viene
        considerato terminato e un nuovo BUY potrà essere cercato.
        """

        if self.buy_armed:
            return

        if self.previous_histogram is None:
            return

        if self.last_histogram < self.previous_histogram:
            self.buy_armed = True

            print(
                "BUY SETUP RIARMATO"
            )

            self.data_binance.cronoMacdString(
                "BUY SETUP RIARMATO"
            )

    # ==========================================================
    # RUN
    # ==========================================================

    def run(self):
        self.print_banner()

        while True:

            # --------------------------------------------------
            # SINCRONIZZAZIONE TIMEFRAME
            # --------------------------------------------------

            now = datetime.datetime.now(
                datetime.timezone.utc
            )

            if self.timeframe == "1m":
                seconds_until_next_time = (
                    60 - now.second
                )

            elif self.timeframe == "5s":
                seconds_until_next_time = (
                    60 - now.second
                )

            elif self.timeframe == "5m":
                cinque_minuti = (
                    now.minute
                    + 5
                    - (now.minute % 5)
                )

                seconds_until_next_time = (
                    (cinque_minuti - now.minute) * 60
                    - now.second
                )

            elif self.timeframe == "15m":
                quindici_minuti = (
                    now.minute
                    + 15
                    - (now.minute % 15)
                )

                seconds_until_next_time = (
                    (quindici_minuti - now.minute) * 60
                    - now.second
                )

            elif self.timeframe == "30m":
                trenta_minuti = (
                    now.minute
                    + 30
                    - (now.minute % 30)
                )

                seconds_until_next_time = (
                    (trenta_minuti - now.minute) * 60
                    - now.second
                )

            elif self.timeframe == "1h":
                seconds_until_next_time = (
                    3600
                    - (now.minute * 60 + now.second)
                )

            else:
                seconds_until_next_time = 0

            if seconds_until_next_time > 0:
                time.sleep(seconds_until_next_time)

            # Buffer dopo chiusura candela
            time.sleep(2)

            if self.stoplossorder == "y":
                self.wallet_binance.verifica_ordini(
                    self.market
                )

            now = datetime.datetime.now(
                datetime.timezone.utc
            ).replace(microsecond=0)

            # --------------------------------------------------
            # DATI + INDICATORI
            # --------------------------------------------------

            try:
                ohlcv = self.data_binance.ohlcv_data(
                    self.market,
                    self.timeframe,
                    limit=self.limit,
                )

                closes = [c[4] for c in ohlcv]

                min_richieste = max(
                    self.sma2_period,
                    self.rsi_period + 1,
                    self.atr_period + 1,
                    35,
                )

                if len(closes) < min_richieste:
                    print(
                        "Dati insufficienti: %d candele "
                        "(minimo %d)."
                        % (
                            len(closes),
                            min_richieste,
                        )
                    )

                    self.data_binance.cronoMacdString(
                        "SKIP ciclo: solo %d candele "
                        "ricevute (minimo %d)"
                        % (
                            len(closes),
                            min_richieste,
                        )
                    )

                    continue

                macd_line, signal_line, histogram = (
                    indicators.macd_series(closes)
                )

                rsi_values = indicators.rsi(
                    closes,
                    self.rsi_period,
                )

                sma1 = indicators.sma(
                    closes,
                    self.sma1_period,
                )

                sma2 = indicators.sma(
                    closes,
                    self.sma2_period,
                )

                atr_value = indicators.atr(
                    ohlcv,
                    self.atr_period,
                )

                if (
                    not histogram
                    or not macd_line
                    or not signal_line
                    or rsi_values is None
                    or sma1 is None
                    or sma2 is None
                    or atr_value is None
                ):
                    print(
                        "Indicatori non calcolabili. "
                        "Salto ciclo."
                    )

                    self.data_binance.cronoMacdString(
                        "SKIP ciclo: indicatori "
                        "non calcolabili"
                    )

                    continue

                self.last_macd = macd_line[-1]
                self.last_signal = signal_line[-1]
                self.last_histogram = histogram[-1]
                self.last_rsi = rsi_values

                self.sma1 = round(sma1, 2)
                self.sma2 = round(sma2, 2)

                # FIX: STOPSIZE adattivo. self.stop_floor è il vecchio
                # valore statico da hype.txt, usato come minimo per evitare
                # uno stop troppo stretto quando l'ATR crolla (mercato
                # piatto). atr_mult amplifica la volatilità recente per
                # dare margine al trailing stop di respirare senza essere
                # toccato dal rumore normale.
                self.atr = atr_value
                self.stopSize = round(
                    max(self.stop_floor, self.atr * self.atr_mult),
                    4,
                )

                # Trend 15m
                self.calculate_trend_filter()

                # Wallet / prezzo
                self.wallet()

            except Exception as e:
                print(
                    "Errore nel ciclo principale "
                    "(rete/API/indicatori): %s"
                    % e
                )

                try:
                    self.data_binance.cronoMacdString(
                        "ERRORE ciclo principale: %s"
                        % e
                    )
                except Exception:
                    pass

                continue

            # --------------------------------------------------
            # HARD STOP
            # --------------------------------------------------

            if self.check_hard_stop():
                continue

            # --------------------------------------------------
            # LOG
            # --------------------------------------------------

            self.data_binance.cronoMacdString(
                "TRAIL MULTIPLER x%s | Price: %s | "
                "MACD: %.5f | Signal: %.5f | "
                "Histo: %.5f | RSI: %.2f | "
                "SMA20: %.2f | SMA50: %.2f | "
                "ATR: %.4f | StopSize: %.4f | "
                "TREND15m Price: %.4f | "
                "TREND15m SMA20: %.4f | "
                "TREND15m SMA50: %.4f | "
                "TREND15m Bullish: %s | "
                "%s: %.2f | %s: %.4f | "
                "trailBuy: %s | trailSell: %s | "
                "buyEntries: %d | buyArmed: %s"
                % (
                    self.multiSize,
                    self.price,
                    self.last_macd,
                    self.last_signal,
                    self.last_histogram,
                    self.last_rsi,
                    self.sma1,
                    self.sma2,
                    self.atr,
                    self.stopSize,
                    self.trend_price,
                    self.trend_sma1,
                    self.trend_sma2,
                    self.trend_bullish,
                    self.stableName,
                    self.stableCoin,
                    self.cryptoName,
                    self.cryptoCoin,
                    self.trailBuy,
                    self.trailSell,
                    self.buy_entries,
                    self.buy_armed,
                )
            )

            print("********** WALLET [%s] *************"
                  % self.walletType)

            print(
                "TRAIL NEW MULTIPLER [%s]"
                % self.multiSize
            )

            print(
                "Timeframe entry: [%s]"
                % self.timeframe
            )

            print(
                "Timeframe trend: [%s]"
                % self.trend_timeframe
            )

            print("UTC:", now)

            prev_h = (
                "%.5f" % self.previous_histogram
                if self.previous_histogram is not None
                else "None"
            )

            print(
                "  HISTOGRAM: %.5f (%s) - "
                "MACD: %.5f - SIGNAL: %.5f"
                % (
                    self.last_histogram,
                    prev_h,
                    self.last_macd,
                    self.last_signal,
                )
            )

            print(
                "  RSI (%s periodi): %.2f "
                "SMA20: %.2f - SMA50: %.2f"
                % (
                    self.rsi_period,
                    self.last_rsi,
                    self.sma1,
                    self.sma2,
                )
            )

            print(
                "  ATR(%d): %.4f - StopSize adattivo: "
                "%.4f (pavimento: %.4f, x%.1f)"
                % (
                    self.atr_period,
                    self.atr,
                    self.stopSize,
                    self.stop_floor,
                    self.atr_mult,
                )
            )

            print(
                "  TREND %s: Price %.4f | SMA20 %.4f | "
                "SMA50 %.4f | BULLISH=%s"
                % (
                    self.trend_timeframe,
                    self.trend_price,
                    self.trend_sma1,
                    self.trend_sma2,
                    self.trend_bullish,
                )
            )

            print(
                "Last price: %s - Prezzo Medio: %s"
                % (
                    self.price,
                    self.priceMin,
                )
            )

            print(
                "  %s Wallet %s: %.2f - %s: %s [%s]"
                % (
                    self.walletType,
                    self.stableName,
                    self.stableCoin,
                    self.cryptoName,
                    self.cryptoCoin,
                    self.cryptoCoinOrder,
                )
            )

            pw = self.wallet_binance.paper_wallet_dump()

            print(
                "  Paper wallet: entry %.3f | PnL %.2f | "
                "fee %.2f | trade %d"
                % (
                    pw["entry_px"],
                    pw["realized_pnl_usd"],
                    pw["fees_usd"],
                    pw["n_trades"],
                )
            )

            print(
                "  Bot Wallet %s: %.2f - %s: %s [%s]"
                % (
                    self.stableName,
                    self.stableBot,
                    self.cryptoName,
                    self.coinBot,
                    self.cryptoCoinOrder,
                )
            )

            print(
                "BUY: %s - SELL: %s | "
                "BUY entries: %d/%d | armed: %s"
                % (
                    self.trailBuy,
                    self.trailSell,
                    self.buy_entries,
                    self.MAX_BUY_ENTRIES,
                    self.buy_armed,
                )
            )

            print("*********************************************")
            print("")

            # --------------------------------------------------
            # RIARMO SETUP
            # --------------------------------------------------

            self.rearm_buy_setup_if_needed()

            # --------------------------------------------------
            # SEGNALI MACD
            # --------------------------------------------------

            if self.previous_histogram is not None:

                diff = round(
                    self.last_histogram
                    - self.previous_histogram,
                    5,
                )

                # ==============================================
                # AUMENTO POSITIVO
                # ==============================================

                if (
                    self.last_macd > self.last_signal
                    and self.last_histogram
                    > self.previous_histogram
                ):
                    trend = "MACD: Aumento positivo"

                    print(
                        trend,
                        "[no]"
                    )

                # ==============================================
                # DIMINUZIONE POSITIVO -> SELL
                # ==============================================

                elif (
                    self.last_macd > self.last_signal
                    and self.last_histogram
                    < self.previous_histogram
                ):

                    print(
                        "Differenza: %s"
                        % diff
                    )

                    trend = (
                        "MACD: Diminuzione positivo"
                    )

                    print(
                        trend,
                        "[ok]"
                    )

                    if self.last_rsi > 40:

                        print(
                            "RSI positivo [ok]"
                        )

                        if (
                            self.cryptoCoin
                            * self.price
                        ) > 10:

                            print(
                                "Wallet Crypto [ok]"
                            )

                            if (
                                self.price
                                - self.stopSize
                            ) > self.priceMin:

                                print(
                                    "Prezzo medio superato [ok]"
                                )

                                if self.trailSell:

                                    self.data_binance.cronoMacd(
                                        now,
                                        self.last_histogram,
                                        self.last_macd,
                                        self.last_signal,
                                        trend,
                                        self.last_rsi,
                                        "sell",
                                    )

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
                                        self.wallet_binance,
                                        self.data_binance,
                                    )

                                    task.run()

                                    self.wallet()

                                    # Se la posizione è stata
                                    # completamente chiusa,
                                    # il prossimo setup riparte
                                    # da zero.
                                    if self.cryptoCoin <= 0:
                                        self.buy_entries = 0
                                        self.buy_armed = True
                                        self.previous_histogram = None

                                else:
                                    print(
                                        "trailSell False"
                                    )

                            else:
                                print(
                                    "Prezzo medio non superato"
                                )

                        else:
                            print("zero coin")

                    else:
                        print(
                            "RSI negativo [no]"
                        )

                # ==============================================
                # DIMINUZIONE NEGATIVO -> BUY
                # ==============================================

                elif (
                    self.last_macd < self.last_signal
                    and self.last_histogram
                    > self.previous_histogram
                ):

                    print(
                        "Differenza: %s"
                        % diff
                    )

                    trend = (
                        "MACD: Diminuzione negativo"
                    )

                    print(
                        trend,
                        "[ok]"
                    )

                    if self.last_rsi < 60:

                        print(
                            "RSI negativo [ok]"
                        )

                        # --------------------------------------
                        # TREND 15m
                        # --------------------------------------

                        if not self.trend_bullish:

                            print(
                                "TREND FILTER [NO BUY]"
                            )

                            print(
                                "  Price %s: %.4f"
                                % (
                                    self.trend_timeframe,
                                    self.trend_price,
                                )
                            )

                            print(
                                "  SMA20 %s: %.4f"
                                % (
                                    self.trend_timeframe,
                                    self.trend_sma1,
                                )
                            )

                            print(
                                "  SMA50 %s: %.4f"
                                % (
                                    self.trend_timeframe,
                                    self.trend_sma2,
                                )
                            )

                            self.data_binance.cronoMacdString(
                                "BUY BLOCCATO | "
                                "Trend %s non bullish | "
                                "Price %.4f | SMA20 %.4f | "
                                "SMA50 %.4f"
                                % (
                                    self.trend_timeframe,
                                    self.trend_price,
                                    self.trend_sma1,
                                    self.trend_sma2,
                                )
                            )

                        elif self.stableCoin > 10:

                            print(
                                "Wallet Stable >10 [ok]"
                            )

                            # ----------------------------------
                            # RISK MANAGEMENT
                            # ----------------------------------

                            if not self.can_open_buy():
                                pass

                            elif self.trailBuy:

                                self.data_binance.cronoMacd(
                                    now,
                                    self.last_histogram,
                                    self.last_macd,
                                    self.last_signal,
                                    trend,
                                    self.last_rsi,
                                    "buy",
                                )

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
                                    self.wallet_binance,
                                    self.data_binance,
                                )

                                task.run()

                                self.wallet()

                                # Il BUY viene considerato
                                # eseguito solo se la posizione
                                # è effettivamente presente.
                                if (
                                    self.cryptoCoin > 0
                                    and self.priceMin > 0
                                ):
                                    self.buy_entries += 1
                                    self.buy_armed = False

                                    print(
                                        "RISK | BUY entry #%d"
                                        % self.buy_entries
                                    )

                                    self.data_binance.cronoMacdString(
                                        "RISK | BUY entry #%d | "
                                        "entry %.4f | size %.4f"
                                        % (
                                            self.buy_entries,
                                            self.priceMin,
                                            self.cryptoCoin,
                                        )
                                    )

                            else:
                                print(
                                    "trailBuy False"
                                )

                        else:
                            print(
                                "zero stableCoin [no]"
                            )

                    else:
                        print(
                            "RSI positivo [no]"
                        )

                # ==============================================
                # AUMENTO NEGATIVO
                # ==============================================

                elif (
                    self.last_macd < self.last_signal
                    and self.last_histogram
                    < self.previous_histogram
                ):

                    print(
                        "Differenza: %s"
                        % diff
                    )

                    trend = (
                        "MACD: Aumento negativo"
                    )

                    print(
                        trend,
                        "[no]"
                    )

            else:
                trend = "N/A"

            # --------------------------------------------------
            # MEMORIZZA HISTOGRAM
            # --------------------------------------------------

            self.previous_histogram = (
                self.last_histogram
            )

    # ==========================================================
    # WALLET
    # ==========================================================

    def wallet(self):
        self.price = self.data_binance.get_price(
            self.market
        )

        self.cryptoName = self.market
        self.stableName = "USDC"

        if self.real == "y":

            self.walletType = "Real"

            self.stableCoin = (
                self.wallet_binance.get_balance(
                    "USDC"
                )
            )

            self.cryptoCoin = (
                self.wallet_binance.get_balance(
                    self.cryptoName
                )
            )

            self.cryptoCoinOrder = (
                self.wallet_binance.get_balance_order(
                    self.cryptoName
                )
            )

        else:

            self.walletType = "Test"

            self.stableCoin = (
                self.wallet_binance.read_balance(0)
            )

            self.cryptoCoin = (
                self.wallet_binance.read_balance(1)
            )

            self.cryptoCoinOrder = (
                self.wallet_binance.get_balance_order(
                    self.cryptoName
                )
            )

        self.stableBot = round(
            self.stableCoin
            * self.percStable,
            2,
        )

        self.coinBot = round(
            self.cryptoCoin
            * self.percCoin,
            2,
        )

        # Fonte unica del prezzo medio:
        # paper -> entry_px
        # reale -> entryPx Hyperliquid
        self.priceMin = (
            self.wallet_binance.get_entry_price(
                self.market
            )
        )

        # SELL trailing disponibile solo sopra
        # il prezzo medio.
        if (
            self.price > self.priceMin
            and self.cryptoCoin > 0
        ):
            self.trailSell = True
        else:
            self.trailSell = False

        # BUY trailing disponibile se abbiamo
        # almeno 10 USDC investibili.
        if self.stableBot > 10:
            self.trailBuy = True
        else:
            self.trailBuy = False
