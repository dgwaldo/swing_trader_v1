# Swing Trader V1

A small Python research/scanning tool for a $1,000 swing-trading account.

## What it does

- Downloads daily OHLCV data with `yfinance`
- Calculates SMA 20/50/200, RSI, MACD, ATR, and volume ratio
- Scores stocks using a transparent rules-based model
- Calculates entry, stop, target, position size, and maximum dollar risk
- Scores recent Yahoo Finance headlines for sentiment
- Runs a simple historical backtest
- Discovers liquid current movers through Yahoo Finance screeners
- Prints a ranked Markdown candidate table
- Saves each scan as a structured daily JSON snapshot

This is a research/education tool, not financial advice or an automated trading system.

## Setup

Windows PowerShell:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Run scanner

```powershell
python main.py scan
```

With no symbol list, the scanner discovers liquid current movers from Yahoo Finance
using the day-gainers and most-active screens, then applies the normal V1 technical
rules to each symbol. If Yahoo's screener is unavailable, it falls back to the
curated fallback list in `main.py`. Discovery filters out symbols below $10,
above $400, or below 500,000 current volume before the technical scan runs.

Each scan also saves a structured JSON snapshot under `data/scans/`, including the
scan date, symbols checked, and ranked candidates. Use `--output` to choose a
different path. This is intended for downstream signal or execution modules; the
backtester continues to use historical OHLCV data directly.

The terminal report is a Markdown table that can be pasted into a trade journal or
notes app. It shows the entry, stop, target, shares, planned dollar risk,
reward/risk, sentiment score, and technical reasons for each candidate.

The scanner keeps the top 15 candidates by default. Choose another limit with
`--top`, or use `0` for every qualifying candidate:

```powershell
python main.py scan --top 10
python main.py scan --top 0
```

Target a custom stock price range with `--min-price` and `--max-price`:

```powershell
python main.py scan --min-price 5 --max-price 25
```

The evening grid reports a target percent move above the planned entry. It defaults
to 1%; change it with `--move-percent`:

```powershell
python main.py scan --move-percent 2.5
```

## Run evening workflow

For an after-hours next-day plan, scan the market, backtest the top 10 qualifying
candidates, and print a combined grid in one command:

```powershell
python main.py evening
```

The workflow uses the current live or extended-hours price for the planned entry,
then recalculates the stop, target, shares, and planned risk. It uses five years of
historical daily data for each selected candidate's backtest. The combined report
is saved under `data/scans/evening_YYYY-MM-DD.json`. You can scan a specific list or
choose another report path:

```powershell
python main.py evening --symbols AAPL MSFT NVDA AMD TSLA
python main.py evening --output data/scans/my-evening-report.json
python main.py evening --min-price 5 --max-price 25
python main.py evening --move-percent 2.5
```

The evening grid is a decision aid for the following session, not an automatic order
system. Before placing an order, recheck the live bid/ask, spread, liquidity, and
any overnight news. A backtest result is historical context, not a prediction.

The evening command also prints a rules-based English `Buying Focus` brief beneath
the grid. The grid and the brief use the same overall ranking, so Rank 1 is the best
overall current opportunity, Rank 2 is the second best, and so on. Ranking starts
with the live setup score, then uses historical fit, backtest return, win rate,
drawdown, and trade count to break ties. The `Strong`, `Limited data`, and `Weak`
labels remain visible as historical-context warnings; a high live rank with limited
backtest data should still be treated cautiously. This is explainable local
analysis, not a cloud AI model or financial advice.

Generated scan snapshots are ignored by Git because they are daily runtime output.

## Run backtest

```powershell
python main.py backtest --symbol AAPL
```

Backtest several symbols independently with the same V1 account assumptions:

```powershell
python main.py backtest --symbols AAPL MSFT NVDA AMD TSLA
```

Each symbol gets its own result starting from the configured account size; this is
not yet a combined portfolio backtest.

Multi-symbol backtests print a Markdown comparison table with trades, wins, losses,
win rate, total return, maximum drawdown, and historical fit. Results are sorted by
historical fit first, then by total return.

`Historical Fit` is a quick review label, not a prediction or a trade signal:

| Historical Fit | Criteria |
|---|---|
| Strong | At least 10 trades, positive total return, and maximum drawdown at or below 5% |
| Limited data | Fewer than 10 trades |
| Weak | At least 10 trades with a negative return or drawdown above 5% |

Use a positive historical fit alongside a valid current scan, not instead of one.
Small samples can look impressive by chance, and a strong past result does not
guarantee a future trade will work.

You can also scan a custom list:

```powershell
python main.py scan --symbols AAPL MSFT NVDA AMD AMZN META GOOGL TSLA PLTR
```

## Sentiment

For candidates that already pass the technical rules, the scanner fetches up to 10
recent Yahoo Finance headlines and scores their text with VADER. The average score
ranges from `-1` (very negative) to `+1` (very positive). Clearly positive news
adds 5 points to the candidate score; clearly negative news subtracts 10 points.

Sentiment is a minor ranking adjustment, not an entry signal. Headlines can be
incomplete, stale, or ambiguous, so never let a sentiment score override the chart,
liquidity, or your risk rules.

## V1 assumptions

- Account size: $1,000
- Risk per trade: 1% ($10)
- Stock price range: $5 to $25
- Minimum reward/risk: 2:1
- Maximum position value: 40% of account
- Long-only
- Daily bars
- No commissions/slippage in V1 backtest
- Signals are evaluated using completed daily candles

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
5. Backtest a broad list of symbols and compare results across different periods.
6. Keep a trade journal before considering real-money use.

The scanner is a research tool, not an automatic trading system. Its output is not
financial advice, and a backtest is not evidence that a strategy will be profitable
in the future.

The V1 backtester currently has no commissions or slippage, does not model a shared
multi-symbol portfolio, and does not include survivorship-bias controls or proper
out-of-sample testing.
