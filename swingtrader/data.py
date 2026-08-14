import pandas as pd
import yfinance as yf


def discover_symbols(
    minimum_price: float = 10.0,
    minimum_volume: int = 500_000,
    size: int = 100,
) -> list[str]:
    """Return liquid symbols surfaced by Yahoo's current market screeners."""
    symbols = []

    for screen_name in ("day_gainers", "most_actives"):
        try:
            result = yf.screen(
                screen_name,
                sortField="percentchange",
                sortAsc=False,
                size=size,
            )
        except Exception as exc:
            print(f"Yahoo screener unavailable for {screen_name}: {exc}")
            continue

        for quote in result.get("quotes", []):
            symbol = quote.get("symbol")
            price = quote.get("regularMarketPrice")
            volume = quote.get("regularMarketVolume")

            if not symbol:
                continue
            if price is not None and price < minimum_price:
                continue
            if volume is not None and volume < minimum_volume:
                continue
            if symbol not in symbols:
                symbols.append(symbol)

    return symbols


def download(symbol: str, period: str = "2y") -> pd.DataFrame:
    df = yf.download(
        symbol,
        period=period,
        interval="1d",
        auto_adjust=True,
        progress=False,
    )

    if df.empty:
        return df

    # yfinance can return MultiIndex columns even for one symbol.
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    return df.dropna()
