# Swing Trader V1

A small Python research/scanning tool for a $1,000 swing-trading account.

## What it does

- Downloads daily OHLCV data with `yfinance`
- Calculates SMA 20/50/200, RSI, MACD, ATR, and volume ratio
- Scores stocks using a transparent rules-based model
- Calculates entry, stop, target, position size, and maximum dollar risk
- Runs a simple historical backtest
- Discovers liquid current movers through Yahoo Finance screeners
- Prints a ranked candidate list
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
curated fallback list in `main.py`. Discovery filters out symbols below $10 or
500,000 current volume before the technical scan runs.

Each scan also saves a structured JSON snapshot under `data/scans/`, including the
scan date, symbols checked, and ranked candidates. Use `--output` to choose a
different path. This is intended for downstream signal or execution modules; the
backtester continues to use historical OHLCV data directly.

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

You can also scan a custom list:

```powershell
python main.py scan --symbols AAPL MSFT NVDA AMD AMZN META GOOGL TSLA PLTR
```

## V1 assumptions

- Account size: $1,000
- Risk per trade: 1% ($10)
- Minimum reward/risk: 2:1
- Maximum position value: 40% of account
- Long-only
- Daily bars
- No commissions/slippage in V1 backtest
- Signals are evaluated using completed daily candles

## Suggested workflow

1. Run the daily scan and review the saved candidates.
2. Verify each candidate's chart, liquidity, and upcoming events manually.
3. Paper trade the proposed entry, stop, target, and share count.
4. Backtest a broad list of symbols and compare results across different periods.
5. Keep a trade journal before considering real-money use.

The scanner is a research tool, not an automatic trading system. Its output is not
financial advice, and a backtest is not evidence that a strategy will be profitable
in the future.

The V1 backtester currently has no commissions or slippage, does not model a shared
multi-symbol portfolio, and does not include survivorship-bias controls or proper
out-of-sample testing.
