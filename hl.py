"""
Wrapper Hyperliquid PERPETUAL (niente più Binance spot né coppie SOL/USDT).

Il symbolic del perpetual è SOLO il nome coin (es. "SOL") e il collaterale
è sempre USDC. Mantiene l'interfaccia usata da botMacd.py / trailMacd.py
(get_price, ohlcv_data, wallet, buy/sell, crono*, ecc.).

Modalità:
- real == "n" -> PAPER TRADING: prezzi live da mainnet, wallet simulato
  persistito in paper_wallet.json (cash USDC, posizione szi, entry, PnL
  realizzato). Nessun ordine reale, nessuna chiave richiesta.
- real == "y" -> REALE: Exchange Hyperliquid su perpetual, con LEVERAGE e
  margine (cross/isolated) da config.py.

Attenzione (perp):
- Per APRIRE una posizione long si usa exchange.market_open(is_buy=True).
- Per CHIUDERE si usa exchange.market_close(): l'SDK mette reduce_only da
  solo. market_open(is_buy=False) avrebbe APERTO uno short.
- La size va arrotondata allo szDecimals dell'asset o l'ordine viene rifiutato.
"""
import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo

import eth_account
from hyperliquid.exchange import Exchange
from hyperliquid.info import Info
from hyperliquid.utils import constants

import config


PAPER_WALLET_FILE = "paper_wallet.json"


class Hyperliquid:
    def __init__(self, api_key="", api_secret="", real="n", market=""):
        self.real = real
        self.market = market
        base_url = (
            constants.MAINNET_API_URL if config.NETWORK == "mainnet"
            else constants.TESTNET_API_URL
        )
        self.info = Info(base_url, skip_ws=True)

        self.exchange = None
        self.account_address = config.HL_ACCOUNT_ADDRESS
        self._leverage_set = False

        if real == "y":
            if not config.HL_SECRET_KEY:
                raise ValueError("REAL=y richiede HL_SECRET_KEY in config.py")
            wallet = eth_account.Account.from_key(config.HL_SECRET_KEY)
            self.exchange = Exchange(wallet, base_url, account_address=config.HL_ACCOUNT_ADDRESS)

    # ---- utilità ----
    def _coin(self, market):
        """Nome interno del perpetual: 'SOL/USDC' o 'SOL/USDT' -> 'SOL'."""
        return market.split("/")[0].strip()

    def round_size(self, coin, sz):
        """Arrotonda la size allo szDecimals del perpetual."""
        try:
            asset = self.info.coin_to_asset.get(coin)
            if asset is not None:
                dec = self.info.asset_to_sz_decimals.get(asset, 0)
                return round(sz, dec)
        except Exception:
            pass
        return sz

    def usd_to_size(self, coin, usd, price):
        """Size (roundata allo szDecimals) per un importo usd, tolta la fee."""
        if price <= 0:
            return 0.0
        sz = (usd / price) * (1 - config.FEE_PCT)
        return self.round_size(coin, sz)

    # ---- prezzo e candele ----
    def get_price(self, market):
        coin = self._coin(market)
        mids = self.info.all_mids()
        if coin not in mids:
            raise ValueError(f"coin {coin} non presente in all_mids()")
        return float(mids[coin])

    def ohlcv_data(self, market, timeframe, limit):
        """Ritorna lista [timestamp_ms, open, high, low, close, volume]."""
        interval = self._map_interval(timeframe)
        seconds = {"1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
                   "1h": 3600, "2h": 7200, "4h": 14400}.get(interval, 60)
        now_ms = int(datetime.now().timestamp() * 1000)
        res = self.info.candles_snapshot(
            self._coin(market), interval,
            startTime=now_ms - limit * seconds * 1000, endTime=now_ms,
        )
        out = []
        for c in res:
            out.append([
                int(c.get("t", 0)),
                float(c.get("o", 0)),
                float(c.get("h", 0)),
                float(c.get("l", 0)),
                float(c.get("c", 0)),
                float(c.get("v", 0)),
            ])
        return out

    @staticmethod
    def _map_interval(timeframe):
        # Hyperliquid non ha '5s': il minimo è 1m
        tf = {
            "5s": "1m",
            "1m": "1m",
            "3m": "3m",
            "5m": "5m",
            "15m": "15m",
            "30m": "30m",
            "1h": "1h",
            "2h": "2h",
            "4h": "4h",
        }
        if timeframe not in tf:
            raise ValueError(f"timeframe {timeframe} non supportato su Hyperliquid")
        return tf[timeframe]

    # ---- saldi ----
    def get_balance(self, coin):
        """REALE: 'USDC'/'USDT' -> equity USDC (marginSummary.accountValue);
        altrimenti -> size posizione perpetual (szi). PAPER: legge il wallet JSON."""
        if not self.exchange:
            return self.read_balance(0 if coin in ("USDT", "USDC") else 1)
        address = self.account_address or self.exchange.wallet.address
        try:
            state = self.info.user_state(address)
        except Exception:
            return 0.0
        if coin in ("USDT", "USDC"):
            return float(state.get("marginSummary", {}).get("accountValue", 0.0))
        for ap in state.get("assetPositions", []):
            pos = ap.get("position", {})
            if pos.get("coin") == coin:
                return float(pos.get("szi", 0.0))
        return 0.0

    def get_balance_order(self, coin):
        """Cripto bloccata dagli ordini aperti. I trigger SL non bloccano la
        posizione su HL: perciò 0 (paper e reale)."""
        return 0.0

    # ---- wallet paper (perp: cash USDC + posizione szi) ----
    def _paper_default(self):
        return {
            "cash_usd": config.START_BALANCE_USD,
            "sz": 0.0,
            "entry_px": 0.0,
            "realized_pnl_usd": 0.0,
            "fees_usd": 0.0,
            "n_trades": 0,
        }

    def _load_paper(self):
        if not os.path.exists(PAPER_WALLET_FILE):
            # migrazione dallo stile binario fileBalance.txt se presente
            d = self._paper_default()
            if os.path.exists("fileBalance.txt"):
                try:
                    with open("fileBalance.txt", "r") as f:
                        d["cash_usd"] = float(f.readlines()[0].strip())
                except Exception:
                    pass
            self._save_paper(d)
            return d
        try:
            with open(PAPER_WALLET_FILE, "r") as f:
                return json.load(f)
        except Exception:
            return self._paper_default()

    def _save_paper(self, d):
        with open(PAPER_WALLET_FILE, "w") as f:
            json.dump(d, f, indent=2)

    def read_balance(self, pr):
        d = self._load_paper()
        return float(d["cash_usd"]) if pr == 0 else float(d["sz"])

    def read_ciclostart(self, cc):
        with open("fileCicloStart.txt", "r") as file:
            return float(file.readlines()[cc].strip())

    def write_ciclostart(self, pMin):
        with open("fileCicloStart.txt", "w") as f:
            f.write(str(pMin))
        self.cronoMacdString("Write Ciclostart", pMin)

    # ---- ordini (perp: market_open long / market_close) ----
    def _ensure_leverage(self, coin):
        if self._leverage_set or not self.exchange:
            return
        self.exchange.update_leverage(
            leverage=config.LEVERAGE,
            name=coin,
            is_cross=(config.ISOLATED != "y"),
        )
        self._leverage_set = True

    def buy(self, market, amount, price):
        """APRE (o aggiunge a) una posizione LONG.
        Reale: market_open(is_buy=True) dopo set_leverage.
        Paper: cash -= (size*price + fee), posizione += size."""
        coin = self._coin(market)
        sz = self.round_size(coin, amount)
        try:
            if self.exchange:
                self._ensure_leverage(coin)
                result = self.exchange.market_open(name=coin, is_buy=True, sz=sz, slippage=0.005)
                self.cronoMacdString("BUY reale", coin, sz, result.get("status", "?") if isinstance(result, dict) else result)
                return result
            d = self._load_paper()
            fee = sz * price * config.FEE_PCT
            old_sz = float(d["sz"])
            d["cash_usd"] = float(d["cash_usd"]) - sz * price - fee
            # entry media pesata quando si aggiunge alla posizione
            if old_sz > 0:
                d["entry_px"] = ((float(d["entry_px"]) * old_sz) + (price * sz)) / (old_sz + sz)
            else:
                d["entry_px"] = price
            d["sz"] = old_sz + sz
            d["fees_usd"] = float(d["fees_usd"]) + fee
            self._save_paper(d)
            self.cronoMacdString("PAPER BUY", coin, "sz", sz, "@", price, "fee", round(fee, 4))
            return {"paper": True, "coin": coin, "sz": sz, "fill_price": price, "fee": fee}
        except Exception as e:
            print("Errore BUY Hyperliquid:", e)
            return None

    def sell(self, market, amount, price):
        """CHIUDE la posizione long per l'ammontare richiesto.
        Reale: market_close (reduce_only). Paper: posizione -= size,
        cash += (size*price - fee), pnl realizzato."""
        coin = self._coin(market)
        sz = self.round_size(coin, amount)
        try:
            if self.exchange:
                result = self.exchange.market_close(coin=coin, sz=sz, slippage=0.005)
                self.cronoMacdString("SELL reale", coin, sz, result.get("status", "?") if isinstance(result, dict) else result)
                return result
            d = self._load_paper()
            pos = float(d["sz"])
            sz = min(sz, pos)
            fee = sz * price * config.FEE_PCT
            realized = (price - float(d["entry_px"])) * sz - fee
            d["cash_usd"] = float(d["cash_usd"]) + sz * price - fee
            d["sz"] = pos - sz
            if d["sz"] <= 0:
                d["sz"] = 0.0
                d["entry_px"] = 0.0
            d["realized_pnl_usd"] = float(d["realized_pnl_usd"]) + realized
            d["fees_usd"] = float(d["fees_usd"]) + fee
            d["n_trades"] = int(d["n_trades"]) + 1
            self._save_paper(d)
            self.cronoMacdString("PAPER SELL", coin, "sz", sz, "@", price,
                                 "pnl", round(realized, 4), "fee", round(fee, 4))
            return {"paper": True, "coin": coin, "sz": sz, "fill_price": price, "fee": fee, "pnl": realized}
        except Exception as e:
            print("Errore SELL Hyperliquid:", e)
            return None

    def crea_ordine_sell_stop(self, market, amount, stop_price, limit_price):
        """Trigger sell stop (reduce_only) per chiudere il long se cade.
        Solo reale (parere paper: solo log)."""
        if not self.exchange:
            self.cronoMacdString("PAPER Sell Stop (solo log)", market, amount, stop_price, limit_price)
            return None
        try:
            coin = self._coin(market)
            sz = self.round_size(coin, amount)
            result = self.exchange.order(
                name=coin,
                is_buy=False,
                sz=sz,
                limit_px=float(limit_price),
                order_type={
                    "trigger": {
                        "isMarket": False,
                        "triggerPx": str(stop_price),
                        "tpsl": "sl",
                    }
                },
                reduce_only=True,
            )
            self.cronoMacdString("Creo Sell Stop HL", result)
            return result
        except Exception as e:
            print("Errore Sell Stop Hyperliquid:", e)
            return None

    def _open_orders(self):
        if not self.exchange:
            return []
        try:
            address = self.account_address or self.exchange.wallet.address
            return self.info.open_orders(address)
        except Exception as e:
            print("Errore open_orders Hyperliquid:", e)
            return []

    def elimina_ordine(self, market):
        """Cancella tutti gli ordini aperti sul coin perp (es. SL residuo)."""
        coin = self._coin(market)
        for o in self._open_orders():
            if o.get("coin") == coin:
                try:
                    self.exchange.cancel(name=coin, oid=o["oid"])
                    self.cronoMacdString("Ordine Sell Stop eliminato", coin, o["oid"])
                except Exception as e:
                    print("Errore cancel:", e)

    def verifica_ordini(self, market):
        """Reale: se non c'è più posizione ma restano trigger SL aperti li
        cancella (posizione già chiusa). Paper: no-op."""
        if not self.exchange:
            return
        coin = self._coin(market)
        try:
            address = self.account_address or self.exchange.wallet.address
            state = self.info.user_state(address)
            szi = 0.0
            for ap in state.get("assetPositions", []):
                if ap.get("position", {}).get("coin") == coin:
                    szi = float(ap.get("position", {}).get("szi", 0.0))
            if szi == 0.0:
                self.elimina_ordine(market)
        except Exception as e:
            print("Errore verifica_ordini Hyperliquid:", e)

    # ---- cronologia ----
    def cronoMacd(self, datax, histogramx, macdx, signalx, trendx, rsix, type):
        with open(config.CRONO_FILE, "a+") as fx:
            fx.write("UTC  %s | Histo: %2f | Macd: %2f | Signal: %2f | Trend: %s | Rsi: %s | Tipo: %s\n"
                     % (datax, histogramx, macdx, signalx, trendx, rsix, type))

    def cronoTradeMacd(self, timex, tipo, pair, qty, priced):
        with open(config.CRONO_FILE, "a+") as fx:
            fx.write("UTC  %s | %s | %s | Qty %s | Prezzo %.2f\n" % (timex, tipo, pair, qty, priced))

    def cronoMacdString(self, *args):
        try:
            with open(config.CRONO_FILE, "a") as f:
                now = datetime.now()
                f.write("--> " + now.strftime("%Y-%m-%d %H:%M:%S") + " ")
                print_str = now.strftime("%Y-%m-%d %H:%M:%S") + " "
                for arg in args:
                    f.write(str(arg) + "|")
                    print_str += str(arg) + "|"
                f.write("\n")
                print_str += "\n"
                print(print_str)
        except Exception as e:
            print("Errore cronologia:", e)

    def timeCET(self):
        return datetime.now(ZoneInfo("Europe/Rome")).strftime("%Y-%m-%d %H:%M:%S %Z")