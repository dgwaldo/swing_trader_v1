from dataclasses import dataclass
import math
import pandas as pd

from .config import TradingConfig
from .indicators import add_indicators, detect_patterns, calculate_forward_probabilities
from .sentiment import get_sentiment


@dataclass
class TradeCandidate:
    symbol: str
    score: int
    prob_gain_10d: float
    prob_loss_5d: float
    risk_rating: str
    entry: float
    stop: float
    target_1pct: float
    target_1: float
    target_2: float
    shares: int
    risk_dollars: float
    reward_risk: float
    main_catalyst: str
    setup: str
    reasons: list[str]
    sentiment_score: float = 0.0
    sentiment_headline: str | None = None


def evaluate_risk_rating(atr_pct: float, prob_loss_5d: float) -> str:
    """Classify risk as Low, Medium, or High based on volatility and historical downside probability."""
    if atr_pct <= 0.035 and prob_loss_5d <= 0.25:
        return "Low"
    elif atr_pct <= 0.065 and prob_loss_5d <= 0.40:
        return "Medium"
    else:
        return "High"


def analyze(
    symbol: str,
    raw: pd.DataFrame,
    cfg: TradingConfig,
    current_price: float | None = None,
) -> TradeCandidate | None:
    if raw.empty:
        return None

    df = add_indicators(raw).dropna()
    if len(df) < 30:
        return None

    r = df.iloc[-1]

    price = float(r["Close"])
    entry = current_price if current_price is not None else price
    atr = float(r["ATR"])
    avg_volume = float(r["AVG_VOLUME20"])

    if (
        entry < cfg.minimum_price
        or entry > cfg.maximum_price
        or avg_volume < cfg.minimum_average_volume
    ):
        return None

    score = 0
    reasons = []

    # 1. Trend & Moving Average Alignment
    if price > r["SMA20"]:
        score += 10
        reasons.append("Price > SMA20")

    if r["SMA20"] > r["SMA50"]:
        score += 15
        reasons.append("SMA20 > SMA50")

    if r["SMA50"] > r["SMA200"]:
        score += 15
        reasons.append("SMA50 > SMA200 (Golden Alignment)")

    # 2. Momentum & Oscillators
    if 45 <= r["RSI"] <= 70:
        score += 10
        reasons.append(f"Healthy RSI ({r['RSI']:.1f})")

    if r["MACD"] > r["MACD_SIGNAL"]:
        score += 10
        if r["MACD_HIST"] > 0 and df["MACD_HIST"].iloc[-2] <= 0:
            score += 5
            reasons.append("MACD Bullish Crossover")
        else:
            reasons.append("MACD Bullish")

    # 3. Volume Confirmation
    vol_ratio = float(r["VOLUME_RATIO"])
    if vol_ratio >= 1.2:
        score += 10
        reasons.append(f"Volume Expansion ({vol_ratio:.1f}x)")

    # 4. Pattern Recognition
    patterns = detect_patterns(df)
    if patterns:
        score += 15
        reasons.extend(patterns)
        setup = ", ".join(patterns)
    elif price >= df["HIGH20"].shift(1).iloc[-1]:
        score += 15
        reasons.append("20-day High Breakout")
        setup = "Breakout"
    elif r["RETURN20"] > 0.05:
        score += 10
        reasons.append("Positive 20-day Momentum")
        setup = "Momentum"
    else:
        setup = "Trend Continuation"

    if score < 55:
        return None

    # 5. News Sentiment & Catalyst Extraction
    sentiment = get_sentiment(symbol)
    if sentiment.article_count:
        if sentiment.score >= cfg.sentiment_positive_threshold:
            score += cfg.sentiment_bonus_score
            reasons.append(f"Positive Sentiment ({sentiment.score:+.2f})")
        elif sentiment.score <= cfg.sentiment_negative_threshold:
            score -= cfg.sentiment_penalty_score
            reasons.append(f"Negative Sentiment ({sentiment.score:+.2f})")

    # 6. Historical Forward Return Probabilities (10 Trading Days)
    prob_gain_10d, prob_loss_5d = calculate_forward_probabilities(df)

    # 7. Targets & Risk Management
    stop = entry - (atr * cfg.stop_atr_multiple)
    if stop <= 0 or stop >= entry:
        return None

    risk_per_share = entry - stop
    atr_pct = atr / entry
    risk_rating = evaluate_risk_rating(atr_pct, prob_loss_5d)

    # Targets
    target_1pct = entry * (1 + cfg.target_percent / 100.0)
    target_1 = entry + max(risk_per_share * 1.5, entry * 0.08)
    target_2 = entry + max(risk_per_share * 3.0, entry * 0.16)

    rr_1 = (target_1 - entry) / risk_per_share

    max_risk = cfg.account_size * cfg.risk_fraction
    max_position_value = cfg.account_size * cfg.max_position_fraction

    shares_by_risk = math.floor(max_risk / risk_per_share)
    shares_by_capital = math.floor(max_position_value / entry)
    shares = max(0, min(shares_by_risk, shares_by_capital))

    if shares < 1:
        return None

    risk_dollars = shares * risk_per_share
    score = min(100, max(0, score))

    return TradeCandidate(
        symbol=symbol,
        score=score,
        prob_gain_10d=prob_gain_10d,
        prob_loss_5d=prob_loss_5d,
        risk_rating=risk_rating,
        entry=entry,
        stop=stop,
        target_1pct=target_1pct,
        target_1=target_1,
        target_2=target_2,
        shares=shares,
        risk_dollars=risk_dollars,
        reward_risk=rr_1,
        main_catalyst=sentiment.catalyst,
        setup=setup,
        reasons=reasons,
        sentiment_score=sentiment.score,
        sentiment_headline=sentiment.headline,
    )
