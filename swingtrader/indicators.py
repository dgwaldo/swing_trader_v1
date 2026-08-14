import pandas as pd
from ta.momentum import RSIIndicator
from ta.trend import MACD
from ta.volatility import AverageTrueRange


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
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
    data["VOLUME_RATIO"] = volume / data["AVG_VOLUME20"]

    data["HIGH20"] = high.rolling(20).max()
    data["RETURN20"] = close.pct_change(20)

    return data
