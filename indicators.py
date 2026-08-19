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


def sma(closes, period):
    """Ultima SMA sul periodo. None se dati insufficienti."""
    if len(closes) < period:
        return None
    return sum(closes[-period:]) / period
