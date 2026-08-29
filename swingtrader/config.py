from dataclasses import dataclass


@dataclass(frozen=True)
class TradingConfig:
    # Starting balance used for position sizing and backtests.
    account_size: float = 1000.0
    # Largest planned loss per trade: 0.01 means 1% of the account ($10 here).
    risk_fraction: float = 0.01
    # Largest portion of the account allowed in one position: 0.40 means 40%.
    max_position_fraction: float = 0.40
    # A setup must offer at least this much potential reward for each $1 risked.
    minimum_rr: float = 2.0
    # Stop distance below entry, measured in Average True Range (ATR) units.
    stop_atr_multiple: float = 1.5
    # Profit target distance, expressed as a multiple of the planned risk.
    target_rr: float = 2.5
    # Avoid low-priced stocks that can have large percentage swings and wide spreads.
    minimum_price: float = 5.0
    # Avoid stocks above the largest whole-share position this account can take by default.
    maximum_price: float = 25.0
    # Require this many shares traded per day on average over the last 20 days.
    minimum_average_volume: int = 500_000
    # Average headline sentiment at or above this value earns a small score bonus.
    sentiment_positive_threshold: float = 0.2
    # Average headline sentiment at or below this value receives a score penalty.
    sentiment_negative_threshold: float = -0.2
    # Points added to an otherwise valid setup with clearly positive headline sentiment.
    sentiment_bonus_score: int = 5
    # Points subtracted from an otherwise valid setup with clearly negative headline sentiment.
    sentiment_penalty_score: int = 10
    # Percent move above entry shown in the evening grid: 1.0 means +1%.
    move_percent: float = 1.0
