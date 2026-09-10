# Swing Trader

A rules-based swing-trading scanner for liquid NYSE and NASDAQ stocks priced between $5 and $30.

## What it does

- **Full Exchange Universe**: Pulls all ~10,000 NYSE and NASDAQ tickers directly from SEC EDGAR and filters for $5–$30 price and >500k volume.
- **Technical Indicator Alignment**: Verifies 20, 50, and 200 SMA alignment, RSI between 45 and 70, and MACD bullish crossovers.
- **Chart Pattern Recognition**: Detects Bull Flags, Ascending Triangles, Cup-and-Handles, and Resistance Breakouts.
- **Statistical Forward Probabilities**: Calculates historical probability of gaining $\ge 10\%$ vs. losing $> 5\%$ over the next 10 trading days.
- **Catalyst & 72h Sentiment Analysis**: Ingests recent headlines to identify primary catalysts (Earnings/Guidance, Analyst Ratings, Insider Flow, Corporate Events).
- **Position & Risk Sizing**: Calculates Entry, ATR-based Stop Loss, Target 1 (+8–10%), Target 2 (+16–20%), share sizing, and Risk Rating.
- **Probability Ranking**: Ranks candidates from highest probability of +10% gain to lowest.

## Setup

Windows PowerShell:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Run scanner

To scan the entire NYSE and NASDAQ universe ($5–$30, >500k volume) and return the Top 25 candidates:

```powershell
python main.py
```

To scan specific tickers or adjust parameters:

```powershell
python main.py --symbols SOFI PLTR AGRO ABUS AES
python main.py --top 10 --min-price 5 --max-price 30
```

Each run outputs a Markdown candidate table and saves a structured snapshot under `data/scans/scan_YYYY-MM-DD.json`.

## Sentiment & Catalyst Classification

For candidates that pass the technical rules, the scanner fetches headlines within the last 72 hours and scores them with VADER. It also classifies the primary catalyst (e.g. Earnings/Guidance, Analyst Action, Insider/Institutional Flow, Corporate Events). Clearly positive news adds 5 points to the candidate score; clearly negative news subtracts 10 points.

Sentiment is a minor ranking adjustment, not an entry signal. Never let sentiment override technical structure, liquidity, or risk rules.

## Assumptions & Risk Management

- Account size: $1,000
- Risk per trade: 1% ($10)
- Stock price range: $5 to $30 (configurable via `--min-price` / `--max-price`)
- Profit targets: +1% quick target, Target 1 (+8–10%), Target 2 (+16–20%)
- Maximum position value: 40% of account ($400)
- Long-only
- Daily bars
- Signals are evaluated using completed daily candles and current market price

## Reading a trade plan

For a long trade, `entry` is the intended buy price. `stop` is the planned loss
exit, and `target` is the planned profit exit. After a buy fills, use a one-cancels-
other (OCO) bracket: a sell stop at the stop price and a sell limit at the target
price. If either exit fills, the broker cancels the other exit.

The displayed risk is planned rather than guaranteed: a stop order can fill below
its stop price during a fast move or overnight gap. Re-check the market price before
placing an order; a stale entry, stop, or target may no longer be appropriate.

## Position limits

The top-15 scan output is a ranked watchlist, not 15 simultaneous orders. Each
candidate is independently sized as if it were the only position in a $1,000
account, so adding their position values can exceed $1,000. The current application
does not yet allocate capital across a shared multi-position portfolio.

While learning, choose only a small number of the strongest setups and keep the
combined position value within available cash. Keep combined planned risk within a
limit you decide before trading; the per-trade $10 risk limit does not automatically
cap risk across multiple open positions.

## Suggested workflow

1. Run the daily scan and review the saved candidates.
2. Use a broad liquid watchlist and active-movers scan to find additional symbols,
   then run them with `python main.py scan --symbols ...`.
3. Verify each candidate's chart, liquidity, bid/ask spread, and upcoming events.
4. Paper trade the proposed entry, stop, target, and share count with an OCO bracket.
5. Keep a trade journal before considering real-money use.

The scanner is a research tool, not an automatic trading system. Its output is not
financial advice.
