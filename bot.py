import requests
import pandas as pd
import numpy as np
import time

# ---------- Fetch historical klines from Binance (no API key needed) ----------
def fetch_klines(symbol="BTCUSDT", interval="15m", start_str=None, limit=1000):
    url = "https://api.binance.com/api/v3/klines"
    params = {
        "symbol": symbol,
        "interval": interval,
        "limit": limit
    }
    if start_str:
        params["startTime"] = start_str
    response = requests.get(url, params=params)
    data = response.json()

    df = pd.DataFrame(data, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_asset_volume", "number_of_trades",
        "taker_buy_base", "taker_buy_quote", "ignore"
    ])

    # Convert types
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)
    df["open_time"] = pd.to_datetime(df["open_time"], unit='ms')

    return df[["open_time", "open", "high", "low", "close", "volume"]]

# ---------- Calculate indicators ----------
def calculate_indicators(df):
    # EMA 20
    df["EMA20"] = df["close"].ewm(span=20, adjust=False).mean()

    # RSI 14
    delta = df["close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(window=14).mean()
    avg_loss = loss.rolling(window=14).mean()
    rs = avg_gain / avg_loss
    df["RSI"] = 100 - (100 / (1 + rs))

    # VWAP
    typical_price = (df["high"] + df["low"] + df["close"]) / 3
    cum_vol = df["volume"].cumsum()
    cum_vol_price = (typical_price * df["volume"]).cumsum()
    df["VWAP"] = cum_vol_price / cum_vol

    return df

# ---------- Strategies ----------
def strategy_ema_rsi(df, i):
    if i < 20 or pd.isna(df.loc[i, "RSI"]) or pd.isna(df.loc[i, "EMA20"]):
        return 0
    price = df.loc[i, "close"]
    if price > df.loc[i, "EMA20"] and df.loc[i, "RSI"] < 30:
        return 1  # buy
    return 0

def strategy_breakout_retest(df, i):
    if i < 2:
        return 0
    prev_high = df.loc[i-2, "high"]
    prev_close = df.loc[i-1, "close"]
    curr_close = df.loc[i, "close"]
    # Breakout: price breaks previous high then retests it
    if prev_close > prev_high and curr_close > prev_high:
        return 1
    return 0

def strategy_scalping_vwap(df, i):
    if i == 0:
        return 0
    prev_close = df.loc[i-1, "close"]
    prev_vwap = df.loc[i-1, "VWAP"]
    curr_close = df.loc[i, "close"]
    curr_vwap = df.loc[i, "VWAP"]
    if prev_close < prev_vwap and curr_close > curr_vwap:
        return 1
    return 0

# ---------- Backtesting ----------
def backtest_strategy(df, strategy_func, stop_loss_pct=0.10):
    position = None
    entry_price = 0
    balance = 10000
    trades = []

    for i in range(len(df)):
        price = df.loc[i, "close"]
        signal = strategy_func(df, i)

        if position is None:
            if signal == 1:
                position = "long"
                entry_price = price
                trades.append({"type": "buy", "price": price, "index": i})
        else:
            # Stop loss
            if price <= entry_price * (1 - stop_loss_pct):
                trades.append({"type": "stop_loss_sell", "price": price, "index": i})
                position = None

            # Could add take profit or exit signals here

    # Summarize results
    num_trades = sum(1 for t in trades if t["type"] == "buy")
    stop_loss_hits = sum(1 for t in trades if t["type"] == "stop_loss_sell")
    estimated_profit_pct = -stop_loss_pct * stop_loss_hits  # rough guess

    return {
        "strategy": strategy_func.__name__,
        "num_trades": num_trades,
        "stop_loss_hits": stop_loss_hits,
        "estimated_profit_pct": estimated_profit_pct,
        "trades": trades
    }

# ---------- Main ----------
def main():
    print("Fetching data...")
    df = fetch_klines(symbol="BTCUSDT", interval="15m", limit=1500)
    df = calculate_indicators(df)

    strategies = [strategy_ema_rsi, strategy_breakout_retest, strategy_scalping_vwap]

    all_results = []
    for strat in strategies:
        print(f"Backtesting {strat.__name__}...")
        result = backtest_strategy(df, strat, stop_loss_pct=0.10)
        all_results.append(result)
        print(f"Results: Trades={result['num_trades']}, Stop-loss hits={result['stop_loss_hits']}, Estimated Profit%={result['estimated_profit_pct']}")

    print("\nSummary of all strategies:")
    for r in all_results:
        print(f"{r['strategy']}: Trades={r['num_trades']}, Stop-loss hits={r['stop_loss_hits']}, Estimated Profit={r['estimated_profit_pct']}%")

if __name__ == "__main__":
    main()
