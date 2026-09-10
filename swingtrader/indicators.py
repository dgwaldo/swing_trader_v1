import numpy as np
import pandas as pd
from ta.momentum import RSIIndicator
from ta.trend import MACD
from ta.volatility import AverageTrueRange


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Calculate moving averages, momentum, volatility, and volume indicators."""
    data = df.copy()

    close = data["Close"]
    high = data["High"]
    low = data["Low"]
    volume = data["Volume"]

    data["SMA20"] = close.rolling(20).mean()
    data["SMA50"] = close.rolling(50).mean()
    data["SMA200"] = close.rolling(200).mean()

    data["RSI"] = RSIIndicator(close=close, window=14).rsi()

    macd = MACD(close=close)
    data["MACD"] = macd.macd()
    data["MACD_SIGNAL"] = macd.macd_signal()
    data["MACD_HIST"] = macd.macd_diff()

    atr = AverageTrueRange(high=high, low=low, close=close, window=14)
    data["ATR"] = atr.average_true_range()

    data["AVG_VOLUME20"] = volume.rolling(20).mean()
    data["VOLUME_RATIO"] = volume / data["AVG_VOLUME20"].replace(0, np.nan)

    data["HIGH20"] = high.rolling(20).max()
    data["LOW20"] = low.rolling(20).min()
    data["RETURN20"] = close.pct_change(20)

    return data


def detect_patterns(df: pd.DataFrame) -> list[str]:
    """Identify classic technical breakout formations (Cup & Handle, Bull Flag, Ascending Triangle, Resistance Breakout)."""
    patterns = []
    if len(df) < 40:
        return patterns

    close = df["Close"].values
    high = df["High"].values
    low = df["Low"].values
    vol_ratio = float(df["VOLUME_RATIO"].iloc[-1]) if "VOLUME_RATIO" in df else 1.0

    # 1. Breakout above 20-day resistance
    prior_high20 = df["HIGH20"].shift(1).iloc[-1]
    if close[-1] >= prior_high20 and vol_ratio >= 1.15:
        patterns.append("Breakout Above Resistance")

    # 2. Bull Flag: Sharp impulsive pole (+8% or more) followed by tight consolidation (<=6% range) holding above SMA20
    if len(close) >= 20:
        pole_gain = (np.max(high[-15:-4]) - np.min(low[-20:-10])) / max(np.min(low[-20:-10]), 1e-6)
        consolidation_range = (np.max(high[-5:]) - np.min(low[-5:])) / max(close[-1], 1e-6)
        sma20 = df["SMA20"].iloc[-1]
        if pole_gain >= 0.08 and consolidation_range <= 0.065 and close[-1] >= sma20:
            patterns.append("Bull Flag")

    # 3. Ascending Triangle: Flat upper horizontal resistance + higher rising swing lows
    if len(close) >= 35:
        highs_35 = high[-35:]
        top_res = np.percentile(highs_35, 95)
        touches = np.sum((highs_35 >= top_res * 0.985) & (highs_35 <= top_res * 1.015))
        low1 = np.min(low[-35:-18])
        low2 = np.min(low[-18:-3])
        if touches >= 2 and low2 > low1 * 1.015 and close[-1] >= top_res * 0.965:
            patterns.append("Ascending Triangle")

    # 4. Cup and Handle: U-shaped rounding bottom followed by shallow handle pullback
    if len(close) >= 55:
        left_rim = np.max(high[-55:-25])
        cup_bottom = np.min(low[-40:-15])
        right_rim = np.max(high[-18:-5])
        cup_depth = (left_rim - cup_bottom) / max(left_rim, 1e-6)
        handle_depth = (right_rim - np.min(low[-5:])) / max(right_rim, 1e-6)
        rim_diff = abs(right_rim - left_rim) / max(left_rim, 1e-6)
        if 0.08 <= cup_depth <= 0.38 and rim_diff <= 0.06 and 0.01 <= handle_depth <= 0.09:
            patterns.append("Cup-and-Handle")

    return patterns


def calculate_forward_probabilities(df: pd.DataFrame) -> tuple[float, float]:
    """Calculate empirical probability of +10% gain and -5% loss over next 10 trading days based on historical similar uptrend setups."""
    if len(df) < 50:
        return 0.30, 0.25

    close = df["Close"]
    high = df["High"]
    low = df["Low"]

    forward_max_10d = high.iloc[::-1].rolling(10).max().iloc[::-1].shift(-1)
    forward_min_10d = low.iloc[::-1].rolling(10).min().iloc[::-1].shift(-1)

    max_gain_10d = (forward_max_10d - close) / close
    max_loss_10d = (forward_min_10d - close) / close

    # Condition on similar constructive technical regime
    cond = (
        (df["Close"] > df["SMA20"])
        & (df["SMA20"] > df["SMA50"])
        & (df["RSI"] >= 45)
        & (df["RSI"] <= 75)
    )

    subset_gains = max_gain_10d[cond].dropna()
    subset_losses = max_loss_10d[cond].dropna()

    if len(subset_gains) >= 10:
        p_gain_10 = float((subset_gains >= 0.10).mean())
        p_loss_5 = float((subset_losses <= -0.05).mean())
    else:
        # Blend with prior if historical sample is small
        all_gains = max_gain_10d.dropna()
        all_losses = max_loss_10d.dropna()
        p_gain_10 = float((all_gains >= 0.10).mean()) if len(all_gains) else 0.30
        p_loss_5 = float((all_losses <= -0.05).mean()) if len(all_losses) else 0.25

    return round(p_gain_10, 3), round(p_loss_5, 3)
