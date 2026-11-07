# -*- coding: utf-8 -*-
"""
Dashboard Streamlit del bot: parametri, wallet/saldi, prezzi, indicatori
MACD/RSI e log attività. Legge gli stessi file runtime del bot e i dati
live da Hyperliquid.

Avvio:
    streamlit run dashboard.py -- --file hype.txt
"""
import argparse
import json
import os
import sys
from datetime import datetime

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

import config
import indicators
from hl import Hyperliquid


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", default="hype.txt", help="File parametri del bot")
    args, _ = parser.parse_known_args(sys.argv[1:])
    return args


def load_params(path):
    """Legge un file KEY=VALUE (come jbmainMacd.py)."""
    params = {}
    if not os.path.exists(path):
        return params
    with open(path) as f:
        for line in f:
            line = line.rstrip("\n")
            if "=" not in line:
                continue
            k, v = line.split("=", 1)
            params[k.strip()] = v.strip()
    return params


def read_token_file(path):
    """Legge un file 'TOKEN: valore' su più righe."""
    tokens = {}
    if not os.path.exists(path):
        return tokens
    with open(path) as f:
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


def read_paper_wallet(fpath="paper_wallet.json"):
    if not os.path.exists(fpath):
        return {}
    with open(fpath) as f:
        return json.load(f)


def read_ciclostart(fpath="fileCicloStart.txt"):
    if not os.path.exists(fpath):
        return 0.0
    with open(fpath) as f:
        line = f.readline().strip()
    try:
        return float(line)
    except ValueError:
        return 0.0


def tail_log(path, n=500):
    if not os.path.exists(path):
        return []
    with open(path) as f:
        lines = f.readlines()
    return lines[-n:]


def fmt_price(v):
    return "%.3f" % v if v is not None else "-"


def fmt_usd(v):
    return "%.2f" % v if v is not None else "-"


args = parse_args()
params = load_params(args.file)
symbol = params.get("SYMBOL", "HYPE") if params else "HYPE"

st.set_page_config(page_title="%s Trailing MACD Bot - Dashboard" % symbol, layout="wide")

# font più compatti per le metriche (utile su schermi piccoli)
st.markdown("""
<style>
[data-testid="stMetricValue"] { font-size: 1.05rem; }
[data-testid="stMetricLabel"] { font-size: 0.72rem; }
[data-testid="stMetric"] { padding: 0.2rem 0.4rem; }
@media (max-width: 640px) {
  [data-testid="stMetricValue"] { font-size: 0.85rem; }
  [data-testid="stMetricLabel"] { font-size: 0.62rem; }
  h1 { font-size: 1.3rem; }
}
</style>
""", unsafe_allow_html=True)

if not params:
    st.error("Parametri non trovati nel file '%s'. Avvio: streamlit run dashboard.py -- --file hype.txt" % args.file)
    st.stop()

timeframe = params.get("TIMEFRAME", "15m")
real = params.get("REAL", "n")
rete = "MAINNET" if config.NETWORK == "mainnet" else "TESTNET"
modalita = "ORDINI REALI" if real == "y" else "PAPER TRADING"

try:
    hl = Hyperliquid(real=real, market=symbol)
except Exception as e:
    st.error("Errore inizializzazione Hyperliquid: %s" % e)
    st.stop()

coin = hl._coin(symbol)
paper_file = hl._get_paper_file()
ciclostart_file = hl._get_ciclostart_file()
crono_file = hl._get_crono_file()

init = read_token_file("walletIniziale.txt")
if real == "y":
    cash_usd = hl.get_balance("USDC")
    sz_pos = hl.get_balance(symbol)
    price_min = hl.get_entry_price(symbol)
    side = hl.get_position_side(symbol)
    bal = {}
else:
    bal = read_paper_wallet(paper_file)
    cash_usd = float(bal.get("cash_usd", 0.0))
    sz_pos = float(bal.get("sz", 0.0))
    price_min = float(bal.get("entry_px", 0.0))
    side = bal.get("side", "FLAT") if sz_pos > 1e-5 else "FLAT"

if sz_pos <= 1e-5:
    side = "FLAT"

# ---------- dati live (prezzi + candele) ----------
mids = {}
price = None
ohlcv = []
try:
    mids = hl.info.all_mids()
    price = float(mids.get(symbol, 0.0)) if symbol in mids else None
    tf = hl._map_interval(timeframe)
    ohlcv = hl.ohlcv_data(symbol, tf, limit=120)
except Exception as e:
    st.warning("Dati live non disponibili: %s" % e)

# ---------- indicatori ----------
closes = [c[4] for c in ohlcv]
macd_line, signal_line, hist = indicators.macd_series(closes)
last_rsi = indicators.rsi(closes, period=14)
sma20 = indicators.sma(closes, 20)
sma50 = indicators.sma(closes, 50)

# ---------- saldo attuale (cash USDC + posizione della coin) ----------
token_rows = []
for tok, amt in [("USDC", cash_usd), (symbol, sz_pos)]:
    mid = float(mids.get(tok, 0.0)) if tok in mids else None
    usd = amt * mid if mid else None
    token_rows.append({
        "Token": tok,
        "Quantità": amt,
        "Prezzo USD": fmt_price(mid),
        "Valore USD": fmt_usd(usd),
    })

if real == "y":
    equity = cash_usd
elif side == "SHORT":
    unrealized = (price_min - (price or 0.0)) * sz_pos if price_min > 0 else 0.0
    equity = cash_usd + unrealized
elif side == "LONG":
    equity = cash_usd + sz_pos * (price or 0.0)
else:
    equity = cash_usd

# ---------- stato trail inferito (come nel bot) ----------
stable_coin = cash_usd
crypto_coin = sz_pos
stable_bot = round(stable_coin * float(params.get("PERC_STABLE", 0.1)), 2)
coin_bot = round(crypto_coin * float(params.get("PERC_COIN", 1.0)), 4)
trail_sell = (side == "LONG" and price is not None and crypto_coin > 0)
trail_cover = (side == "SHORT" and price is not None and crypto_coin > 0)
trail_buy = (side == "FLAT" and stable_bot > 10)
trail_short = (side == "FLAT" and stable_bot > 10 and params.get("ENABLE_SHORT", "n").lower() == "y")

# ============================================================
# SIDEBAR - parametri e auto-refresh
# ============================================================
st.sidebar.header("Parametri bot (%s)" % os.path.basename(args.file))
param_rows = [
    {"Parametro": "Coin", "Valore": symbol},
    {"Parametro": "Timeframe", "Valore": timeframe},
    {"Parametro": "Trail Stop Size", "Valore": "$%.2f" % float(params.get("STOPSIZE", 0))},
    {"Parametro": "Sell Stop Trigger", "Valore": "$%.2f (stopz x %s)" % (
        float(params.get("STOPSIZE", 0)) * float(params.get("MSIZE", 1)), params.get("MSIZE", "1"))},
    {"Parametro": "Intervallo Trail", "Valore": "%ss" % params.get("INTERVAL", "0")},
    {"Parametro": "Invest. Stable", "Valore": "%.0f%%" % (float(params.get("PERC_STABLE", 0)) * 100)},
    {"Parametro": "Vendita Coin", "Valore": "%.0f%%" % (float(params.get("PERC_COIN", 0)) * 100)},
]
st.sidebar.dataframe(pd.DataFrame(param_rows), hide_index=True)

st.sidebar.header("Configurazione (config.py)")
cfg_rows = [
    {"Opzione": "Modalità", "Valore": modalita},
    {"Opzione": "Rete", "Valore": rete},
    {"Opzione": "Leva", "Valore": "%gx (%s)" % (config.LEVERAGE,
        "isolated" if config.ISOLATED == "y" else "cross")},
    {"Opzione": "Fee simulata", "Valore": "%.3f%%" % (config.FEE_PCT * 100)},
    {"Opzione": "Capitale iniziale", "Valore": "$%.2f" % config.START_BALANCE_USD},
    {"Opzione": "StopLoss ordine", "Valore": "attivo" if params.get("STOPLOSS", "n") == "y" else "non attivo"},
]
st.sidebar.dataframe(pd.DataFrame(cfg_rows), hide_index=True)

st.sidebar.header("Aggiornamento")
refresh_secs = st.sidebar.number_input("Auto-refresh (secondi)", min_value=5, max_value=300, value=30, step=5)
st.sidebar.caption("Ultimo aggiornamento: %s" % datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
st.sidebar.button("Aggiorna ora", type="primary")
if refresh_secs > 0:
    components.html("<meta http-equiv='refresh' content='%d'>" % refresh_secs, height=0)

# ============================================================
# HEADER
# ============================================================
st.title("%s Trailing MACD Bot - Dashboard" % coin)
st.caption("%s | %s | Coin: %s | Timeframe: %s" % (modalita, rete, symbol, timeframe))

# ---------- riga metriche principali ----------
c1, c2, c3, c4, c5, c6, c7, c8 = st.columns(8)
c1.metric("Prezzo %s" % coin, fmt_price(price))
c2.metric("Capitale Paper (USD)" if real != "y" else "Equity Conto (USD)", fmt_usd(equity))
c3.metric("USDC", fmt_usd(stable_coin))
c4.metric("Posizione (%s)" % side, "%g %s" % (crypto_coin, symbol))
c5.metric("PnL realizzato", fmt_usd(bal.get("realized_pnl_usd")) if real != "y" else "-")
c6.metric("Fee totali", fmt_usd(bal.get("fees_usd")) if real != "y" else "-")
c7.metric("Trade", bal.get("n_trades", 0) if real != "y" else "-")
c8.metric("RSI (14)", "%.2f" % last_rsi if last_rsi is not None else "-")

tab_wallet, tab_prezzo, tab_log = st.tabs(["Wallet e saldi", "Prezzo e indicatori", "Attività (log)"])

# ============================================================
# TAB 1 - WALLET E SALDI
# ============================================================
with tab_wallet:
    st.subheader("Saldo attuale (%s)" % ("Hyperliquid Reale" if real == "y" else os.path.basename(paper_file)))
    st.dataframe(pd.DataFrame(token_rows), hide_index=True)
    st.caption("Valore USD calcolato a prezzo live di mercato.")

    col_a, col_b, col_c = st.columns(3)
    with col_a:
        st.subheader("Wallet iniziale (walletIniziale.txt)")
        st.dataframe(pd.DataFrame(list(init.items()), columns=["Token", "Quantità"]), hide_index=True)
    with col_b:
        st.subheader("Dettagli wallet paper (%s)" % os.path.basename(paper_file))
        paper_rows = [
            {"Campo": "Cash USDC", "Valore": fmt_usd(bal.get("cash_usd"))},
            {"Campo": "Posizione %s" % coin, "Valore": "%g" % bal.get("sz", 0.0)},
            {"Campo": "Prezzo medio entry", "Valore": fmt_price(price_min)},
            {"Campo": "PnL realizzato", "Valore": fmt_usd(bal.get("realized_pnl_usd"))},
            {"Campo": "Fee totali", "Valore": fmt_usd(bal.get("fees_usd"))},
            {"Campo": "Trade eseguiti", "Valore": bal.get("n_trades", 0)},
        ]
        st.dataframe(pd.DataFrame(paper_rows), hide_index=True)
    with col_c:
        st.subheader("Stato trail (inferito)")
        trail_rows = [
            {"Stato": "Side posizione", "Valore": side},
            {"Stato": "Prezzo medio entry", "Valore": fmt_price(price_min)},
            {"Stato": "trailBuy (Long)", "Valore": trail_buy},
            {"Stato": "trailShort (Short)", "Valore": trail_short},
            {"Stato": "trailSell (Exit Long)", "Valore": trail_sell},
            {"Stato": "trailCover (Exit Short)", "Valore": trail_cover},
            {"Stato": "Stable Bot", "Valore": fmt_usd(stable_bot)},
            {"Stato": "Coin Bot", "Valore": "%g" % coin_bot},
        ]
        st.dataframe(pd.DataFrame(trail_rows), hide_index=True)

# ============================================================
# TAB 2 - PREZZO E INDICATORI
# ============================================================
with tab_prezzo:
    if not ohlcv:
        st.warning("Nessun dato candele disponibile.")
    else:
        idx = pd.to_datetime([c[0] for c in ohlcv], unit="ms")
        st.subheader("Prezzo %s (%s)" % (coin, timeframe))
        st.line_chart(pd.DataFrame(closes, index=idx, columns=["Close"]))

        st.subheader("MACD / Signal / Histogram")
        st.line_chart(pd.DataFrame({
            "MACD": macd_line,
            "Signal": signal_line,
            "Histogram": hist,
        }, index=idx))

        st.subheader("Ultimi valori indicatori")
        ind_rows = [
            {"Indicatore": "MACD", "Valore": fmt_price(macd_line[-1]) if macd_line else "-"},
            {"Indicatore": "Signal", "Valore": fmt_price(signal_line[-1]) if signal_line else "-"},
            {"Indicatore": "Histogram", "Valore": fmt_price(hist[-1]) if hist else "-"},
            {"Indicatore": "RSI (14)", "Valore": "%.2f" % last_rsi if last_rsi is not None else "-"},
            {"Indicatore": "SMA20", "Valore": fmt_price(sma20)},
            {"Indicatore": "SMA50", "Valore": fmt_price(sma50)},
        ]
        st.dataframe(pd.DataFrame(ind_rows), hide_index=True)
        st.caption("Filtro RSI del bot: buy < 60 | sell > 40")

# ============================================================
# TAB 3 - ATTIVITA' (LOG)
# ============================================================
with tab_log:
    st.subheader("Ultima attività (%s)" % os.path.basename(crono_file))
    log_lines = tail_log(crono_file, n=500)
    if not log_lines:
        st.caption("Nessuna attività registrata.")
    else:
        filtro = st.text_input("Filtra nel log (es. BUY, SELL, RESET, WALLET)", value="")
        righe = [l.rstrip("\n") for l in log_lines]
        if filtro:
            righe = [l for l in righe if filtro.lower() in l.lower()]
        st.code("\n".join(righe) if righe else "(nessuna riga corrisponde al filtro)")