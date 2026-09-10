import json
import urllib.request
from pathlib import Path
import pandas as pd
import yfinance as yf


import json
import urllib.request
from pathlib import Path
import pandas as pd
import yfinance as yf


def fetch_market_candidates(
    minimum_price: float = 5.0,
    maximum_price: float = 30.0,
    minimum_volume: int = 500_000,
    cache_path: str = "data/nasdaq_screener.json",
) -> list[str]:
    """Fetch all NYSE and NASDAQ common stocks in 1 instant request via Nasdaq official screener."""
    path = Path(cache_path)
    rows = []

    url = "https://api.nasdaq.com/api/screener/stocks?tableonly=true&limit=25&offset=0&download=true"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "application/json, text/plain, */*",
    }
    req = urllib.request.Request(url, headers=headers)

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        rows = data.get("data", {}).get("rows", [])
        if rows:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    except Exception as exc:
        print(f"Warning: Nasdaq screener online fetch failed ({exc}). Trying local cache...")
        if path.exists():
            try:
                rows = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                pass

    if not rows:
        return []

    def parse_price(val):
        if not val:
            return 0.0
        s = str(val).replace("$", "").replace(",", "").strip()
        try:
            return float(s)
        except ValueError:
            return 0.0

    def parse_vol(val):
        if not val:
            return 0
        s = str(val).replace(",", "").strip()
        try:
            return int(s)
        except ValueError:
            return 0

    def is_valid_stock(row):
        sym = row.get("symbol", "")
        name = row.get("name", "").lower()
        if not sym or any(c in sym for c in ["^", ".", "/", " ", "$"]):
            return False
        # Filter out warrants, rights, units, preferreds, and funds
        if any(w in name for w in ["warrant", "right", "unit", "preferred", "etf", "fund", "trust", "note", "etn", "par $", "depository"]):
            return False
        price = parse_price(row.get("lastsale"))
        vol = parse_vol(row.get("volume"))
        return minimum_price <= price <= maximum_price and vol >= minimum_volume

    qualifying = [r["symbol"] for r in rows if is_valid_stock(r)]
    print(f"Discovered {len(qualifying)} qualifying ${minimum_price:.2f}-${maximum_price:.2f} stocks (> {minimum_volume:,} volume) across NYSE & NASDAQ.")
    return qualifying


def download_batch(symbols: list[str], period: str = "1y", batch_size: int = 60) -> dict[str, pd.DataFrame]:
    """Download daily price history for multiple symbols in parallel batches with rate-limit protection."""
    import time

    results = {}
    if not symbols:
        return results

    total_batches = (len(symbols) + batch_size - 1) // batch_size
    for idx in range(total_batches):
        chunk = symbols[idx * batch_size : (idx + 1) * batch_size]
        try:
            df = yf.download(
                chunk,
                period=period,
                interval="1d",
                auto_adjust=True,
                group_by="ticker",
                progress=False,
                threads=False,
            )
            if df.empty:
                continue

            if len(chunk) == 1:
                ticker = chunk[0]
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(0)
                clean_df = df.dropna()
                if not clean_df.empty and len(clean_df) >= 30:
                    results[ticker] = clean_df
            else:
                for ticker in chunk:
                    try:
                        if ticker in df:
                            sub_df = df[ticker].dropna()
                            if not sub_df.empty and len(sub_df) >= 30:
                                results[ticker] = sub_df
                    except Exception:
                        continue
        except Exception:
            pass
        
        # Short polite pause between chunks to prevent rate-limiting
        if idx < total_batches - 1:
            time.sleep(0.2)

    return results


def current_price(symbol: str) -> float | None:
    """Return the latest regular or extended-hours price when available."""
    try:
        history = yf.Ticker(symbol).history(
            period="1d",
            interval="1m",
            prepost=True,
            auto_adjust=True,
        )
    except Exception:
        return None

    if history.empty:
        return None

    close = history["Close"].dropna()
    return float(close.iloc[-1]) if not close.empty else None
