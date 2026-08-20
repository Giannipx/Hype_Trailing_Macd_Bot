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


def read_paper_wallet():
    if not os.path.exists("paper_wallet.json"):
        return {}
    with open("paper_wallet.json") as f:
        return json.load(f)


def read_ciclostart():
    if not os.path.exists("fileCicloStart.txt"):
        return 0.0
    with open("fileCicloStart.txt") as f:
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


st.set_page_config(page_title="HYPE Trailing MACD Bot - Dashboard", layout="wide")

args = parse_args()
params = load_params(args.file)
if not params:
    st.error("Parametri non trovati nel file '%s'. Avvio: streamlit run dashboard.py -- --file hype.txt" % args.file)
    st.stop()

symbol = params.get("SYMBOL", "HYPE")
timeframe = params.get("TIMEFRAME", "15m")
real = params.get("REAL", "n")
rete = "MAINNET" if config.NETWORK == "mainnet" else "TESTNET"
modalita = "ORDINI REALI" if real == "y" else "PAPER TRADING"

try:
    hl = Hyperliquid(real=real, market=symbol)
except Exception as e:
    st.error("Errore inizializzazione Hyperliquid: %s" % e)
    st.stop()

bal = read_token_file("fileBalance.txt")
init = read_token_file("walletIniziale.txt")
paper = read_paper_wallet()
price_min = read_ciclostart()

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

# ---------- saldi per token con valore USD ----------
token_rows = []
for tok, amt in bal.items():
    mid = float(mids.get(tok, 0.0)) if tok in mids else None
    usd = amt * mid if mid else None
    token_rows.append({
        "Token": tok,
        "Quantità": amt,
        "Prezzo USD": fmt_price(mid),
        "Valore USD": fmt_usd(usd),
    })

equity = paper.get("cash_usd", bal.get("USDC", 0.0)) + paper.get("sz", 0.0) * (price or 0.0)

# ---------- stato trail inferito (come nel bot) ----------
stable_coin = bal.get("USDC", 0.0)
crypto_coin = bal.get(symbol, 0.0)
stable_bot = round(stable_coin * float(params.get("PERC_STABLE", 0.1)), 2)
coin_bot = round(crypto_coin * float(params.get("PERC_COIN", 1.0)), 4)
trail_sell = price is not None and price > price_min and crypto_coin > 0
trail_buy = stable_bot > 10

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
st.title("HYPE Trailing MACD Bot - Dashboard")
st.caption("%s | %s | Coin: %s | Timeframe: %s" % (modalita, rete, symbol, timeframe))

# ---------- riga metriche principali ----------
c1, c2, c3, c4, c5, c6, c7, c8 = st.columns(8)
c1.metric("Prezzo HYPE", fmt_price(price))
c2.metric("Capitale Paper (USD)", fmt_usd(equity))
c3.metric("USDC", fmt_usd(stable_coin))
c4.metric("HYPE", "%g" % crypto_coin)
c5.metric("PnL realizzato", fmt_usd(paper.get("realized_pnl_usd")))
c6.metric("Fee totali", fmt_usd(paper.get("fees_usd")))
c7.metric("Trade", paper.get("n_trades", 0))
c8.metric("RSI (14)", "%.2f" % last_rsi if last_rsi is not None else "-")

tab_wallet, tab_prezzo, tab_log = st.tabs(["Wallet e saldi", "Prezzo e indicatori", "Attività (log)"])

# ============================================================
# TAB 1 - WALLET E SALDI
# ============================================================
with tab_wallet:
    st.subheader("Bilanci attuali (fileBalance.txt)")
    st.dataframe(pd.DataFrame(token_rows), hide_index=True)
    st.caption("Valore USD calcolato a prezzo live di mercato.")

    col_a, col_b, col_c = st.columns(3)
    with col_a:
        st.subheader("Wallet iniziale (walletIniziale.txt)")
        st.dataframe(pd.DataFrame(list(init.items()), columns=["Token", "Quantità"]), hide_index=True)
    with col_b:
        st.subheader("Wallet paper (paper_wallet.json)")
        paper_rows = [
            {"Campo": "Cash USDC", "Valore": fmt_usd(paper.get("cash_usd"))},
            {"Campo": "Posizione HYPE", "Valore": "%g" % paper.get("sz", 0.0)},
            {"Campo": "Prezzo medio entry", "Valore": fmt_price(paper.get("entry_px"))},
            {"Campo": "PnL realizzato", "Valore": fmt_usd(paper.get("realized_pnl_usd"))},
            {"Campo": "Fee totali", "Valore": fmt_usd(paper.get("fees_usd"))},
            {"Campo": "Trade eseguiti", "Valore": paper.get("n_trades", 0)},
        ]
        st.dataframe(pd.DataFrame(paper_rows), hide_index=True)
    with col_c:
        st.subheader("Stato trail (inferito)")
        trail_rows = [
            {"Stato": "Prezzo minimo (fileCicloStart)", "Valore": fmt_price(price_min)},
            {"Stato": "trailBuy (stableBot > 10)", "Valore": trail_buy},
            {"Stato": "trailSell (prezzo > min e HYPE > 0)", "Valore": trail_sell},
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
        st.subheader("Prezzo HYPE (%s)" % timeframe)
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
    st.subheader("Ultima attività (cronoMacd.txt)")
    log_lines = tail_log(config.CRONO_FILE, n=500)
    if not log_lines:
        st.caption("Nessuna attività registrata.")
    else:
        filtro = st.text_input("Filtra nel log (es. BUY, SELL, RESET, WALLET)", value="")
        righe = [l.rstrip("\n") for l in log_lines]
        if filtro:
            righe = [l for l in righe if filtro.lower() in l.lower()]
        st.code("\n".join(righe) if righe else "(nessuna riga corrisponde al filtro)")