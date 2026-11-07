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
    - filtro trend 15m per i BUY (EMA200 + tenuta SMA20 + ADX)
    - ingresso mean-reversion su ipervenduto estremo (RSI < 28)
    - buyStopSize reattivo separato da sell stopSize
    - limite esposizione massima
    - massimo numero di ingressi
    - riarmo del setup BUY
    - take profit parziale + break-even stop + hard stop loss dinamico
    """

    # ==========================================================
    # RISK MANAGEMENT
    # ==========================================================
    MAX_POSITION_PCT = 0.50       # max 50% dell'equity in HYPE
    MAX_BUY_ENTRIES = 2           # max 2 BUY per ciclo di posizione
    MAX_LOSS_PCT = 0.01           # hard stop: pavimento percentuale (usato solo se l'ATR è stretto)
    HARD_STOP_ATR_MULT = 2.0      # hard stop dinamico: ATR * questo moltiplicatore (bilanciato con TP 1.5x)

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
        max_loss_pct=None,
        hard_stop_atr_mult=None,
        adx_threshold=14.0,
        trail_buy_timeout=1200,
        trend_flip_exit="n",
        time_stop_minutes=60.0,
        enable_short="n",
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
        if trend_timeframe in (None, "", "auto", "None"):
            tf_map = {"1m": "15m", "3m": "15m", "5m": "15m", "15m": "1h", "30m": "1h", "1h": "4h", "2h": "4h", "4h": "1d"}
            self.trend_timeframe = tf_map.get(timeframe, "4h" if timeframe == "1h" else "15m")
        else:
            self.trend_timeframe = trend_timeframe
        self.limit = limit

        self.rsi_period = rsi_period
        self.sma1_period = sma1_period
        self.sma2_period = sma2_period

        # ------------------------------------------------------
        # STOPSIZE ADATTIVO (ATR) DIFFERENZIATO BUY / SELL
        # ------------------------------------------------------
        self.stop_floor = stopSize
        self.atr_period = atr_period
        self.atr_mult = atr_mult
        self.atr = None
        self.stopSize = stopSize  # valore iniziale per SELL
        self.buyStopSize = round(stopSize * 0.6, 4)  # valore iniziale più reattivo per BUY
        self.shortStopSize = round(stopSize * 0.6, 4)  # valore iniziale più reattivo per SHORT
        self.enable_short = (str(enable_short).strip().lower() == "y")
        self.position_side = "FLAT"
        self.max_loss_pct = (
            self.MAX_LOSS_PCT
            if max_loss_pct is None
            else max_loss_pct
        )
        self.hard_stop_atr_mult = (
            self.HARD_STOP_ATR_MULT
            if hard_stop_atr_mult is None
            else hard_stop_atr_mult
        )

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
        self.adx_threshold = float(adx_threshold)
        self.trend_sma1 = None
        self.trend_ema200 = None
        self.trend_adx = None
        self.trend_price = None
        self.trend_bullish = False
        self._prev_trend_bullish = False
        self.trend_bearish = False
        self._prev_trend_bearish = False

        # Trailing
        self.trail_buy_timeout = (
            float(trail_buy_timeout)
            if trail_buy_timeout is not None
            else 900.0
        )
        self.trend_flip_exit = str(trend_flip_exit).strip().lower()
        self.time_stop_seconds = float(time_stop_minutes) * 60.0 if time_stop_minutes else 3600.0
        self.entry_time = None
        self.trailBuy = False
        self.trailSell = False
        self.trailShort = False
        self.trailCover = False
        self.take_profit_hit = False

        # Wallet
        self.walletType = "Null"

        # ------------------------------------------------------
        # RISK MANAGEMENT
        # ------------------------------------------------------
        self.buy_entries = 0
        self.buy_armed = True
        self.MAX_BUY_ENTRIES = 1

        self._prompt_reset_paper()
        self.wallet()

        # Ripristino stato runtime salvato (resilienza a crash/riavvio)
        saved_tp, saved_time = self.wallet_binance.load_trade_state()
        if self.position_side != "FLAT":
            self.take_profit_hit = saved_tp
            self.entry_time = saved_time
            self.buy_entries = 1
            self.buy_armed = False
            if self.take_profit_hit:
                print("RIPRISTINO STATO: Take Profit Parziale già eseguito in precedenza. Stop a Break-Even attivo.")

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

        crono_f = getattr(self.wallet_binance, "_get_crono_file", lambda: config.CRONO_FILE)()
        try:
            ans = input(
                f"Vuoi resettare anche la cronologia ({crono_f})? [y/N]: "
            )
        except EOFError:
            ans = ""
        if ans.strip().lower() == "y":
            with open(crono_f, "w"):
                pass
            print(f"Cronologia paper resettata: {crono_f}")

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
            "Trail Stop SELL:   $%.2f (pavimento) "
            "- adattivo via ATR(%d) x%.1f"
            % (
                self.stop_floor,
                self.atr_period,
                self.atr_mult,
            )
        )
        print(
            "Trail Stop BUY:    $%.2f (pavimento) "
            "- adattivo via ATR(%d) x1.0 (timeout %gs)"
            % (
                self.stop_floor * 0.6,
                self.atr_period,
                self.trail_buy_timeout,
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
        print("Filtro RSI:        buy < 55 (sniper < 28) | sell > 40")
        print(
            "Trend filter:      %s Price > EMA200 & >= SMA20 + ADX >= %.1f"
            % (self.trend_timeframe, self.adx_threshold)
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
            "Hard stop:         -%.2f%% (dinamico ATR x%.1f)"
            % (self.max_loss_pct * 100, self.hard_stop_atr_mult)
        )
        print("Modulo SHORT:      %s" % ("attivo (bi-direzionale)" if self.enable_short else "non attivo (solo long)"))
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
            limit=300,
        )

        trend_closes = [c[4] for c in trend_ohlcv]

        min_required = 200
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
        trend_ema200 = indicators.ema(
            trend_closes,
            200,
        )
        trend_adx = indicators.adx(
            trend_ohlcv,
            14,
        )

        if trend_sma1 is None or trend_ema200 is None or trend_adx is None:
            raise ValueError(
                "Indicatori trend non calcolabili sul timeframe %s"
                % self.trend_timeframe
            )

        self.trend_price = trend_closes[-1]
        self.trend_sma1 = trend_sma1
        self.trend_ema200 = trend_ema200
        self.trend_adx = trend_adx

        # Trend Bullish: Prezzo 15m sopra EMA200, ADX in spinta, e prezzo
        # non in fase di crollo sotto la SMA20 15m (tolleranza 1.5% per pullback sani)
        self.trend_bullish = (
            self.trend_price > self.trend_ema200
            and self.trend_price >= (self.trend_sma1 * 0.985)
            and self.trend_adx >= self.adx_threshold
        )

        # Trend Bearish: Prezzo 15m sotto EMA200, ADX in spinta, e prezzo
        # non in fase di spike sopra la SMA20 15m (tolleranza 1.5% per pullback ribassisti sani)
        self.trend_bearish = (
            self.trend_price < self.trend_ema200
            and self.trend_price <= (self.trend_sma1 * 1.015)
            and self.trend_adx >= self.adx_threshold
        )

        return self.trend_bullish

    # ==========================================================
    # HARD STOP LOSS
    # ==========================================================
    def check_hard_stop(self):
        if self.cryptoCoin <= 0:
            return False

        if self.priceMin <= 0:
            return False

        coin_symbol = self.market.split("/")[0]
        max_loss_pct = getattr(self, "max_loss_pct", 0.01)
        hard_stop_atr_mult = getattr(self, "hard_stop_atr_mult", 4.0)
        floor_distance = self.priceMin * max_loss_pct
        atr_distance = (self.atr or 0) * hard_stop_atr_mult
        is_break_even = self.take_profit_hit

        if is_break_even:
            # Sposta l'hard stop a break-even (prezzo di ingresso + 0.20%)
            stop_distance = -(self.priceMin * 0.002)
        else:
            stop_distance = max(floor_distance, atr_distance)

        stop_price = self.priceMin - stop_distance

        if self.price > stop_price:
            return False

        print("")
        print("=" * 60)
        if is_break_even:
            print("!!! BREAK-EVEN STOP (DOPO TAKE PROFIT) !!!")
            print(
                "Prezzo medio:      %.4f"
                % self.priceMin
            )
            print(
                "Prezzo corrente:   %.4f"
                % self.price
            )
            print(
                "Stop Break-even:   %.4f (+0.20%%)"
                % stop_price
            )
        else:
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
                "Stop:              %.4f (-$%.4f, pavimento %.2f%%, ATR x%.1f: $%.4f)"
                % (
                    stop_price,
                    stop_distance,
                    max_loss_pct * 100,
                    hard_stop_atr_mult,
                    atr_distance,
                )
            )
        print(
            "Posizione:         %.4f %s"
            % (self.cryptoCoin, coin_symbol)
        )
        print("=" * 60)

        stop_name = "BREAK-EVEN STOP" if is_break_even else "HARD STOP"

        try:
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
                coin_symbol,
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
                    "%s NON ESEGUITO: %s"
                    % (stop_name, result.get("error"))
                )
                self.data_binance.cronoMacdString(
                    "%s NON ESEGUITO" % stop_name,
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
                "%s | Entry %.4f | "
                "Trigger %.4f | Fill %.4f | "
                "PnL %.4f | Fee %.4f"
                % (
                    stop_name,
                    self.priceMin,
                    stop_price,
                    fill_price,
                    pnl,
                    fee,
                )
            )

            print(
                "%s ESEGUITO | Fill %.4f | "
                "PnL %.4f | Fee %.4f"
                % (
                    stop_name,
                    fill_price,
                    pnl,
                    fee,
                )
            )

            self.buy_entries = 0
            self.buy_armed = True
            self.previous_histogram = None
            self.take_profit_hit = False
            self.entry_time = None
            self.wallet_binance.save_trade_state(False, None)
            self.wallet()

            return True

        except Exception as e:
            print(
                "Errore %s: %s"
                % (stop_name, e)
            )
            self.data_binance.cronoMacdString(
                "ERRORE %s" % stop_name,
                str(e),
            )
            return False

    # ==========================================================
    # TAKE PROFIT PARZIALE
    # ==========================================================
    def check_take_profit(self):
        if self.cryptoCoin <= 0 or self.priceMin <= 0:
            self.take_profit_hit = False
            return False

        if self.take_profit_hit:
            return False

        # Target: 1.5x ATR distance dal prezzo di ingresso
        tp_distance = (self.atr or 0) * 1.5
        tp_price = self.priceMin + tp_distance

        if self.price >= tp_price and tp_distance > 0:
            print("")
            print("=" * 60)
            print("!!! TAKE PROFIT PARZIALE (1.5x ATR) !!!")
            print("Prezzo medio:      %.4f" % self.priceMin)
            print("Prezzo corrente:   %.4f" % self.price)
            print("Target:            %.4f (+%.4f)" % (tp_price, tp_distance))
            print("=" * 60)

            sell_amount = self.wallet_binance.round_size(
                self.market.split("/")[0],
                self.cryptoCoin * 0.5,
            )
            if sell_amount <= 0:
                return False

            try:
                if self.real == "y" and self.stoplossorder == "y":
                    try:
                        self.wallet_binance.elimina_ordine(self.market)
                    except Exception:
                        pass

                result = self.wallet_binance.sell(self.market, sell_amount, self.price)
                if result.get("filled", False):
                    self.take_profit_hit = True
                    self.wallet_binance.save_trade_state(True, self.entry_time)
                    fill_price = result.get("fill_price", self.price)
                    pnl = result.get("pnl", 0.0)
                    self.data_binance.cronoMacdString(
                        "TAKE PROFIT PARZIALE 50%% | Fill %.4f | PnL %.4f" % (fill_price, pnl)
                    )
                    self.wallet()
                    if self.real == "y" and self.stoplossorder == "y" and self.cryptoCoin > 0:
                        be_price = round(self.priceMin * 1.002, 4)
                        self.wallet_binance.crea_ordine_sell_stop(self.market, self.cryptoCoin, be_price, be_price)
                    return True
                else:
                    print("TAKE PROFIT NON ESEGUITO: %s" % result.get("error"))
            except Exception as e:
                print("Errore TAKE PROFIT: %s" % e)
        return False

    # ==========================================================
    # USCITA TECNICA ANTICIPATA (TIME/MOMENTUM INVALIDATION)
    # ==========================================================
    def check_technical_exit(self):
        """
        Uscita tecnica anticipata / invalidazione del trade:
        Se la posizione e aperta da piu di time_stop_seconds (default 60 min),
        il prezzo e sotto il prezzo medio di acquisto (in perdita),
        e il MACD a 5m conferma un deterioramento ribassista (histogram < previous_histogram e last_rsi < 48),
        allora esce a mercato per tagliare la perdita prima dell'Hard Stop.
        """
        if self.cryptoCoin <= 0 or self.priceMin <= 0:
            return False

        if not hasattr(self, "entry_time") or self.entry_time is None:
            return False

        elapsed = time.time() - self.entry_time
        time_limit = getattr(self, "time_stop_seconds", 3600.0)

        is_stagnant_loss = (
            elapsed >= time_limit
            and self.price < self.priceMin
            and self.last_histogram is not None
            and self.previous_histogram is not None
            and self.last_histogram < self.previous_histogram
            and getattr(self, "last_rsi", 50.0) < 48.0
        )

        if not is_stagnant_loss:
            return False

        print("")
        print("=" * 60)
        print("!!! USCITA TECNICA ANTICIPATA (TIME/MOMENTUM INVALIDATION) !!!")
        print("Durata trade:       %.1f min" % (elapsed / 60))
        print("Prezzo medio:       %.4f" % self.priceMin)
        print("Prezzo corrente:    %.4f" % self.price)
        loss_pct = (((self.price - self.priceMin) / self.priceMin) * 100) if self.priceMin > 0 else 0
        print("Perdita parziale:   %.2f%%" % loss_pct)
        print("=" * 60)

        coin_symbol = self.market.split("/")[0]
        try:
            if self.real == "y" and self.stoplossorder == "y":
                try:
                    self.wallet_binance.elimina_ordine(self.market)
                except Exception as e:
                    print("Avviso cancellazione SL: %s" % e)

            amount = self.wallet_binance.round_size(coin_symbol, self.cryptoCoin)
            if amount <= 0:
                return False

            result = self.wallet_binance.sell(self.market, amount, self.price)
            if not result.get("filled", False):
                print("USCITA TECNICA NON ESEGUITA: %s" % result.get("error"))
                return False

            fill_price = result.get("fill_price", self.price)
            pnl = result.get("pnl", 0.0)
            fee = result.get("fee", 0.0)

            self.data_binance.cronoMacdString(
                "USCITA TECNICA | Entry %.4f | Fill %.4f | PnL %.4f | Fee %.4f | Durata %.0fm"
                % (self.priceMin, fill_price, pnl, fee, elapsed / 60)
            )

            self.buy_entries = 0
            self.buy_armed = True
            self.previous_histogram = None
            self.take_profit_hit = False
            self.entry_time = None
            self.wallet_binance.save_trade_state(False, None)
            self.wallet()
            return True

        except Exception as e:
            print("Errore USCITA TECNICA: %s" % e)
            return False

    # ==========================================================
    # HARD STOP LOSS SHORT
    # ==========================================================
    def check_hard_stop_short(self):
        if self.position_side != "SHORT" or self.cryptoCoin <= 0 or self.priceMin <= 0:
            return False

        coin_symbol = self.market.split("/")[0]
        max_loss_pct = getattr(self, "max_loss_pct", 0.01)
        hard_stop_atr_mult = getattr(self, "hard_stop_atr_mult", 2.0)
        floor_distance = self.priceMin * max_loss_pct
        atr_distance = (self.atr or 0) * hard_stop_atr_mult
        is_break_even = self.take_profit_hit

        if is_break_even:
            # Sposta l'hard stop a break-even (prezzo di ingresso - 0.20% per lo short)
            stop_price = self.priceMin * (1.0 - 0.002)
        else:
            stop_price = self.priceMin + max(floor_distance, atr_distance)

        if self.price < stop_price:
            return False

        stop_name = "BREAK-EVEN STOP SHORT" if is_break_even else "HARD STOP SHORT"
        print("")
        print("=" * 60)
        print("!!! %s !!!" % stop_name)
        print("Prezzo medio Short: %.4f" % self.priceMin)
        print("Prezzo corrente:    %.4f" % self.price)
        print("Trigger Stop:       %.4f" % stop_price)
        print("Posizione Short:    %.4f %s" % (self.cryptoCoin, coin_symbol))
        print("=" * 60)

        try:
            if self.real == "y" and self.stoplossorder == "y":
                try:
                    self.wallet_binance.elimina_ordine(self.market)
                except Exception as e:
                    print("Avviso cancellazione SL: %s" % e)

            amount = self.wallet_binance.round_size(coin_symbol, self.cryptoCoin)
            if amount <= 0:
                return False

            result = self.wallet_binance.close_short(self.market, amount, self.price)
            if not result.get("filled", False):
                print("%s NON ESEGUITO: %s" % (stop_name, result.get("error")))
                return False

            fill_price = result.get("fill_price", self.price)
            pnl = result.get("pnl", 0.0)
            fee = result.get("fee", 0.0)

            self.data_binance.cronoMacdString(
                "%s | Entry %.4f | Trigger %.4f | Fill %.4f | PnL %.4f | Fee %.4f"
                % (stop_name, self.priceMin, stop_price, fill_price, pnl, fee)
            )

            self.buy_entries = 0
            self.buy_armed = True
            self.previous_histogram = None
            self.take_profit_hit = False
            self.entry_time = None
            self.wallet_binance.save_trade_state(False, None)
            self.wallet()
            return True

        except Exception as e:
            print("Errore %s: %s" % (stop_name, e))
            return False

    # ==========================================================
    # TAKE PROFIT PARZIALE SHORT
    # ==========================================================
    def check_take_profit_short(self):
        if self.position_side != "SHORT" or self.cryptoCoin <= 0 or self.priceMin <= 0 or self.take_profit_hit:
            return False

        tp_distance = (self.atr or 0) * 1.5
        tp_price = self.priceMin - tp_distance

        if self.price <= tp_price and tp_distance > 0:
            print("")
            print("=" * 60)
            print("!!! TAKE PROFIT PARZIALE SHORT (1.5x ATR) !!!")
            print("Prezzo medio Short: %.4f" % self.priceMin)
            print("Prezzo corrente:    %.4f" % self.price)
            print("Target:             %.4f (-%.4f)" % (tp_price, tp_distance))
            print("=" * 60)

            coin_symbol = self.market.split("/")[0]
            sell_amount = self.wallet_binance.round_size(coin_symbol, self.cryptoCoin * 0.5)
            if sell_amount <= 0:
                return False

            try:
                if self.real == "y" and self.stoplossorder == "y":
                    try:
                        self.wallet_binance.elimina_ordine(self.market)
                    except Exception:
                        pass

                result = self.wallet_binance.close_short(self.market, sell_amount, self.price)
                if result.get("filled", False):
                    self.take_profit_hit = True
                    self.wallet_binance.save_trade_state(True, self.entry_time)
                    fill_price = result.get("fill_price", self.price)
                    pnl = result.get("pnl", 0.0)
                    self.data_binance.cronoMacdString(
                        "TAKE PROFIT PARZIALE SHORT 50%% | Fill %.4f | PnL %.4f" % (fill_price, pnl)
                    )
                    self.wallet()
                    if self.real == "y" and self.stoplossorder == "y" and self.cryptoCoin > 0:
                        be_price = round(self.priceMin * 0.998, 4)
                        self.wallet_binance.crea_ordine_buy_stop(self.market, self.cryptoCoin, be_price, be_price)
                    return True
                else:
                    print("TAKE PROFIT SHORT NON ESEGUITO: %s" % result.get("error"))
            except Exception as e:
                print("Errore TAKE PROFIT SHORT: %s" % e)
        return False

    # ==========================================================
    # USCITA TECNICA ANTICIPATA SHORT
    # ==========================================================
    def check_technical_exit_short(self):
        if self.position_side != "SHORT" or self.cryptoCoin <= 0 or self.priceMin <= 0:
            return False

        if not hasattr(self, "entry_time") or self.entry_time is None:
            return False

        elapsed = time.time() - self.entry_time
        time_limit = getattr(self, "time_stop_seconds", 3600.0)

        is_stagnant_loss = (
            elapsed >= time_limit
            and self.price > self.priceMin
            and self.last_histogram is not None
            and self.previous_histogram is not None
            and self.last_histogram > self.previous_histogram
            and getattr(self, "last_rsi", 50.0) > 52.0
        )

        if not is_stagnant_loss:
            return False

        print("")
        print("=" * 60)
        print("!!! USCITA TECNICA ANTICIPATA SHORT (MOMENTUM INVALIDATION) !!!")
        print("Durata trade Short: %.1f min" % (elapsed / 60))
        print("Prezzo medio Short: %.4f" % self.priceMin)
        print("Prezzo corrente:    %.4f" % self.price)
        loss_pct = (((self.price - self.priceMin) / self.priceMin) * 100) if self.priceMin > 0 else 0
        print("Perdita parziale:   %.2f%%" % loss_pct)
        print("=" * 60)

        coin_symbol = self.market.split("/")[0]
        try:
            if self.real == "y" and self.stoplossorder == "y":
                try:
                    self.wallet_binance.elimina_ordine(self.market)
                except Exception as e:
                    print("Avviso cancellazione SL: %s" % e)

            amount = self.wallet_binance.round_size(coin_symbol, self.cryptoCoin)
            if amount <= 0:
                return False

            result = self.wallet_binance.close_short(self.market, amount, self.price)
            if not result.get("filled", False):
                print("USCITA TECNICA SHORT NON ESEGUITA: %s" % result.get("error"))
                return False

            fill_price = result.get("fill_price", self.price)
            pnl = result.get("pnl", 0.0)
            fee = result.get("fee", 0.0)

            self.data_binance.cronoMacdString(
                "USCITA TECNICA SHORT | Entry %.4f | Fill %.4f | PnL %.4f | Fee %.4f | Durata %.0fm"
                % (self.priceMin, fill_price, pnl, fee, elapsed / 60)
            )

            self.buy_entries = 0
            self.buy_armed = True
            self.previous_histogram = None
            self.take_profit_hit = False
            self.entry_time = None
            self.wallet_binance.save_trade_state(False, None)
            self.wallet()
            return True

        except Exception as e:
            print("Errore USCITA TECNICA SHORT: %s" % e)
            return False

    # ==========================================================
    # BUY RISK FILTER
    # ==========================================================
    def can_open_buy(self):
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

        equity = self.get_equity()
        position_value = (
            self.cryptoCoin * self.price
        ) if self.cryptoCoin > 0 else 0.0
        max_position = (
            equity * self.MAX_POSITION_PCT
        )
        next_buy_value = self.stableBot

        if (
            position_value + next_buy_value
            > max_position + 0.50
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

            tf_sec_map = {
                "5s": 60,
                "1m": 60,
                "3m": 180,
                "5m": 300,
                "15m": 900,
                "30m": 1800,
                "1h": 3600,
                "2h": 7200,
                "4h": 14400,
                "1d": 86400,
            }
            tf_sec = tf_sec_map.get(self.timeframe, 3600)
            now_ts = int(now.timestamp())
            seconds_until_next_time = tf_sec - (now_ts % tf_sec)

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

                self.atr = atr_value
                # StopSize per SELL: più largo (atr_mult, es. 1.8x) per lasciar correre il trend
                self.stopSize = round(
                    max(self.stop_floor, self.atr * self.atr_mult),
                    4,
                )
                # StopSize per BUY: più stretto (1.0x ATR, floor 60%) per entrare sul rimbalzo entro il timeout
                self.buyStopSize = round(
                    max(self.stop_floor * 0.6, self.atr * 1.0),
                    4,
                )
                # StopSize per SHORT: simmetrico a buyStopSize
                self.shortStopSize = self.buyStopSize

                # Trend 15m
                self.calculate_trend_filter()

                # Wallet / prezzo
                self.wallet()

            except Exception as e:
                err_str = str(e)
                if "502" in err_str or "Bad Gateway" in err_str or "<html" in err_str:
                    clean_err = "Hyperliquid API 502 Bad Gateway (temporaneo, riprovo...)"
                elif "503" in err_str or "504" in err_str or "Gateway Time" in err_str:
                    clean_err = "Hyperliquid API 503/504 Timeout (temporaneo, riprovo...)"
                elif "ConnectionError" in err_str or "RemoteDisconnected" in err_str:
                    clean_err = "Disconnessione rete Hyperliquid (riprovo...)"
                else:
                    clean_err = err_str[:120]
                print(
                    "Errore nel ciclo principale (rete/API): %s"
                    % clean_err
                )
                try:
                    self.data_binance.cronoMacdString(
                        "ERRORE ciclo principale: %s"
                        % clean_err
                    )
                except Exception:
                    pass
                continue

            # --------------------------------------------------
            # GESTIONE RISCHIO USCITE LONG E SHORT
            # --------------------------------------------------
            if self.position_side == "LONG":
                if self.check_hard_stop():
                    continue
                if self.check_take_profit():
                    continue
                if self.check_technical_exit():
                    continue
            elif self.position_side == "SHORT":
                if self.check_hard_stop_short():
                    continue
                if self.check_take_profit_short():
                    continue
                if self.check_technical_exit_short():
                    continue

            # --------------------------------------------------
            # TREND FLIP EXIT (opzionale, default "n")
            # --------------------------------------------------
            if self.cryptoCoin > 0 and self.trend_flip_exit == "y":
                should_exit = False
                flip_type = None
                if self.position_side == "LONG" and self.trend_bearish:
                    should_exit = True
                    flip_type = "BEARISH (Price < EMA200)"
                elif self.position_side == "SHORT" and self.trend_bullish:
                    should_exit = True
                    flip_type = "BULLISH (Price > EMA200)"

                if should_exit:
                    print("")
                    print("=" * 60)
                    print("!!! TREND FLIP %s - CHIUSURA POSIZIONE %s !!!" % (flip_type, self.position_side))
                    print(
                        "Trend %s: Price %.4f | SMA20 %.4f | EMA200 %.4f | ADX %.1f"
                        % (
                            self.trend_timeframe,
                            self.trend_price,
                            self.trend_sma1,
                            self.trend_ema200,
                            self.trend_adx,
                        )
                    )
                    print(
                        "Posizione %s: %.4f %s @ entry %.4f"
                        % (
                            self.position_side,
                            self.cryptoCoin,
                            self.cryptoName,
                            self.priceMin,
                        )
                    )
                    print("=" * 60)

                    try:
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

                        if amount > 0:
                            if self.position_side == "LONG":
                                result = self.wallet_binance.sell(
                                    self.market,
                                    amount,
                                    self.price,
                                )
                            else:
                                result = self.wallet_binance.close_short(
                                    self.market,
                                    amount,
                                    self.price,
                                )

                            if not result.get("filled", False):
                                print(
                                    "TREND FLIP EXIT NON ESEGUITO: %s"
                                    % result.get("error")
                                )
                                self.data_binance.cronoMacdString(
                                    "TREND FLIP EXIT NON ESEGUITO",
                                    result.get("error"),
                                )
                            else:
                                fill_price = result.get(
                                    "fill_price",
                                    self.price,
                                )
                                pnl = result.get("pnl", 0.0)
                                fee = result.get("fee", 0.0)

                                self.data_binance.cronoMacdString(
                                    "TREND FLIP EXIT %s | Entry %.4f | "
                                    "Fill %.4f | PnL %.4f | Fee %.4f"
                                    % (
                                        self.position_side,
                                        self.priceMin,
                                        fill_price,
                                        pnl,
                                        fee,
                                    )
                                )
                                print(
                                    "TREND FLIP EXIT ESEGUITO | Fill %.4f | "
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
                        self.take_profit_hit = False
                        self.entry_time = None
                        self.wallet_binance.save_trade_state(False, None)
                        self.wallet()

                    except Exception as e:
                        print("Errore TREND FLIP EXIT: %s" % e)
                        self.data_binance.cronoMacdString(
                            "ERRORE TREND FLIP EXIT", str(e)
                        )

            self._prev_trend_bullish = self.trend_bullish
            self._prev_trend_bearish = self.trend_bearish

            # --------------------------------------------------
            # LOG
            # --------------------------------------------------
            self.data_binance.cronoMacdString(
                "TRAIL MULTIPLER x%s | Price: %s | "
                "MACD: %.5f | Signal: %.5f | "
                "Histo: %.5f | RSI: %.2f | "
                "SMA20: %.2f | SMA50: %.2f | "
                "ATR: %.4f | StopSize: %.4f | BuyStop: %.4f | "
                "TREND%s Price: %.4f | "
                "TREND%s SMA20: %.4f | "
                "TREND%s EMA200: %.4f | "
                "TREND%s ADX: %.1f | "
                "TREND%s Bullish: %s | TREND%s Bearish: %s | "
                "%s: %.2f | %s: %.4f | side: %s | "
                "trailBuy: %s | trailSell: %s | trailShort: %s | trailCover: %s | "
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
                    self.buyStopSize,
                    self.trend_timeframe,
                    self.trend_price,
                    self.trend_timeframe,
                    self.trend_sma1,
                    self.trend_timeframe,
                    self.trend_ema200,
                    self.trend_timeframe,
                    self.trend_adx,
                    self.trend_timeframe,
                    self.trend_bullish,
                    self.trend_timeframe,
                    self.trend_bearish,
                    self.stableName,
                    self.stableCoin,
                    self.cryptoName,
                    self.cryptoCoin,
                    self.position_side,
                    self.trailBuy,
                    self.trailSell,
                    self.trailShort,
                    self.trailCover,
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
                "  ATR(%d): %.4f - SellStop: %.4f (x%.1f) | BuyStop: %.4f (x1.0)"
                % (
                    self.atr_period,
                    self.atr,
                    self.stopSize,
                    self.atr_mult,
                    self.buyStopSize,
                )
            )
            print(
                "  TREND %s: Price %.4f | SMA20 %.4f | "
                "EMA200 %.4f | ADX %.1f | BULLISH=%s | BEARISH=%s"
                % (
                    self.trend_timeframe,
                    self.trend_price,
                    self.trend_sma1,
                    self.trend_ema200,
                    self.trend_adx,
                    self.trend_bullish,
                    self.trend_bearish,
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
                # DIMINUZIONE POSITIVO -> SELL LONG o ENTRY SHORT
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

                    # Uscita LONG
                    if self.position_side == "LONG":
                        if self.last_rsi > 40:
                            print("RSI positivo [ok]")
                            tp_level = self.priceMin + ((self.atr or 0) * 1.5)
                            if self.take_profit_hit and self.price < tp_level:
                                print(
                                    "RUNNER LONG PROTETTO: Prezzo %.4f < Livello TP1 %.4f. "
                                    "Mantengo la posizione protetta dallo stop a Break-Even (+0.20%%)."
                                    % (self.price, tp_level)
                                )
                            elif (self.cryptoCoin * self.price) > 10 and self.trailSell:
                                self.data_binance.cronoMacd(now, self.last_histogram, self.last_macd, self.last_signal, trend, self.last_rsi, "sell")
                                task = StopTrail(
                                    self.market, "sell", self.stopSize, self.interval, self.multiSize,
                                    self.stableBot, self.percCoin, self.real, self.stoplossorder,
                                    self.wallet_binance, self.data_binance,
                                )
                                task.run()
                                self.wallet()
                                if self.cryptoCoin <= 0:
                                    self.buy_entries = 0
                                    self.buy_armed = True
                                    self.previous_histogram = None
                                    self.entry_time = None
                                    self.take_profit_hit = False
                                    self.wallet_binance.save_trade_state(False, None)

                    # Ingresso SHORT (inversione ribassista dopo pullback rialzista)
                    elif self.position_side == "FLAT" and self.enable_short:
                        if self.last_rsi > 45:
                            if not self.trend_bearish:
                                print("TREND FILTER [NO SHORT]")
                                self.data_binance.cronoMacdString(
                                    "SHORT BLOCCATO | Trend %s non bearish | Price %.4f | EMA200 %.4f"
                                    % (self.trend_timeframe, self.trend_price, self.trend_ema200)
                                )
                            elif self.can_open_buy() and self.trailShort:
                                self.data_binance.cronoMacd(now, self.last_histogram, self.last_macd, self.last_signal, trend, self.last_rsi, "short")
                                task = StopTrail(
                                    self.market, "short", self.shortStopSize, self.interval, self.multiSize,
                                    self.stableBot, self.percCoin, self.real, self.stoplossorder,
                                    self.wallet_binance, self.data_binance, max_duration=self.trail_buy_timeout,
                                )
                                task.run()
                                self.wallet()
                                if self.cryptoCoin > 0 and self.priceMin > 0:
                                    self.buy_entries = 1
                                    self.buy_armed = False
                                    self.entry_time = time.time()
                                    self.take_profit_hit = False
                                    self.wallet_binance.save_trade_state(False, self.entry_time)
                                    self.data_binance.cronoMacdString(
                                        "RISK | SHORT entry #%d | entry %.4f | size %.4f"
                                        % (self.buy_entries, self.priceMin, self.cryptoCoin)
                                    )

                # ==============================================
                # DIMINUZIONE NEGATIVO -> BUY LONG o COVER SHORT
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

                    # Uscita SHORT (ricopertura in trailing)
                    if self.position_side == "SHORT":
                        if self.last_rsi < 55:
                            tp_level = self.priceMin - ((self.atr or 0) * 1.5)
                            if self.take_profit_hit and self.price > tp_level:
                                print(
                                    "RUNNER SHORT PROTETTO: Prezzo %.4f > Livello TP1 %.4f. "
                                    "Mantengo la posizione protetta da Break-Even (-0.20%%)."
                                    % (self.price, tp_level)
                                )
                            elif (self.cryptoCoin * self.price) > 10 and self.trailCover:
                                self.data_binance.cronoMacd(now, self.last_histogram, self.last_macd, self.last_signal, trend, self.last_rsi, "cover")
                                task = StopTrail(
                                    self.market, "cover", self.stopSize, self.interval, self.multiSize,
                                    self.stableBot, self.percCoin, self.real, self.stoplossorder,
                                    self.wallet_binance, self.data_binance,
                                )
                                task.run()
                                self.wallet()
                                if self.cryptoCoin <= 0:
                                    self.buy_entries = 0
                                    self.buy_armed = True
                                    self.previous_histogram = None
                                    self.entry_time = None
                                    self.take_profit_hit = False
                                    self.wallet_binance.save_trade_state(False, None)

                    # Ingresso LONG (solo se FLAT)
                    elif self.position_side == "FLAT":
                        if self.last_rsi < 55:
                            print(
                                "RSI negativo [ok]"
                            )

                            # --------------------------------------
                            # TREND 15m OPPURE SNIPER OVERSOLD (<28)
                            # --------------------------------------
                            oversold_reversal = (self.last_rsi < 28.0)
                            if not (self.trend_bullish or oversold_reversal):
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
                                    "  EMA200 %s: %.4f"
                                    % (
                                        self.trend_timeframe,
                                        self.trend_ema200,
                                    )
                                )
                                print(
                                    "  ADX %s: %.1f"
                                    % (
                                        self.trend_timeframe,
                                        self.trend_adx,
                                    )
                                )
                                self.data_binance.cronoMacdString(
                                    "BUY BLOCCATO | "
                                    "Trend %s non bullish | "
                                    "Price %.4f | SMA20 %.4f | "
                                    "EMA200 %.4f | ADX %.1f"
                                    % (
                                        self.trend_timeframe,
                                        self.trend_price,
                                        self.trend_sma1,
                                        self.trend_ema200,
                                        self.trend_adx,
                                    )
                                )

                            elif self.stableCoin > 10:
                                if oversold_reversal and not self.trend_bullish:
                                    print("SNIPER OVERSOLD [RSI %.2f < 28] -> BUY consentito sotto EMA200" % self.last_rsi)
                                    self.data_binance.cronoMacdString(
                                        "SNIPER OVERSOLD ATTIVO | RSI %.2f < 28" % self.last_rsi
                                    )
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
                                        self.buyStopSize,
                                        self.interval,
                                        self.multiSize,
                                        self.stableBot,
                                        self.percCoin,
                                        self.real,
                                        self.stoplossorder,
                                        self.wallet_binance,
                                        self.data_binance,
                                        max_duration=self.trail_buy_timeout,
                                    )
                                    task.run()
                                    self.wallet()

                                    if (
                                        self.cryptoCoin > 0
                                        and self.priceMin > 0
                                    ):
                                        self.buy_entries = 1
                                        self.buy_armed = False
                                        self.entry_time = time.time()
                                        self.take_profit_hit = False
                                        self.wallet_binance.save_trade_state(False, self.entry_time)
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
                    trend = "MACD: Aumento negativo"
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
    # WALLET & EQUITY
    # ==========================================================
    def get_equity(self):
        """Calcola l'Equity totale reale del conto o del paper wallet.
        - REALE: self.stableCoin (get_balance USDC) contiene già 'accountValue'
          da Hyperliquid marginSummary, comprensivo di PnL non realizzato.
          NON va risommato il controvalore della crypto per evitare doppio conteggio.
        - PAPER LONG: cash_usd + (cryptoCoin * price) [il nozionale era stato scalato dal cash]
        - PAPER SHORT: cash_usd + ((entry_px - price) * cryptoCoin) [unrealized pnl da discesa]
        - FLAT: self.stableCoin
        """
        if self.real == "y":
            return float(self.stableCoin)
        current_price = getattr(self, "price", 0.0) or 0.0
        if self.position_side == "SHORT":
            unrealized = ((self.priceMin - current_price) * self.cryptoCoin) if self.priceMin > 0 else 0.0
            return float(self.stableCoin) + unrealized
        elif self.position_side == "LONG":
            return float(self.stableCoin) + (self.cryptoCoin * current_price)
        return float(self.stableCoin)

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

        # Fonte unica del prezzo medio e side della posizione (prima del calcolo equity):
        self.priceMin = (
            self.wallet_binance.get_entry_price(
                self.market
            )
        )
        self.position_side = (
            self.wallet_binance.get_position_side(
                self.market
            )
        )

        # Position Sizing Dinamico basato sul rischio all'Hard Stop
        equity = self.get_equity()
        if hasattr(self, "price") and hasattr(self, "atr") and hasattr(self, "max_loss_pct"):
            risk_amount_usd = equity * self.max_loss_pct

            atr_dist = (self.atr or 0) * self.hard_stop_atr_mult
            floor_dist = self.price * self.max_loss_pct
            stop_dist = max(floor_dist, atr_dist)

            max_allowed = self.wallet_binance.get_withdrawable() * 0.95
            max_position_limit = equity * getattr(self, "MAX_POSITION_PCT", 0.50)
            current_position_value = (self.cryptoCoin * self.price) if self.cryptoCoin > 0 else 0
            remaining_allowance = max(0.0, max_position_limit - current_position_value)

            if stop_dist > 0:
                coins = risk_amount_usd / stop_dist
                target_by_risk = coins * self.price
                # Non superare mai equity * percStable come base per singolo trade
                stable_cap = equity * self.percStable
                target_usd = min(target_by_risk, stable_cap)
                self.stableBot = round(min(target_usd, max_allowed, remaining_allowance), 2)
            else:
                self.stableBot = round(min(equity * self.percStable, max_allowed, remaining_allowance), 2)
        else:
            self.stableBot = round(self.stableCoin * self.percStable, 2)

        coin_symbol = self.market.split("/")[0]
        self.coinBot = self.wallet_binance.round_size(
            coin_symbol,
            self.cryptoCoin * self.percCoin,
        )

        # Gestione stati trailing LONG / SHORT / FLAT
        if self.cryptoCoin > 0 and self.position_side == "LONG":
            self.trailSell = True
            self.trailCover = False
            self.trailBuy = False
            self.trailShort = False
        elif self.cryptoCoin > 0 and self.position_side == "SHORT":
            self.trailCover = True
            self.trailSell = False
            self.trailBuy = False
            self.trailShort = False
        else:
            self.position_side = "FLAT"
            self.trailSell = False
            self.trailCover = False
            self.take_profit_hit = False
            self.entry_time = None
            self.trailBuy = (self.stableBot > 10)
            self.trailShort = (self.stableBot > 10 and self.enable_short)