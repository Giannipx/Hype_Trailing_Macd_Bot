# ============================================================
# CONFIG - ADATTAMENTO HYPERLIQUID
# (il bot originale era per Binance; ora usa hyperliquid-python-sdk)
# ============================================================

import os
from dotenv import load_dotenv

# Le chiavi reali vanno nel file .env (escluso da git). Nessuna chiave
# o segreto deve stare in questo file versionato.
load_dotenv()

# Modalità di default: TEST (= paper trading). Nessuna chiave richiesta.
# Per il REALE serve una rete mainnet + chiave privata del wallet HL.
REAL = "n"          # "y" = ordini reali | "n" = simulato (default)

# Rete dati di mercato: anche in TEST i prezzi sono reali (mainnet),
# in modo che il paper trading sia valutabile.
NETWORK = "mainnet" # "mainnet" o "testnet"

# Chiavi richieste SOLO con REAL=y: lette da variabili d'ambiente
# (file .env di fianco a config.py). Se vuote, il bot resta in paper.
HL_ACCOUNT_ADDRESS = os.environ.get("HL_ACCOUNT_ADDRESS", "")
HL_SECRET_KEY = os.environ.get("HL_SECRET_KEY", "")

# Commissione simulata (0.035% per side) e capitale iniziale paper
FEE_PCT = 0.00035
START_BALANCE_USD = 1000.0

# Leva e margine per gli ordini PERPETUAL reali (usati solo con REAL=y).
# ISOLATED = "y" -> margine isolato | "n" -> cross (default HL)
LEVERAGE = 1.0
ISOLATED = "n"

# Cronologia/traccia scritta su file (come l'originale)
CRONO_FILE = "cronoMacd.txt"

# versione del bot (per banner e log)
VERSION = "2026.08.20 claude"
