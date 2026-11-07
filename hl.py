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
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import eth_account
from hyperliquid.exchange import Exchange
from hyperliquid.info import Info
from hyperliquid.utils import constants

import config


PAPER_WALLET_FILE = "paper_wallet.json"
WALLET_INIZIALE_FILE = "walletIniziale.txt"


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
        self._cached_user_state = None
        self._cached_user_state_time = 0.0

        if real == "y":
            if not config.HL_SECRET_KEY:
                raise ValueError("REAL=y richiede HL_SECRET_KEY in config.py")
            wallet = eth_account.Account.from_key(config.HL_SECRET_KEY)
            self.exchange = Exchange(wallet, base_url, account_address=config.HL_ACCOUNT_ADDRESS)

    def _get_user_state(self, max_age_seconds=1.5):
        """Cache breve (1.5s) dello user_state per evitare chiamate REST ridondanti
        nello stesso ciclo."""
        now = time.time()
        if (
            self._cached_user_state is not None
            and (now - self._cached_user_state_time) < max_age_seconds
        ):
            return self._cached_user_state
        address = self.account_address or self.exchange.wallet.address
        state = self.info.user_state(address)
        self._cached_user_state = state
        self._cached_user_state_time = now
        return state

    # ---- utilità ----
    def _coin(self, market):
        """Nome interno del perpetual: 'SOL/USDC' o 'SOL/USDT' -> 'SOL'."""
        return market.split("/")[0].strip()

    def _coin_clean(self):
        """Nome della coin in minuscolo (es. 'hype', 'sol')."""
        if not self.market:
            return ""
        return self._coin(self.market).strip().lower()

    def _get_paper_file(self):
        c = self._coin_clean()
        if c:
            f = f"paper_wallet_{c}.json"
            if not os.path.exists(f) and c == "hype" and os.path.exists(PAPER_WALLET_FILE):
                return PAPER_WALLET_FILE
            return f
        return PAPER_WALLET_FILE

    def _get_ciclostart_file(self):
        c = self._coin_clean()
        if c:
            f = f"fileCicloStart_{c}.txt"
            if not os.path.exists(f) and c == "hype" and os.path.exists("fileCicloStart.txt"):
                return "fileCicloStart.txt"
            return f
        return "fileCicloStart.txt"

    def _get_bot_state_file(self):
        c = self._coin_clean()
        if c:
            f = f"bot_state_{c}.json"
            if not os.path.exists(f) and c == "hype" and os.path.exists("bot_state.json"):
                return "bot_state.json"
            return f
        return "bot_state.json"

    def _get_crono_file(self):
        c = self._coin_clean()
        if c and c != "hype":
            return f"cronoMacd_{c}.txt"
        if c == "hype":
            if os.path.exists("cronoMacd_hype.txt"):
                return "cronoMacd_hype.txt"
            return config.CRONO_FILE
        return config.CRONO_FILE

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
        """Size (roundata allo szDecimals) per un importo usd.

        FIX: prima si toglieva la fee qui (* (1 - FEE_PCT)) E DI NUOVO in
        buy() (fee = sz*price*FEE_PCT sottratta dal cash) — fee sottratta
        due volte sullo stesso trade. La fee va applicata una sola volta,
        qui sotto in buy()/sell(), non nel dimensionamento della size.
        """
        if price <= 0 or usd <= 0:
            return 0.0
        sz = usd / price
        return self.round_size(coin, sz)

    # ---- prezzo e candele ----
    def get_price(self, market):
        coin = self._coin(market)
        mids = self.info.all_mids()
        if coin not in mids:
            raise ValueError(f"coin {coin} non presente in all_mids()")
        return float(mids[coin])

    def ohlcv_data(self, market, timeframe, limit, closed_only=True):
        """Ritorna lista [timestamp_ms, open, high, low, close, volume].

        FIX: prima si passava endTime=now_ms senza verificare se l'ultima
        candela restituita fosse già chiusa. Con un timeframe di 15m, a metà
        intervallo (es. 18:07 su una candela 18:00-18:15) l'ultima candela è
        ancora in formazione: MACD/RSI/SMA calcolati su di essa cambiano ad
        ogni ciclo e il comportamento live non è più equivalente a un
        eventuale backtest futuro sugli stessi dati storici (che vedrebbe
        solo candele chiuse). closed_only=True (default) scarta l'ultima
        candela se il suo "T" (close time) è ancora nel futuro rispetto ad
        ora; per compensare si richiede una candela in più (fetch_limit).
        """
        interval = self._map_interval(timeframe)
        seconds = {"1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
                   "1h": 3600, "2h": 7200, "4h": 14400, "1d": 86400}.get(interval, 60)
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        fetch_limit = limit + 1 if closed_only else limit
        res = self.info.candles_snapshot(
            self._coin(market), interval,
            startTime=now_ms - fetch_limit * seconds * 1000, endTime=now_ms,
        )
        if closed_only and res:
            close_time = int(res[-1].get("T", 0))
            if close_time == 0 or close_time > now_ms:
                res = res[:-1]
        res = res[-limit:] if limit else res
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

    def ohlcv_history(self, market, timeframe, start_ms, end_ms, page_candles=4500):
        """Scarica uno storico esteso (giorni/settimane), paginando le
        richieste a candles_snapshot. USATO SOLO da backtest.py, non dal
        loop live (ohlcv_data() sopra resta invariato per il bot in
        esecuzione). Ritorna liste di 7 campi (invece dei 6 di ohlcv_data):
        [open_time_ms, o, h, l, c, v, close_time_ms] — il close_time serve
        al backtest per allineare correttamente il timeframe di trend (15m)
        a ogni istante storico simulato, cosa che il bot live non deve fare
        perché lavora sempre sul "adesso".
        """
        interval = self._map_interval(timeframe)
        seconds = {"1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
                   "1h": 3600, "2h": 7200, "4h": 14400, "1d": 86400}.get(interval, 60)

        out = []
        seen_t = set()
        cursor = start_ms
        page_span_ms = page_candles * seconds * 1000

        while cursor < end_ms:
            chunk_end = min(cursor + page_span_ms, end_ms)
            res = self.info.candles_snapshot(
                self._coin(market), interval,
                startTime=cursor, endTime=chunk_end,
            )
            if not res:
                cursor = chunk_end
                continue
            for c in res:
                t = int(c.get("t", 0))
                if t in seen_t:
                    continue
                seen_t.add(t)
                out.append([
                    t,
                    float(c.get("o", 0)),
                    float(c.get("h", 0)),
                    float(c.get("l", 0)),
                    float(c.get("c", 0)),
                    float(c.get("v", 0)),
                    int(c.get("T", 0)),
                ])
            last_t = int(res[-1].get("t", cursor))
            next_cursor = last_t + seconds * 1000
            if next_cursor <= cursor:
                # l'API non sta avanzando: evita un loop infinito
                break
            cursor = next_cursor

        out.sort(key=lambda r: r[0])

        # scarta l'ultima candela se ancora aperta (stessa logica di
        # ohlcv_data closed_only=True)
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        if out and (out[-1][6] == 0 or out[-1][6] > now_ms):
            out = out[:-1]

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
            "1d": "1d",
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
        try:
            state = self._get_user_state()
        except Exception:
            return 0.0
        if coin in ("USDT", "USDC"):
            return float(state.get("marginSummary", {}).get("accountValue", 0.0))
        for ap in state.get("assetPositions", []):
            pos = ap.get("position", {})
            if pos.get("coin") == coin:
                return abs(float(pos.get("szi", 0.0)))
        return 0.0

    def get_withdrawable(self):
        """Ritorna il margine USDC libero/disponibile per nuovi ordini."""
        if not self.exchange:
            d = self._load_paper()
            cash = float(d.get("cash_usd", 0.0))
            if d.get("side") == "SHORT" and float(d.get("sz", 0.0)) > 1e-5:
                entry_notional = float(d.get("entry_px", 0.0)) * float(d.get("sz", 0.0))
                return max(0.0, cash - entry_notional)
            return cash
        try:
            state = self._get_user_state()
            return float(state.get("withdrawable", 0.0))
        except Exception:
            return 0.0

    def get_position_side(self, market=None):
        """Ritorna 'LONG', 'SHORT' o 'FLAT'."""
        coin = self._coin(market or self.market)
        if self.exchange:
            try:
                state = self._get_user_state()
                for ap in state.get("assetPositions", []):
                    pos = ap.get("position", {})
                    if pos.get("coin") == coin:
                        szi = float(pos.get("szi", 0.0))
                        if szi > 1e-5:
                            return "LONG"
                        elif szi < -1e-5:
                            return "SHORT"
                        return "FLAT"
            except Exception:
                return "FLAT"
        d = self._load_paper()
        if float(d.get("sz", 0.0)) <= 1e-5:
            return "FLAT"
        return d.get("side", "LONG")

    def get_balance_order(self, coin):
        """Cripto bloccata dagli ordini aperti. I trigger SL non bloccano la
        posizione su HL: perciò 0 (paper e reale)."""
        return 0.0

    def get_entry_price(self, market):
        """Prezzo medio di ingresso della posizione corrente."""
        coin = self._coin(market)
        if not self.exchange:
            d = self._load_paper()
            if not d.get("entry_is_real", True):
                return 0.0
            return float(d.get("entry_px", 0.0))
        try:
            state = self._get_user_state()
            for ap in state.get("assetPositions", []):
                pos = ap.get("position", {})
                if pos.get("coin") == coin:
                    return float(pos.get("entryPx", 0.0) or 0.0)
        except Exception:
            pass
        return 0.0

    # ---- persistenza stato trade (resilienza al riavvio) ----
    def save_trade_state(self, take_profit_hit, entry_time):
        """Salva take_profit_hit ed entry_time per mantenerli dopo riavvio/crash."""
        if not self.exchange:
            d = self._load_paper()
            d["take_profit_hit"] = bool(take_profit_hit)
            d["entry_time"] = entry_time
            self._save_paper(d)
        else:
            state_file = self._get_bot_state_file()
            tmp = state_file + ".tmp"
            with open(tmp, "w") as f:
                json.dump({"take_profit_hit": bool(take_profit_hit), "entry_time": entry_time}, f)
            os.replace(tmp, state_file)

    def load_trade_state(self):
        """Carica lo stato del trade al riavvio."""
        if not self.exchange:
            d = self._load_paper()
            return bool(d.get("take_profit_hit", False)), d.get("entry_time", None)
        state_file = self._get_bot_state_file()
        if os.path.exists(state_file):
            try:
                with open(state_file) as f:
                    s = json.load(f)
                    return bool(s.get("take_profit_hit", False)), s.get("entry_time", None)
            except Exception:
                pass
        return False, None

    # ---- wallet paper (perp: cash USDC + posizione szi) ----
    def _paper_default(self):
        return {
            "cash_usd": config.START_BALANCE_USD,
            "sz": 0.0,
            "entry_px": 0.0,
            "side": "FLAT",
            "entry_is_real": False,
            "realized_pnl_usd": 0.0,
            "fees_usd": 0.0,
            "n_trades": 0,
            "take_profit_hit": False,
            "entry_time": None,
        }

    def _init_paper(self):
        """Crea il wallet paper partendo dai valori di walletIniziale.txt
        (cash USDC e quantità della coin marcata a mercato)."""
        d = self._paper_default()
        init = self.read_wallet_iniziale()
        d["cash_usd"] = init.get("USDC", config.START_BALANCE_USD)
        coin = self._coin(self.market)
        d["sz"] = init.get(coin, 0.0)
        # le token iniziali vengono marcate a mercato (entry = prezzo attuale)
        # così la prima vendita non registra un PnL "fantasma" a prezzo 0.
        # entry_is_real resta False: questo prezzo NON deve mai bloccare un
        # BUY/SELL, è solo un riferimento contabile per il calcolo del PnL.
        if d["sz"] > 0:
            try:
                d["entry_px"] = self.get_price(self.market)
            except Exception:
                d["entry_px"] = 0.0
        d["entry_is_real"] = False
        self._save_paper(d)
        return d

    def _load_paper(self):
        target = self._get_paper_file()
        if not os.path.exists(target):
            # primo avvio: il wallet paper parte dai valori di walletIniziale.txt
            return self._init_paper()
        try:
            with open(target, "r") as f:
                return json.load(f)
        except Exception:
            # file corrotto o vuoto (es. crash): ricrea da walletIniziale.txt
            return self._init_paper()

    def _save_paper(self, d):
        # FIX: scrittura non atomica. Un crash/kill a metà "json.dump" lasciava
        # paper_wallet corrotto, e _load_paper() in quel caso richiamava
        # silenziosamente _init_paper(), azzerando PnL/trade simulati senza
        # nessun avviso visibile. Ora si scrive su file temporaneo e si fa
        # replace atomico solo a scrittura completata.
        target = self._get_paper_file()
        tmp_path = target + ".tmp"
        with open(tmp_path, "w") as f:
            json.dump(d, f, indent=2)
        os.replace(tmp_path, target)

    def read_wallet_iniziale(self):
        """Quantità iniziali dei token da walletIniziale.txt (riferimento)."""
        tokens = {}
        if not os.path.exists(WALLET_INIZIALE_FILE):
            return tokens
        with open(WALLET_INIZIALE_FILE, "r") as f:
            for line in f:
                line = line.strip()
                if ":" not in line:
                    continue
                tok, val = line.split(":", 1)
                try:
                    tokens[tok.strip()] = float(val.strip())
                except ValueError:
                    pass
        return tokens

    def paper_wallet_dump(self):
        """Ritorna il dict del wallet paper (cash USDC, posizione, entry, PnL...)."""
        return self._load_paper()

    def reset_balance(self):
        """Azzera il wallet paper: riparte dai valori di walletIniziale.txt
        (USDC e quantità della coin) e azzera fileCicloStart.txt. Solo paper:
        in reale non si tocca nulla."""
        if self.real == "y":
            return
        self._init_paper()
        self.write_ciclostart(0.0)
        self.cronoMacdString("PAPER WALLET RESET da walletIniziale.txt")

    def read_balance(self, pr):
        """PAPER: legge il wallet paper (USDC se pr==0, posizione coin se pr==1)."""
        d = self._load_paper()
        return float(d["cash_usd"]) if pr == 0 else float(d["sz"])

    def read_ciclostart(self, cc):
        fpath = self._get_ciclostart_file()
        if not os.path.exists(fpath):
            return 0.0
        with open(fpath, "r") as file:
            lines = file.readlines()
            if not lines or cc >= len(lines):
                return 0.0
            return float(lines[cc].strip())

    def write_ciclostart(self, pMin):
        # FIX: stessa scrittura non atomica di _save_paper. fileCicloStart.txt
        # governa il gate "vendo solo sopra il prezzo medio d'acquisto": se
        # viene letto a metà scrittura (o troncato da un crash), read_ciclostart
        # può sollevare un IndexError su file vuoto e far cadere il bot.
        fpath = self._get_ciclostart_file()
        tmp_path = fpath + ".tmp"
        with open(tmp_path, "w") as f:
            f.write(str(pMin))
        os.replace(tmp_path, fpath)
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

    def _normalize_order_result(self, result, fallback_price):
        """Uniforma la risposta dell'SDK (reale) in un dict con la stessa
        forma del ramo paper.

        FIX: la versione precedente ritornava SEMPRE un dict con
        "fill_price" valorizzato (sul fallback se non trovava avgPx),
        ritornando None solo se `result` non era un dict. Un ordine
        rifiutato da Hyperliquid arriva comunque come dict (con uno stato
        di errore dentro agli statuses), quindi veniva trattato come
        eseguito dal chiamante (trailMacd.py faceva
        `res.get("fill_price", self.price)` e proseguiva scrivendo
        wallet/ciclostart/log come se il trade fosse andato a buon fine).
        Ora si espone esplicitamente "filled": True/False e "error": il
        chiamante DEVE controllare "filled" prima di considerare
        l'operazione eseguita."""
        if not isinstance(result, dict):
            return {"status": "error", "filled": False, "error": "risposta ordine non valida (non-dict)",
                    "coin": self._coin(self.market), "fill_price": fallback_price, "result": result}
        status = result.get("status")
        avg_px = fallback_price
        filled = False
        err_msg = None
        try:
            statuses = result["response"]["data"]["statuses"]
            if statuses and isinstance(statuses[0], dict):
                s0 = statuses[0]
                if "filled" in s0:
                    avg_px = float(s0["filled"].get("avgPx", fallback_price))
                    filled = True
                elif "resting" in s0:
                    # un market order che finisce "resting" invece di essere
                    # eseguito subito non va considerato un fill.
                    err_msg = "ordine resting, non eseguito immediatamente"
                elif "error" in s0:
                    err_msg = str(s0["error"])
            else:
                err_msg = "risposta ordine priva di statuses"
        except (KeyError, TypeError, ValueError, IndexError) as e:
            err_msg = f"risposta ordine non riconosciuta ({e})"
        if status != "ok" and err_msg is None:
            err_msg = f"status={status}"
        return {
            "status": status,
            "filled": filled,
            "error": err_msg,
            "coin": self._coin(self.market),
            "fill_price": float(avg_px),
            "result": result,
        }

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
                self._cached_user_state = None
                norm = self._normalize_order_result(result, self.price if hasattr(self, "price") else price)
                esito = "FILLED" if norm["filled"] else f"NON ESEGUITO ({norm.get('error')})"
                self.cronoMacdString("BUY reale", coin, sz, norm.get("status", "?"), esito)
                return norm
            d = self._load_paper()
            fee = sz * price * config.FEE_PCT
            old_sz = float(d["sz"]) if (d.get("side") == "LONG" and d.get("entry_is_real", True)) else 0.0
            d["cash_usd"] = float(d["cash_usd"]) - sz * price - fee
            if old_sz > 0:
                d["entry_px"] = ((float(d["entry_px"]) * old_sz) + (price * sz)) / (old_sz + sz)
            else:
                d["entry_px"] = price
            d["entry_is_real"] = True
            d["side"] = "LONG"
            d["sz"] = old_sz + sz
            d["fees_usd"] = float(d["fees_usd"]) + fee
            self._save_paper(d)
            self.cronoMacdString("PAPER BUY", coin, "sz", sz, "@", price, "fee", round(fee, 4))
            return {"paper": True, "filled": True, "error": None, "coin": coin, "sz": sz, "fill_price": price, "fee": fee}
        except Exception as e:
            print("Errore BUY Hyperliquid:", e)
            self.cronoMacdString("BUY ERRORE", coin, str(e))
            return {"filled": False, "status": "error", "error": str(e), "fill_price": price}

    def sell(self, market, amount, price):
        """CHIUDE la posizione long per l'ammontare richiesto.
        Reale: market_close (reduce_only). Paper: posizione -= size,
        cash += (size*price - fee), pnl realizzato."""
        coin = self._coin(market)
        sz = self.round_size(coin, amount)
        try:
            if self.exchange:
                result = self.exchange.market_close(coin=coin, sz=sz, slippage=0.005)
                self._cached_user_state = None
                norm = self._normalize_order_result(result, self.price if hasattr(self, "price") else price)
                esito = "FILLED" if norm["filled"] else f"NON ESEGUITO ({norm.get('error')})"
                self.cronoMacdString("SELL reale", coin, sz, norm.get("status", "?"), esito)
                return norm
            d = self._load_paper()
            pos = float(d["sz"])
            sz = min(sz, pos)
            fee = sz * price * config.FEE_PCT
            realized = (price - float(d["entry_px"])) * sz - fee
            d["cash_usd"] = float(d["cash_usd"]) + sz * price - fee
            d["sz"] = round(max(0.0, pos - sz), 6)
            if d["sz"] <= 1e-5:
                d["sz"] = 0.0
                d["entry_px"] = 0.0
                d["entry_is_real"] = False
                d["side"] = "FLAT"
                d["take_profit_hit"] = False
                d["entry_time"] = None
            d["realized_pnl_usd"] = float(d["realized_pnl_usd"]) + realized
            d["fees_usd"] = float(d["fees_usd"]) + fee
            d["n_trades"] = int(d["n_trades"]) + 1
            self._save_paper(d)
            self.cronoMacdString("PAPER SELL", coin, "sz", sz, "@", price,
                                 "pnl", round(realized, 4), "fee", round(fee, 4))
            return {"paper": True, "filled": True, "error": None, "coin": coin, "sz": sz, "fill_price": price, "fee": fee, "pnl": realized}
        except Exception as e:
            print("Errore SELL Hyperliquid:", e)
            self.cronoMacdString("SELL ERRORE", coin, str(e))
            return {"filled": False, "status": "error", "error": str(e), "fill_price": price}

    def open_short(self, market, amount, price):
        """APRE (o aggiunge a) una posizione SHORT.
        Reale: market_open(is_buy=False) dopo set_leverage.
        Paper: cash -= fee, posizione short += size, pnl calcolato su discesa."""
        coin = self._coin(market)
        sz = self.round_size(coin, amount)
        try:
            if self.exchange:
                self._ensure_leverage(coin)
                result = self.exchange.market_open(name=coin, is_buy=False, sz=sz, slippage=0.005)
                self._cached_user_state = None
                norm = self._normalize_order_result(result, self.price if hasattr(self, "price") else price)
                esito = "FILLED" if norm["filled"] else f"NON ESEGUITO ({norm.get('error')})"
                self.cronoMacdString("SHORT reale", coin, sz, norm.get("status", "?"), esito)
                return norm
            d = self._load_paper()
            fee = sz * price * config.FEE_PCT
            old_sz = float(d["sz"]) if (d.get("side") == "SHORT" and d.get("entry_is_real", True)) else 0.0
            if old_sz > 0:
                d["entry_px"] = ((float(d["entry_px"]) * old_sz) + (price * sz)) / (old_sz + sz)
            else:
                d["entry_px"] = price
            d["entry_is_real"] = True
            d["side"] = "SHORT"
            d["sz"] = old_sz + sz
            d["cash_usd"] = float(d["cash_usd"]) - fee
            d["fees_usd"] = float(d["fees_usd"]) + fee
            self._save_paper(d)
            self.cronoMacdString("PAPER SHORT", coin, "sz", sz, "@", price, "fee", round(fee, 4))
            return {"paper": True, "filled": True, "error": None, "coin": coin, "sz": sz, "fill_price": price, "fee": fee}
        except Exception as e:
            print("Errore SHORT Hyperliquid:", e)
            self.cronoMacdString("SHORT ERRORE", coin, str(e))
            return {"filled": False, "status": "error", "error": str(e), "fill_price": price}

    def close_short(self, market, amount, price):
        """CHIUDE la posizione SHORT (ricopertura / Buy to close).
        Reale: market_close (reduce_only).
        Paper: pnl = (entry - price)*sz - fee, aggiunge pnl al cash."""
        coin = self._coin(market)
        sz = self.round_size(coin, amount)
        try:
            if self.exchange:
                result = self.exchange.market_close(coin=coin, sz=sz, slippage=0.005)
                self._cached_user_state = None
                norm = self._normalize_order_result(result, self.price if hasattr(self, "price") else price)
                esito = "FILLED" if norm["filled"] else f"NON ESEGUITO ({norm.get('error')})"
                self.cronoMacdString("COVER SHORT reale", coin, sz, norm.get("status", "?"), esito)
                return norm
            d = self._load_paper()
            pos = float(d["sz"])
            sz = min(sz, pos)
            fee = sz * price * config.FEE_PCT
            realized = (float(d["entry_px"]) - price) * sz - fee
            d["cash_usd"] = float(d["cash_usd"]) + realized
            d["sz"] = round(max(0.0, pos - sz), 6)
            if d["sz"] <= 1e-5:
                d["sz"] = 0.0
                d["entry_px"] = 0.0
                d["entry_is_real"] = False
                d["side"] = "FLAT"
                d["take_profit_hit"] = False
                d["entry_time"] = None
            d["realized_pnl_usd"] = float(d["realized_pnl_usd"]) + realized
            d["fees_usd"] = float(d["fees_usd"]) + fee
            d["n_trades"] = int(d["n_trades"]) + 1
            self._save_paper(d)
            self.cronoMacdString("PAPER COVER SHORT", coin, "sz", sz, "@", price,
                                 "pnl", round(realized, 4), "fee", round(fee, 4))
            return {"paper": True, "filled": True, "error": None, "coin": coin, "sz": sz, "fill_price": price, "fee": fee, "pnl": realized}
        except Exception as e:
            print("Errore COVER SHORT Hyperliquid:", e)
            self.cronoMacdString("COVER SHORT ERRORE", coin, str(e))
            return {"filled": False, "status": "error", "error": str(e), "fill_price": price}

    def crea_ordine_sell_stop(self, market, amount, stop_price, limit_price):
        """Trigger sell stop (reduce_only) per chiudere il long se cade.
        isMarket=True: esecuzione garantita appena il trigger viene toccato
        (accetta eventuale slippage); è più sicuro di un limit su gap veloci.
        Solo reale (in paper: solo log)."""
        if not self.exchange:
            self.cronoMacdString("PAPER Sell Stop (solo log)", market, amount, stop_price, limit_price)
            return None
        try:
            coin = self._coin(market)
            sz = self.round_size(coin, amount)
            stop_px = float(f"{float(stop_price):.5g}")
            # Per trigger market sell, HL richiede comunque un limit_px (es. -3% di slippage max)
            slip_px = float(f"{stop_px * 0.97:.5g}")
            result = self.exchange.order(
                name=coin,
                is_buy=False,
                sz=sz,
                limit_px=slip_px,
                order_type={
                    "trigger": {
                        "isMarket": True,
                        "triggerPx": stop_px,
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

    def crea_ordine_buy_stop(self, market, amount, stop_price, limit_price):
        """Trigger buy stop (reduce_only) per chiudere lo short se il prezzo sale.
        isMarket=True: esecuzione garantita appena il trigger viene toccato.
        Solo reale (in paper: solo log)."""
        if not self.exchange:
            self.cronoMacdString("PAPER Buy Stop (solo log)", market, amount, stop_price, limit_price)
            return None
        try:
            coin = self._coin(market)
            sz = self.round_size(coin, amount)
            stop_px = float(f"{float(stop_price):.5g}")
            # Per trigger market buy, limit_px a +3% di slippage max
            slip_px = float(f"{stop_px * 1.03:.5g}")
            result = self.exchange.order(
                name=coin,
                is_buy=True,
                sz=sz,
                limit_px=slip_px,
                order_type={
                    "trigger": {
                        "isMarket": True,
                        "triggerPx": stop_px,
                        "tpsl": "sl",
                    }
                },
                reduce_only=True,
            )
            self.cronoMacdString("Creo Buy Stop HL", result)
            return result
        except Exception as e:
            print("Errore Buy Stop Hyperliquid:", e)
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
            state = self._get_user_state()
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
        fname = self._get_crono_file()
        with open(fname, "a+") as fx:
            fx.write("UTC  %s | Histo: %2f | Macd: %2f | Signal: %2f | Trend: %s | Rsi: %s | Tipo: %s\n"
                     % (datax, histogramx, macdx, signalx, trendx, rsix, type))

    def cronoTradeMacd(self, timex, tipo, pair, qty, priced):
        fname = self._get_crono_file()
        with open(fname, "a+") as fx:
            fx.write("UTC  %s | %s | %s | Qty %.4f | Prezzo %.3f\n"
                     % (timex.strftime("%Y-%m-%d %H:%M:%S"), tipo, pair, float(qty), float(priced)))

    def cronoMacdString(self, *args):
        try:
            fname = self._get_crono_file()
            with open(fname, "a") as f:
                # FIX: prima datetime.now() (ora locale del sistema, naive),
                # mentre le candele Hyperliquid sono in epoch ms UTC. Log
                # disallineati rispetto ai dati OHLCV rendono più difficile
                # il confronto futuro live/backtest.
                now = datetime.now(timezone.utc)
                f.write("--> " + now.strftime("%Y-%m-%d %H:%M:%S UTC") + " ")
                print_str = now.strftime("%Y-%m-%d %H:%M:%S UTC") + " "
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