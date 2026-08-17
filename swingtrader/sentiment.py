from dataclasses import dataclass

import yfinance as yf
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

_analyzer = SentimentIntensityAnalyzer()


@dataclass
class SentimentResult:
    score: float  # VADER compound score, -1 (very negative) to +1 (very positive)
    article_count: int
    headline: str | None


def get_sentiment(symbol: str, max_articles: int = 10) -> SentimentResult:
    """Averages VADER sentiment over a symbol's most recent Yahoo Finance headlines."""
    try:
        articles = yf.Ticker(symbol).news[:max_articles]
    except Exception:
        return SentimentResult(score=0.0, article_count=0, headline=None)

    if not articles:
        return SentimentResult(score=0.0, article_count=0, headline=None)

    scores = []
    for article in articles:
        title = article.get("content", {}).get("title", "")
        if title:
            scores.append(_analyzer.polarity_scores(title)["compound"])

    if not scores:
        return SentimentResult(score=0.0, article_count=0, headline=None)

    top_headline = articles[0].get("content", {}).get("title")
    return SentimentResult(
        score=sum(scores) / len(scores),
        article_count=len(scores),
        headline=top_headline,
    )
