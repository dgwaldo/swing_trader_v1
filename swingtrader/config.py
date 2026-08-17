from dataclasses import dataclass


@dataclass(frozen=True)
class TradingConfig:
    account_size: float = 1000.0
    risk_fraction: float = 0.01
    max_position_fraction: float = 0.40
    minimum_rr: float = 2.0
    stop_atr_multiple: float = 1.5
    target_rr: float = 2.5
    minimum_price: float = 10.0
    minimum_average_volume: int = 500_000
    sentiment_positive_threshold: float = 0.2
    sentiment_negative_threshold: float = -0.2
    sentiment_bonus_score: int = 5
    sentiment_penalty_score: int = 10
