from dataclasses import dataclass
import math
import pandas as pd

from .config import TradingConfig
from .indicators import add_indicators
from .sentiment import get_sentiment


@dataclass
class TradeCandidate:
    symbol: str
    score: int
    setup: str
    entry: float
    stop: float
    target: float
    shares: int
    risk_dollars: float
    reward_risk: float
    reasons: list[str]
    sentiment_score: float = 0.0
    sentiment_headline: str | None = None


def analyze(symbol: str, raw: pd.DataFrame, cfg: TradingConfig) -> TradeCandidate | None:
    if raw.empty:
        return None

    df = add_indicators(raw).dropna()
    if df.empty:
        return None

    r = df.iloc[-1]

    price = float(r["Close"])
    atr = float(r["ATR"])
    avg_volume = float(r["AVG_VOLUME20"])

    if price < cfg.minimum_price or avg_volume < cfg.minimum_average_volume:
        return None

    score = 0
    reasons = []

    # Trend
    if price > r["SMA20"]:
        score += 10
        reasons.append("above SMA20")

    if r["SMA20"] > r["SMA50"]:
        score += 15
        reasons.append("SMA20 > SMA50")

    if r["SMA50"] > r["SMA200"]:
        score += 15
        reasons.append("SMA50 > SMA200")

    # Momentum
    if 50 <= r["RSI"] <= 70:
        score += 10
        reasons.append("healthy RSI")

    if r["MACD"] > r["MACD_SIGNAL"]:
        score += 10
        reasons.append("MACD bullish")

    # Volume confirmation
    if r["VOLUME_RATIO"] >= 1.2:
        score += 10
        reasons.append("volume expansion")

    # Breakout / relative strength
    prior_high20 = df["HIGH20"].shift(1).iloc[-1]
    if price >= prior_high20:
        score += 20
        reasons.append("20-day breakout")
        setup = "Breakout"
    elif r["RETURN20"] > 0.05:
        score += 10
        reasons.append("positive 20-day momentum")
        setup = "Momentum"
    else:
        setup = "Trend"

    if score < 60:
        return None

    sentiment = get_sentiment(symbol)
    if sentiment.article_count:
        if sentiment.score >= cfg.sentiment_positive_threshold:
            score += cfg.sentiment_bonus_score
            reasons.append("positive news sentiment")
        elif sentiment.score <= cfg.sentiment_negative_threshold:
            score -= cfg.sentiment_penalty_score
            reasons.append("negative news sentiment")

    entry = price
    stop = entry - (atr * cfg.stop_atr_multiple)

    if stop <= 0 or stop >= entry:
        return None

    risk_per_share = entry - stop
    target = entry + (risk_per_share * cfg.target_rr)
    rr = (target - entry) / risk_per_share

    max_risk = cfg.account_size * cfg.risk_fraction
    max_position_value = cfg.account_size * cfg.max_position_fraction

    shares_by_risk = math.floor(max_risk / risk_per_share)
    shares_by_capital = math.floor(max_position_value / entry)
    shares = max(0, min(shares_by_risk, shares_by_capital))

    if shares < 1 or rr < cfg.minimum_rr:
        return None

    risk_dollars = shares * risk_per_share

    return TradeCandidate(
        symbol=symbol,
        score=score,
        setup=setup,
        entry=entry,
        stop=stop,
        target=target,
        shares=shares,
        risk_dollars=risk_dollars,
        reward_risk=rr,
        reasons=reasons,
        sentiment_score=sentiment.score,
        sentiment_headline=sentiment.headline,
    )
