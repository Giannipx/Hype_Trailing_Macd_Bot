# AGENTS.md

Python 3.9 trading bot for **Hyperliquid perpetuals** (adapted from a Binance spot bot). No tests or linter; comment and log strings are in **Italian** — keep them that way.

## Run

```
python jbmainMacd.py --file hype.txt
```

- The bot loops forever on MACD/RSI signals; `StopTrail` (trailMacd.py) follows price every `INTERVAL` seconds until its stop is touched, protected by timeout (`TRAIL_BUY_TIMEOUT`) and breakdown invalidation. Supports 4 actions: `buy`, `sell`, `short`, and `cover`.
- Bi-directional SHORT module: enabled via `ENABLE_SHORT=y` in param file. When active, opens short positions during bearish trends (`Price < EMA200 and Price <= SMA20 * 1.015 and ADX >= threshold`) on MACD `Diminuzione Positivo` and covers on `Diminuzione Negativo`.
- Risk management in `botMacd.py`: partial take profit (50% at 1.5x ATR, trailing stop shifted to break-even: +0.20% for long, -0.20% for short) and dynamic ATR hard stop (`HARD_STOP_ATR_MULT`). `TREND_FLIP_EXIT` is optional and disabled by default (`n`). Rounding buffer ($0.50) in `can_open_buy` prevents float lockout.

## Live vs paper trading (`config.py`)

- `REAL=n` (default): paper trading — live mainnet prices, simulated `paper_wallet.json` balance. No keys required.
- `REAL=y`: real orders on mainnet; requires `HL_ACCOUNT_ADDRESS` + `HL_SECRET_KEY`. These are read from the environment via `python-dotenv` (`config.py` calls `load_dotenv()`): put them in a local `.env` (gitignored, see `.env.example`) — never hardcode them in `config.py`, which is committed. `NETWORK` supports `mainnet`/`testnet`.
- `LEVERAGE`-equivalent is `LEVERAGE`; `ISOLATED`="y" → isolated margin, else cross.
- Paper state is gitignored in `paper_wallet.json` (was untracked, kept out of git). At startup the bot asks whether to reset the paper wallet from `walletIniziale.txt` (`_prompt_reset_paper` / `reset_balance` in `botMacd.py`/`hl.py`); `fileCicloStart.txt` and `cronoMacd.txt` are tracked runtime files. `fileCicloStart.txt` stores the buy price that gates trailing sells (`write_ciclostart`, atomic tmp+`os.replace`).
- On the real branch of `buy()`/`sell()`, the SDK response is normalized into `{"coin", "sz", "fill_price", "status"}` by `_normalize_order_result` (extracts `avgPx` from `response.data.statuses`, fallback to the pre-order price) so `trailMacd.py` gets a consistent `fill_price` dict like the paper branch.

## Hyperliquid wrapper gotchas (`hl.py`)

- Perpetual symbol is **coin name only** (e.g. `HYPE`, not `HYPE/USDT`); `hl.py` strips everything before `/`.
- Hyperliquid has no `5s` candle: `5s` maps to `1m`.
- Open a long with `market_open(is_buy=True)`; open a short with `market_open(is_buy=False)`.
- **Close** any position (long or short) with `market_close()` (SDK sets reduce_only). Never use `market_open` to close positions.
- Position tracking: `get_position_side()` returns `"LONG"`, `"SHORT"`, or `"FLAT"`. Epsilon cleaning (`sz <= 1e-5`) ensures positions fully reset to `"FLAT"`.
- Order size must be rounded to the asset's `szDecimals` or the order is rejected (`round_size` / `usd_to_size`).
- Sell-stop order (`crea_ordine_sell_stop`) is real-only and uses `isMarket: True` (guaranteed fill, some slippage); in paper mode it just logs.
- `StopTrail` receives the already-created `wallet_binance`/`data_binance` instances from `CryptoBot` (`wallet_instance`/`data_instance`) so `_leverage_set` is effective and no extra `Info` clients are opened per trade.
- Reused library: `hyperliquid-python-sdk` + `eth-account` + `python-dotenv` (see `requirements.txt`).

## Code layout

- `jbmainMacd.py` — entry point and params parsing (`ENABLE_SHORT`, etc.).
- `botMacd.py` — main MACD/RSI signal loop, decides buy/sell/short/cover, gates on `trailBuy`/`trailSell`/`trailShort`/`trailCover`.
- `trailMacd.py` — trailing stop execution after a signal fires (`buy`, `sell`, `short`, `cover`).
- `indicators.py` — pure-Python MACD/RSI/SMA/ADX (no pandas/ta).
- `config.py` — all trading/config knobs.
- `hl.py` — single wrapper around Hyperliquid (balance, candles, orders, logging, paper wallet).
- `backtest.py` — offline backtester supporting long-only and bi-directional trading, dynamic ATR stop, partial TP, and multi-timeframe analysis.
- `dashboard.py` — optional Streamlit dashboard: `streamlit run dashboard.py -- --file hype.txt` (needs `streamlit`, added in `requirements.txt`); reads the same runtime files and live Hyperliquid data.