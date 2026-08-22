"""
Backtest offline della strategia HYPE Trailing MACD Bot.

Riusa DIRETTAMENTE (non riscrive a mano):
- indicators.py                    -> stessa matematica MACD/RSI/SMA/ATR
- botMacd.CryptoBot.MAX_POSITION_PCT / MAX_BUY_ENTRIES / MAX_LOSS_PCT
                                    -> stesse costanti di risk management
- config.FEE_PCT                   -> stessa fee simulata
- hl.Hyperliquid.round_size() / usd_to_size()
                                    -> stesso arrotondamento szDecimals

Questo per garantire che il backtest testi DAVVERO la stessa strategia del
bot live, non una sua reinterpretazione — se cambi una soglia in botMacd.py
(es. MAX_LOSS_PCT), il backtest la eredita automaticamente senza bisogno di
essere aggiornato a mano.

Cosa NON può essere replicato esattamente (limite intrinseco dei dati OHLC,
non di questo script):
- StopTrail interroga il prezzo in continuo dal vivo (ogni INTERVAL secondi).
  Qui viene approssimato candela per candela usando high/low: si assume un
  percorso interno low->high sulle candele rialziste (close>=open) e
  high->low su quelle ribassiste. L'ordine VERO dei prezzi dentro una
  candela non è ricavabile dai soli dati OHLC storici — è un'euristica
  standard nei backtest candle-based, non una certezza.
- Mentre un trail (buy o sell) è "attivo", il ciclo MACD del bot live resta
  bloccato in attesa (StopTrail.run() è bloccante) - stessa cosa qui: il
  backtest non valuta nuovi segnali MACD durante un trail in corso, e
  riprende dal punto in cui il trail si è risolto.
- Il calcolo degli indicatori usa una finestra scorrevole delle ultime
  LIMIT=100 candele (stesso valore di default di CryptoBot.limit), non
  l'intero storico: è ESATTAMENTE il comportamento del bot live, che rifà
  ohlcv_data(..., limit=100) e ricalcola tutto da zero ogni ciclo — non un
  compromesso di questo script.

USO:
    python3 backtest.py                     # 1m, 5m, 15m su 30 giorni
    python3 backtest.py --days 7            # 7 giorni
    python3 backtest.py --timeframes 5m,15m # solo alcuni timeframe
    python3 backtest.py --symbol HYPE

Richiede rete verso l'API Hyperliquid (stessa richiesta del bot live).
Non tocca paper_wallet.json / fileCicloStart.txt / cronoMacd.txt del bot
live: il wallet del backtest è interamente in memoria (BacktestWallet).
"""
import argparse
import bisect
import csv
import os
import sys
from datetime import datetime, timedelta, timezone

import config
import indicators
from hl import Hyperliquid
from botMacd import CryptoBot  # solo per le costanti di risk management (parità col bot live)


# Stessa finestra di ricalcolo indicatori del bot live (CryptoBot.__init__
# default limit=100, non sovrascritto da jbmainMacd.py).
LIMIT = 100
RSI_PERIOD = 14
SMA1_PERIOD = 20
SMA2_PERIOD = 50
ATR_PERIOD = 14
ATR_MULT = 1.5
TREND_TIMEFRAME = "15m"

OUTPUT_DIR = "backtest_output"


# ==================================================================
# WALLET SIMULATO (stessa logica di hl.py buy()/sell(), in memoria)
# ==================================================================

class BacktestWallet:
    """Specchio di _paper_default()/buy()/sell()/get_entry_price() in
    hl.py, ma senza toccare file su disco: il backtest deve poter girare
    senza interferire col paper_wallet.json del bot live in esecuzione."""

    def __init__(self, cash_usd, sz=0.0):
        self.cash_usd = cash_usd
        self.sz = sz
        self.entry_px = 0.0
        self.entry_is_real = False
        self.realized_pnl_usd = 0.0
        self.fees_usd = 0.0
        self.n_trades = 0

    def get_entry_price(self):
        # Stessa regola del fix in hl.py: un saldo iniziale marcato a
        # mercato (mai comprato davvero) non deve mai bloccare BUY/SELL.
        if not self.entry_is_real:
            return 0.0
        return self.entry_px

    def buy(self, sz, price):
        if sz <= 0:
            return 0.0
        fee = sz * price * config.FEE_PCT
        old_sz = self.sz
        self.cash_usd -= sz * price + fee
        if old_sz > 0 and self.entry_is_real:
            self.entry_px = ((self.entry_px * old_sz) + (price * sz)) / (old_sz + sz)
        else:
            self.entry_px = price
        self.entry_is_real = True
        self.sz = old_sz + sz
        self.fees_usd += fee
        self.n_trades += 1
        return fee

    def sell(self, sz, price):
        sz = min(sz, self.sz)
        if sz <= 0:
            return 0.0, 0.0
        fee = sz * price * config.FEE_PCT
        realized = (price - self.entry_px) * sz - fee
        self.cash_usd += sz * price - fee
        self.sz -= sz
        if self.sz <= 0:
            self.sz = 0.0
            self.entry_px = 0.0
            self.entry_is_real = False
        self.realized_pnl_usd += realized
        self.fees_usd += fee
        self.n_trades += 1
        return fee, realized

    def equity(self, price):
        return self.cash_usd + self.sz * price


# ==================================================================
# INDICATORI (finestra scorrevole LIMIT, come ohlcv_data live)
# ==================================================================

def compute_cycle_indicators(window_candles):
    """Stesso identico calcolo del blocco 'DATI + INDICATORI' in
    botMacd.py, ma su una finestra già estratta invece che su una fetch
    live. Ritorna None se i dati sono insufficienti, esattamente come il
    bot live farebbe 'continue' quel ciclo."""
    closes = [c[4] for c in window_candles]
    min_richieste = max(SMA2_PERIOD, RSI_PERIOD + 1, ATR_PERIOD + 1, 35)
    if len(closes) < min_richieste:
        return None

    macd_line, signal_line, histogram = indicators.macd_series(closes)
    rsi_value = indicators.rsi(closes, RSI_PERIOD)
    sma1 = indicators.sma(closes, SMA1_PERIOD)
    sma2 = indicators.sma(closes, SMA2_PERIOD)
    atr_value = indicators.atr(window_candles, ATR_PERIOD)

    if (not histogram or not macd_line or not signal_line
            or rsi_value is None or sma1 is None or sma2 is None or atr_value is None):
        return None

    return {
        "macd": macd_line[-1],
        "signal": signal_line[-1],
        "histogram": histogram[-1],
        "rsi": rsi_value,
        "sma1": sma1,
        "sma2": sma2,
        "atr": atr_value,
    }


def trend_bullish_at(trend_candles, trend_close_times, as_of_ms):
    """Stesso identico calcolo di calculate_trend_filter() in botMacd.py,
    ma allineato nel tempo: prende solo le candele 15m già chiuse "as of"
    il momento simulato (as_of_ms), esattamente come farebbe una fetch live
    in quell'istante storico."""
    idx = bisect.bisect_right(trend_close_times, as_of_ms) - 1
    if idx < 0:
        return None, None

    window = trend_candles[max(0, idx - LIMIT + 1): idx + 1]
    closes = [c[4] for c in window]
    min_required = max(SMA1_PERIOD, SMA2_PERIOD, 50)
    if len(closes) < min_required:
        return None, None

    sma1 = indicators.sma(closes, SMA1_PERIOD)
    sma2 = indicators.sma(closes, SMA2_PERIOD)
    if sma1 is None or sma2 is None:
        return None, None

    trend_price = closes[-1]
    bullish = trend_price > sma2 and sma1 > sma2
    return bullish, {"price": trend_price, "sma1": sma1, "sma2": sma2}


# ==================================================================
# RISK MANAGEMENT (stesse costanti di CryptoBot, importate non duplicate)
# ==================================================================

def can_open_buy(wallet, price, buy_entries, buy_armed, perc_stable):
    """Specchio esatto di CryptoBot.can_open_buy()."""
    if buy_entries >= CryptoBot.MAX_BUY_ENTRIES:
        return False
    if not buy_armed:
        return False

    position_value = wallet.sz * price
    equity = wallet.cash_usd + position_value
    max_position = equity * CryptoBot.MAX_POSITION_PCT
    next_buy_value = round(wallet.cash_usd * perc_stable, 2)

    if position_value + next_buy_value > max_position:
        return False

    return True


# ==================================================================
# TRAILING STOP (approssimazione candle-based di StopTrail.update_stop())
# ==================================================================

def simulate_trail(candles, start_idx, kind, stopsize, perc_stable, perc_coin,
                    wallet, hl_util, symbol):
    """Specchio approssimato di StopTrail: dal vivo interroga il prezzo in
    continuo, qui scandisce le candele successive usando high/low. Ritorna
    (exit_idx, filled, info_dict). Se non scatta mai entro i dati
    disponibili, ritorna filled=False (posizione/trail resta pendente a
    fine backtest, viene segnalato nel report finale)."""
    price0 = candles[start_idx][4]

    if kind == "buy":
        stoploss = price0 + stopsize
    else:
        stoploss = price0 - stopsize

    for idx in range(start_idx + 1, len(candles)):
        o, h, l, c = candles[idx][1], candles[idx][2], candles[idx][3], candles[idx][4]
        # Percorso intrabar approssimato (vedi nota limiti in cima al file)
        path = [l, h] if c >= o else [h, l]

        for p in path:
            if kind == "sell":
                if (p - stopsize) > stoploss:
                    stoploss = p - stopsize
                elif p <= stoploss:
                    if p > wallet.get_entry_price():
                        coin_sell = hl_util.round_size(symbol, wallet.sz * perc_coin)
                        if coin_sell <= 0:
                            continue
                        fee, pnl = wallet.sell(coin_sell, p)
                        return idx, True, {
                            "fill_price": p, "fee": fee, "pnl": pnl, "sz": coin_sell,
                        }
                    # prezzo minimo non superato: come dal vivo, resta in attesa
            else:  # buy
                if (p + stopsize) < stoploss:
                    stoploss = p + stopsize
                elif p >= stoploss:
                    stable_buy = round(wallet.cash_usd * perc_stable, 2)
                    amount = hl_util.usd_to_size(symbol, stable_buy, p)
                    if amount <= 0:
                        continue
                    fee = wallet.buy(amount, p)
                    return idx, True, {
                        "fill_price": p, "fee": fee, "sz": amount,
                    }

    return len(candles) - 1, False, {}


# ==================================================================
# LOOP PRINCIPALE (specchio di CryptoBot.run(), su dati storici)
# ==================================================================

def run_backtest(candles, trend_candles, symbol, hl_util,
                  perc_stable, perc_coin, stop_floor, multi_size,
                  start_cash=1000.0):
    trend_close_times = [c[6] for c in trend_candles]

    wallet = BacktestWallet(cash_usd=start_cash, sz=0.0)
    buy_entries = 0
    buy_armed = True
    previous_histogram = None

    trades = []
    equity_curve = []
    pending_trail = None  # se un trail non si risolve mai entro i dati

    i = LIMIT
    n = len(candles)

    while i < n:
        window = candles[max(0, i - LIMIT + 1): i + 1]
        ind = compute_cycle_indicators(window)

        if ind is None:
            i += 1
            continue

        price = candles[i][4]
        close_time_i = candles[i][6]

        atr_value = ind["atr"]
        stop_size = round(max(stop_floor, atr_value * ATR_MULT), 4)

        trend_ok, _trend_info = trend_bullish_at(trend_candles, trend_close_times, close_time_i)
        if trend_ok is None:
            i += 1
            continue

        price_min = wallet.get_entry_price()

        # ---- HARD STOP (controllato ogni ciclo, come check_hard_stop()) ----
        if wallet.sz > 0 and price_min > 0:
            stop_price = price_min * (1.0 - CryptoBot.MAX_LOSS_PCT)
            if price <= stop_price:
                amount = hl_util.round_size(symbol, wallet.sz)
                fee, pnl = wallet.sell(amount, price)
                trades.append({
                    "idx": i, "time": candles[i][0], "type": "HARD_STOP",
                    "price": price, "sz": amount, "fee": fee, "pnl": pnl,
                })
                buy_entries = 0
                buy_armed = True
                previous_histogram = None
                equity_curve.append((candles[i][0], wallet.equity(price)))
                i += 1
                continue

        equity_curve.append((candles[i][0], wallet.equity(price)))

        # ---- RIARMO (rearm_buy_setup_if_needed) ----
        if not buy_armed and previous_histogram is not None and ind["histogram"] < previous_histogram:
            buy_armed = True

        # ---- SEGNALI MACD (stessa identica classificazione di botMacd.py) ----
        if previous_histogram is not None:
            macd_v, signal_v, hist_v = ind["macd"], ind["signal"], ind["histogram"]

            if macd_v > signal_v and hist_v > previous_histogram:
                pass  # AUMENTO POSITIVO: nessuna azione

            elif macd_v > signal_v and hist_v < previous_histogram:
                # DIMINUZIONE POSITIVO -> candidato SELL
                rsi_v = ind["rsi"]
                if (rsi_v > 40 and wallet.sz * price > 10
                        and (price - stop_size) > price_min
                        and price > price_min and wallet.sz > 0):

                    exit_idx, filled, info = simulate_trail(
                        candles, i, "sell", stop_size, perc_stable, perc_coin,
                        wallet, hl_util, symbol,
                    )

                    if filled:
                        trades.append({
                            "idx": exit_idx, "time": candles[exit_idx][0], "type": "SELL",
                            "price": info["fill_price"], "sz": info["sz"],
                            "fee": info["fee"], "pnl": info["pnl"],
                        })
                        if wallet.sz <= 0:
                            buy_entries = 0
                            buy_armed = True
                            previous_histogram = None
                        i = exit_idx + 1
                        continue
                    else:
                        pending_trail = {"type": "sell", "start_idx": i}
                        i = exit_idx + 1
                        continue

            elif macd_v < signal_v and hist_v > previous_histogram:
                # DIMINUZIONE NEGATIVO -> candidato BUY
                rsi_v = ind["rsi"]
                if rsi_v < 60 and trend_ok and wallet.cash_usd > 10:
                    if can_open_buy(wallet, price, buy_entries, buy_armed, perc_stable):
                        exit_idx, filled, info = simulate_trail(
                            candles, i, "buy", stop_size, perc_stable, perc_coin,
                            wallet, hl_util, symbol,
                        )

                        if filled:
                            trades.append({
                                "idx": exit_idx, "time": candles[exit_idx][0], "type": "BUY",
                                "price": info["fill_price"], "sz": info["sz"],
                                "fee": info["fee"], "pnl": None,
                            })
                            if wallet.sz > 0 and wallet.get_entry_price() > 0:
                                buy_entries += 1
                                buy_armed = False
                            i = exit_idx + 1
                            continue
                        else:
                            pending_trail = {"type": "buy", "start_idx": i}
                            i = exit_idx + 1
                            continue

            else:
                pass  # AUMENTO NEGATIVO: nessuna azione

        previous_histogram = ind["histogram"]
        i += 1

    return {
        "wallet": wallet,
        "trades": trades,
        "equity_curve": equity_curve,
        "pending_trail": pending_trail,
    }


# ==================================================================
# REPORT
# ==================================================================

def max_drawdown(equity_curve):
    if not equity_curve:
        return 0.0, 0.0
    peak = equity_curve[0][1]
    max_dd_usd = 0.0
    max_dd_pct = 0.0
    for _, eq in equity_curve:
        if eq > peak:
            peak = eq
        dd = peak - eq
        dd_pct = (dd / peak * 100) if peak > 0 else 0.0
        if dd > max_dd_usd:
            max_dd_usd = dd
        if dd_pct > max_dd_pct:
            max_dd_pct = dd_pct
    return max_dd_usd, max_dd_pct


def print_report(timeframe, days, result, start_cash, last_price):
    wallet = result["wallet"]
    trades = result["trades"]
    equity_curve = result["equity_curve"]

    n_buy = sum(1 for t in trades if t["type"] == "BUY")
    n_sell = sum(1 for t in trades if t["type"] == "SELL")
    n_hard_stop = sum(1 for t in trades if t["type"] == "HARD_STOP")

    sell_trades = [t for t in trades if t["type"] in ("SELL", "HARD_STOP")]
    wins = sum(1 for t in sell_trades if (t["pnl"] or 0) > 0)
    win_rate = (wins / len(sell_trades) * 100) if sell_trades else 0.0

    final_equity = wallet.equity(last_price)
    total_return_pct = ((final_equity - start_cash) / start_cash * 100) if start_cash else 0.0
    dd_usd, dd_pct = max_drawdown(equity_curve)

    print("=" * 70)
    print("BACKTEST %s | ultimi %d giorni" % (timeframe, days))
    print("=" * 70)
    print("Candele valutate:      %d" % len(equity_curve))
    print("Trade totali:          %d (BUY: %d | SELL: %d | HARD STOP: %d)"
          % (len(trades), n_buy, n_sell, n_hard_stop))
    print("Win rate (uscite):     %.1f%% (%d/%d)" % (win_rate, wins, len(sell_trades)))
    print("PnL realizzato:        $%.2f" % wallet.realized_pnl_usd)
    print("Fee totali pagate:     $%.2f" % wallet.fees_usd)
    print("Equity iniziale:       $%.2f" % start_cash)
    print("Equity finale:         $%.2f (posizione aperta: %.4f)" % (final_equity, wallet.sz))
    print("Rendimento totale:     %+.2f%%" % total_return_pct)
    print("Max drawdown:          $%.2f (%.2f%%)" % (dd_usd, dd_pct))
    if result["pending_trail"]:
        print("ATTENZIONE: un trail (%s) non si è mai risolto entro i dati disponibili "
              "(iniziato all'indice %d) — probabile a fine periodo di backtest."
              % (result["pending_trail"]["type"], result["pending_trail"]["start_idx"]))
    print("")

    return {
        "timeframe": timeframe, "days": days,
        "n_trades": len(trades), "n_buy": n_buy, "n_sell": n_sell, "n_hard_stop": n_hard_stop,
        "win_rate_pct": round(win_rate, 2),
        "realized_pnl_usd": round(wallet.realized_pnl_usd, 2),
        "fees_usd": round(wallet.fees_usd, 2),
        "final_equity_usd": round(final_equity, 2),
        "total_return_pct": round(total_return_pct, 2),
        "max_drawdown_usd": round(dd_usd, 2),
        "max_drawdown_pct": round(dd_pct, 2),
    }


def save_trades_csv(path, trades):
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["idx", "time_ms", "time_utc", "type", "price", "sz", "fee", "pnl"])
        for t in trades:
            time_utc = datetime.fromtimestamp(t["time"] / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            writer.writerow([t["idx"], t["time"], time_utc, t["type"],
                              round(t["price"], 4), round(t["sz"], 6),
                              round(t["fee"], 4), round(t["pnl"], 4) if t["pnl"] is not None else ""])


# ==================================================================
# MAIN
# ==================================================================

def main():
    parser = argparse.ArgumentParser(description="Backtest offline HYPE Trailing MACD Bot")
    parser.add_argument("--symbol", default="HYPE")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--timeframes", default="1m,5m,15m")
    parser.add_argument("--perc-stable", type=float, default=0.20)
    parser.add_argument("--perc-coin", type=float, default=0.5)
    parser.add_argument("--stop-floor", type=float, default=0.20)
    parser.add_argument("--multi-size", type=float, default=3)
    parser.add_argument("--start-cash", type=float, default=1000.0)
    args = parser.parse_args()

    timeframes = [t.strip() for t in args.timeframes.split(",") if t.strip()]

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    hl_util = Hyperliquid(real="n", market=args.symbol)  # solo per info/round_size/usd_to_size, nessuna chiave richiesta

    end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ms = int((datetime.now(timezone.utc) - timedelta(days=args.days)).timestamp() * 1000)

    print("Scarico storico 15m (trend filter, condiviso da tutti i timeframe)...")
    trend_candles = hl_util.ohlcv_history(args.symbol, TREND_TIMEFRAME, start_ms, end_ms)
    print("  %d candele 15m" % len(trend_candles))

    summary_rows = []

    for tf in timeframes:
        print("\nScarico storico %s..." % tf)
        if tf == TREND_TIMEFRAME:
            candles = trend_candles
        else:
            candles = hl_util.ohlcv_history(args.symbol, tf, start_ms, end_ms)
        print("  %d candele %s" % (len(candles), tf))

        if len(candles) < LIMIT + 10:
            print("  Dati insufficienti per %s, salto." % tf)
            continue

        result = run_backtest(
            candles, trend_candles, args.symbol, hl_util,
            args.perc_stable, args.perc_coin, args.stop_floor, args.multi_size,
            start_cash=args.start_cash,
        )

        last_price = candles[-1][4]
        summary = print_report(tf, args.days, result, args.start_cash, last_price)
        summary_rows.append(summary)

        csv_path = os.path.join(OUTPUT_DIR, "trades_%s.csv" % tf)
        save_trades_csv(csv_path, result["trades"])
        print("Trade salvati in %s" % csv_path)

    if summary_rows:
        print("\n" + "=" * 70)
        print("RIEPILOGO COMPARATIVO")
        print("=" * 70)
        header = "%-6s %8s %10s %12s %10s %14s %10s" % (
            "TF", "Trade", "WinRate%", "PnL$", "Fee$", "Rendimento%", "MaxDD%")
        print(header)
        for s in summary_rows:
            print("%-6s %8d %10.1f %12.2f %10.2f %14.2f %10.2f" % (
                s["timeframe"], s["n_trades"], s["win_rate_pct"],
                s["realized_pnl_usd"], s["fees_usd"], s["total_return_pct"], s["max_drawdown_pct"],
            ))

        summary_path = os.path.join(OUTPUT_DIR, "summary.csv")
        with open(summary_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(summary_rows[0].keys()))
            writer.writeheader()
            writer.writerows(summary_rows)
        print("\nRiepilogo salvato in %s" % summary_path)


if __name__ == "__main__":
    main()
