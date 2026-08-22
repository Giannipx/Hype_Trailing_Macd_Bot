"""
Indicatori in Python puro (niente pandas/ta/talib), con la stessa semantica
di ta.trend.MACD e talib.RSI usati nel bot originale per Binance.
close: lista di prezzi di chiusura dal più vecchio al più recente.
"""


def _ema_series(closes, period):
    """Serie completa di EMA, seed al primo close (come ta.trend.MACD)."""
    alpha = 2.0 / (period + 1)
    ema = closes[0]
    out = [ema]
    for p in closes[1:]:
        ema = alpha * p + (1 - alpha) * ema
        out.append(ema)
    return out


def macd_series(closes, fast=12, slow=26, signal=9):
    """Ritorna (macd_line, signal_line, histogram) come liste di float."""
    if len(closes) < slow + signal:
        return [], [], []
    fast_ema = _ema_series(closes, fast)
    slow_ema = _ema_series(closes, slow)
    macd = [f - s for f, s in zip(fast_ema, slow_ema)]
    signal_line = _ema_series(macd, signal)
    hist = [m - s for m, s in zip(macd, signal_line)]
    return macd, signal_line, hist


def rsi(closes, period=14):
    """RSI Wilder (smoothing), come talib.RSI. None se dati insufficienti."""
    if len(closes) < period + 1:
        return None
    # Primo avg su una finestra di `period` delta
    gains = 0.0
    losses = 0.0
    for i in range(1, period + 1):
        delta = closes[i] - closes[i - 1]
        if delta >= 0:
            gains += delta
        else:
            losses -= delta
    avg_gain = gains / period
    avg_loss = losses / period

    for i in range(period + 1, len(closes)):
        delta = closes[i] - closes[i - 1]
        gain = max(delta, 0.0)
        loss = max(-delta, 0.0)
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def atr(candles, period=14):
    """Average True Range (Wilder smoothing, stessa logica di rsi() sopra).
    candles: lista di [timestamp, open, high, low, close, volume] dal più
    vecchio al più recente (formato ohlcv_data() di hl.py). Ritorna l'ultimo
    valore ATR, o None se i dati sono insufficienti (< period + 1 candele).

    Serve per uno STOPSIZE adattivo: un trailing stop fisso in dollari (es.
    $0.20) è troppo largo o troppo stretto a seconda della volatilità
    corrente; l'ATR misura l'escursione di prezzo media recente e permette
    di far seguire allo stop la volatilità reale invece di un valore
    arbitrario e statico.
    """
    if len(candles) < period + 1:
        return None

    true_ranges = []
    for i in range(1, len(candles)):
        high = candles[i][2]
        low = candles[i][3]
        prev_close = candles[i - 1][4]
        tr = max(
            high - low,
            abs(high - prev_close),
            abs(low - prev_close),
        )
        true_ranges.append(tr)

    if len(true_ranges) < period:
        return None

    # primo ATR = media semplice dei primi `period` True Range
    avg = sum(true_ranges[:period]) / period
    for tr in true_ranges[period:]:
        avg = (avg * (period - 1) + tr) / period

    return avg


def sma(closes, period):
    """Ultima SMA sul periodo. None se dati insufficienti."""
    if len(closes) < period:
        return None
    return sum(closes[-period:]) / period
