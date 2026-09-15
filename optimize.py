import argparse
from datetime import datetime, timezone, timedelta
import os
import sys

# Assicuriamoci che python trovi i moduli
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from hl import Hyperliquid
from backtest import run_backtest

def main():
    parser = argparse.ArgumentParser(description="Grid Search Optimizer per HYPE Bot")
    parser.add_argument("--days", type=int, default=30, help="Giorni di storico da usare")
    args = parser.parse_args()

    hl_util = Hyperliquid(real="n", market="HYPE")
    end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ms = int((datetime.now(timezone.utc) - timedelta(days=args.days)).timestamp() * 1000)

    print("Scaricando dati storici 15m (Trend)...")
    trend_candles = hl_util.ohlcv_history("HYPE", "15m", start_ms, end_ms)
    
    print("Scaricando dati storici 5m (Trade)...")
    candles_5m = hl_util.ohlcv_history("HYPE", "5m", start_ms, end_ms)

    if not trend_candles or not candles_5m:
        print("Errore nel download dei dati storici.")
        return

    # Parametri fissi
    symbol = "HYPE"
    perc_stable = 0.15 # Fallback se max_loss non interviene
    perc_coin = 1.0
    stop_floor = 0.35
    multi_size = 2.0
    atr_period = 14
    stoplossorder = "n"
    max_loss_pct = 0.01  # Rischio 1%
    start_cash = 1000.0

    print(f"\nInizio Ottimizzazione (Timeframe 5m, {args.days} giorni)...")
    results = []

    # Griglia dei parametri da testare
    atr_mults = [1.5, 2.0, 2.5, 3.0, 3.5]
    hard_stop_atr_mults = [2.0, 3.0, 4.0, 5.0, 6.0]

    for am in atr_mults:
        for hsam in hard_stop_atr_mults:
            if am > hsam:
                continue # Il trailing stop (am) deve essere più stretto dell'hard stop (hsam)
                
            res = run_backtest(
                candles_5m, trend_candles, symbol, hl_util,
                perc_stable, perc_coin, stop_floor, multi_size,
                atr_period, am, stoplossorder, max_loss_pct,
                hsam, start_cash=start_cash
            )
            
            wallet = res["wallet"]
            trades = res["trades"]
            
            # Calcolo PnL finale
            final_equity = wallet.cash_usd + (wallet.sz * candles_5m[-1][4])
            pnl = final_equity - start_cash
            
            # Quanti trade reali sono stati fatti (uscite e target)
            uscite = [t for t in trades if t["type"] in ["SELL", "HARD_STOP", "TAKE_PROFIT_PARZIALE", "STOP_LOSS"]]
            
            results.append({
                "atr_mult": am,
                "hs_atr_mult": hsam,
                "pnl": pnl,
                "trades": len(uscite)
            })
            print(f"ATR {am:.1f}x | HardStop {hsam:.1f}x -> PnL: ${pnl:+.2f} ({len(uscite)} trade uscite)")
            
    print("\n" + "="*50)
    print("MIGLIORI 5 SETUP (Ordina per PnL)")
    print("="*50)
    results.sort(key=lambda x: x["pnl"], reverse=True)
    for i, r in enumerate(results[:5]):
         print(f"{i+1}. ATR_MULT {r['atr_mult']:.1f}x | HARD_STOP_ATR_MULT {r['hs_atr_mult']:.1f}x --> PnL: ${r['pnl']:+.2f} ({r['trades']} trades)")

if __name__ == "__main__":
    main()
