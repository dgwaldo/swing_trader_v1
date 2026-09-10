from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
import yfinance as yf
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

_analyzer = SentimentIntensityAnalyzer()


@dataclass
class SentimentResult:
    score: float  # VADER compound score, -1 (very negative) to +1 (very positive)
    article_count: int
    headline: str | None
    catalyst: str


def classify_catalyst(headline: str | None) -> str:
    """Classify the primary driver or catalyst from the latest news."""
    if not headline:
        return "Technical Trend"
    
    h = headline.lower()
    if any(k in h for k in ["earnings", "eps", "revenue", "profit", "quarter", "guidance", "sales beat", "q1", "q2", "q3", "q4"]):
        return "Earnings / Guidance"
    if any(k in h for k in ["upgrade", "downgrade", "price target", "outperform", "buy rating", "overweight", "analyst"]):
        return "Analyst Action"
    if any(k in h for k in ["insider", "buys shares", "institutional", "stake", "form 4", "13f"]):
        return "Insider / Institutional Flow"
    if any(k in h for k in ["contract", "partnership", "deal", "fda", "approval", "patent", "launch", "expansion", "sec"]):
        return "Contract / Corporate Event"
    if any(k in h for k in ["short squeeze", "squeeze", "high volume", "breakout", "surges", "rallies", "jumps"]):
        return "Momentum / Short Squeeze"
    
    return "Company News"


def get_sentiment(symbol: str, max_articles: int = 10, hours_lookback: int = 72) -> SentimentResult:
    """Averages VADER sentiment over recent headlines within the last 72 hours and identifies key catalyst."""
    try:
        articles = yf.Ticker(symbol).news[:max_articles]
    except Exception:
        return SentimentResult(score=0.0, article_count=0, headline=None, catalyst="Technical Trend")

    if not articles:
        return SentimentResult(score=0.0, article_count=0, headline=None, catalyst="Technical Trend")

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=hours_lookback)

    scores = []
    recent_headlines = []

    for article in articles:
        content = article.get("content", {})
        title = content.get("title", "")
        pub_date_str = content.get("pubDate")
        
        # Check 72h window if pubDate is available
        is_recent = True
        if pub_date_str:
            try:
                # e.g. 2026-09-09T16:15:00Z
                pub_dt = datetime.fromisoformat(pub_date_str.replace("Z", "+00:00"))
                if pub_dt < cutoff:
                    is_recent = False
            except Exception:
                pass

        if title and is_recent:
            scores.append(_analyzer.polarity_scores(title)["compound"])
            recent_headlines.append(title)

    if not scores:
        # If no articles within 72h, check latest available title without strong sentiment weight
        top_title = articles[0].get("content", {}).get("title") if articles else None
        return SentimentResult(
            score=0.0,
            article_count=0,
            headline=top_title,
            catalyst=classify_catalyst(top_title),
        )

    top_headline = recent_headlines[0]
    avg_score = float(sum(scores) / len(scores))
    catalyst = classify_catalyst(top_headline)

    return SentimentResult(
        score=avg_score,
        article_count=len(scores),
        headline=top_headline,
        catalyst=catalyst,
    )
