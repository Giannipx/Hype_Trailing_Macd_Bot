# ============================================================
# CONFIG - ADATTAMENTO HYPERLIQUID
# (il bot originale era per Binance; ora usa hyperliquid-python-sdk)
# ============================================================

# Modalità di default: TEST (= paper trading). Nessuna chiave richiesta.
# Per il REALE serve una rete mainnet + chiave privata del wallet HL.
REAL = "n"          # "y" = ordini reali | "n" = simulato (default)

# Rete dati di mercato: anche in TEST i prezzi sono reali (mainnet),
# in modo che il paper trading sia valutabile.
NETWORK = "mainnet" # "mainnet" o "testnet"

# Chiavi richieste SOLO con REAL=y
HL_ACCOUNT_ADDRESS = ""
HL_SECRET_KEY = ""

# Commissione simulata (0.035% per side) e capitale iniziale paper
FEE_PCT = 0.00035
START_BALANCE_USD = 1000.0

# Leva e margine per gli ordini PERPETUAL reali (usati solo con REAL=y).
# ISOLATED = "y" -> margine isolato | "n" -> cross (default HL)
LEVERAGE = 1.0
ISOLATED = "n"

# Cronologia/traccia scritta su file (come l'originale)
CRONO_FILE = "cronoMacd.txt"
