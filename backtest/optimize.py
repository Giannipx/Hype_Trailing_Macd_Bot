import argparse
from datetime import datetime, timezone, timedelta
import os
import sys
from pathlib import Path

# Assicura che la directory root del progetto sia in sys.path
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

# Cartella corrente per import di backtest.py
BACKTEST_DIR = Path(__file__).resolve().parent
if str(BACKTEST_DIR) not in sys.path:
    sys.path.insert(0, str(BACKTEST_DIR))

from hl import Hyperliquid
from backtest import run_backtest, resolve_trend_tf, TF_SECONDS


def main():
    parser = argparse.ArgumentParser(description="Grid Search Optimizer per HYPE Bot")
    parser.add_argument("--days", type=int, default=30, help="Giorni di storico da usare (default: 30)")
    parser.add_argument("--timeframe", default="1h", help="Timeframe candele per trade (default: 1h)")
    parser.add_argument("--trend-timeframe", default="auto", help="Timeframe per trend filter (default: auto)")
    parser.add_argument("--enable-short", choices=("y", "n"), default="y", help="Abilita posizioni SHORT (default: y)")
    args = parser.parse_args()

    hl_util = Hyperliquid(real="n", market="HYPE")
    end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ms = int((datetime.now(timezone.utc) - timedelta(days=args.days)).timestamp() * 1000)

    target_trend_tf = resolve_trend_tf(args.timeframe, args.trend_timeframe)
    trend_sec = TF_SECONDS.get(target_trend_tf, 900)
    warmup_ms = 250 * trend_sec * 1000
    trend_start_ms = max(0, start_ms - warmup_ms)

    print("Scaricando dati storici %s (Trend filter)..." % target_trend_tf)
    trend_candles = hl_util.ohlcv_history("HYPE", target_trend_tf, trend_start_ms, end_ms)

    print("Scaricando dati storici %s (Trade)..." % args.timeframe)
    candles = hl_util.ohlcv_history("HYPE", args.timeframe, start_ms, end_ms)

    if not trend_candles or not candles:
        print("Errore nel download dei dati storici.")
        return

    # Parametri base
    symbol = "HYPE"
    perc_stable = 0.20
    perc_coin = 0.50
    stop_floor = 0.18
    multi_size = 2.0
    atr_period = 14
    stoplossorder = "n"
    max_loss_pct = 0.008  # Rischio 0.8%
    start_cash = 1000.0
    enable_short = (args.enable_short == "y")

    print(
        f"\nInizio Ottimizzazione (Timeframe {args.timeframe}, Trend {target_trend_tf}, "
        f"{args.days} giorni, SHORT={args.enable_short})..."
    )
    results = []

    # Griglia dei parametri da testare
    atr_mults = [1.5, 1.8, 2.0, 2.5, 3.0]
    hard_stop_atr_mults = [2.5, 3.0, 3.5, 4.0, 5.0]

    for am in atr_mults:
        for hsam in hard_stop_atr_mults:
            if am >= hsam:
                continue  # Il trailing stop (am) deve essere più stretto dell'hard stop (hsam)

            res = run_backtest(
                candles, trend_candles, symbol, hl_util,
                perc_stable, perc_coin, stop_floor, multi_size,
                atr_period, am, stoplossorder, max_loss_pct,
                hsam, timeframe=args.timeframe, start_cash=start_cash,
                enable_short=enable_short,
            )

            wallet = res["wallet"]
            trades = res["trades"]

            # Calcolo Equity e PnL finale con metodo allineato
            final_equity = wallet.equity(candles[-1][4])
            pnl = final_equity - start_cash

            # Trade di uscita conclusi con PnL
            uscite = [t for t in trades if t.get("pnl") is not None]
            wins = sum(1 for t in uscite if (t.get("pnl") or 0) > 0)
            win_rate = (wins / len(uscite) * 100) if uscite else 0.0

            results.append({
                "atr_mult": am,
                "hs_atr_mult": hsam,
                "pnl": pnl,
                "trades": len(uscite),
                "win_rate": win_rate,
            })
            print(
                f"ATR {am:.1f}x | HardStop {hsam:.1f}x -> PnL: ${pnl:+.2f} "
                f"({len(uscite)} uscite, WinRate: {win_rate:.1f}%)"
            )

    print("\n" + "=" * 60)
    print("MIGLIORI 5 SETUP (Ordinati per PnL)")
    print("=" * 60)
    results.sort(key=lambda x: x["pnl"], reverse=True)
    for i, r in enumerate(results[:5]):
        print(
            f"{i+1}. ATR_MULT {r['atr_mult']:.1f}x | HARD_STOP_ATR_MULT {r['hs_atr_mult']:.1f}x "
            f"--> PnL: ${r['pnl']:+.2f} ({r['trades']} trades, WR: {r['win_rate']:.1f}%)"
        )


if __name__ == "__main__":
    main()
