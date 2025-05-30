import requests
import pandas as pd
import time
import numpy as np
from ta.momentum import RSIIndicator
from ta.trend import EMAIndicator
import os

# --- CONFIGURATION ---
symbol = "BTCUSDT"
interval = "15m"
days = 365  # 1 year
limit_per_request = 1000
initial_balance = 10000
data_file = f"{symbol}_{interval}_{days}d.csv"

config = {
    "stop_loss_pct": 0.10,  # 2%
    "take_profit_pct": 0.03,  # 3%
}

strategies = ["EMA_RSI_VWAP", "BREAKOUT_RETEST", "SCALPING_VWAP"]

# --- FETCH BINANCE KLINES ---
def get_klines(symbol, interval, start_time, end_time, limit=1000):
    url = "https://api.binance.com/api/v3/klines"
    params = {
        "symbol": symbol,
        "interval": interval,
        "limit": limit,
        "startTime": start_time,
        "endTime": end_time
    }
    response = requests.get(url, params=params)
    data = response.json()
    return data

def fetch_data(symbol, interval, days):
    if os.path.exists(data_file):
        print(f"Loading cached data from {data_file}")
        df = pd.read_csv(data_file, index_col=0, parse_dates=True)
        return df

    print(f"Fetching {days} days of data for {symbol} @ {interval}...")

    end_time = int(time.time() * 1000)
    start_time = end_time - days * 24 * 60 * 60 * 1000
    all_klines = []

    total_klines_needed = days * 24 * 4  # 4 x 15min intervals per hour
    fetched_klines = 0

    while start_time < end_time:
        klines = get_klines(symbol, interval, start_time, end_time, limit_per_request)
        if not klines:
            break
        all_klines += klines
        fetched_klines += len(klines)
        start_time = klines[-1][0] + 1

        progress = (fetched_klines / total_klines_needed) * 100
        print(f"Progress: {progress:.2f}% ({fetched_klines} klines fetched)", end='\r')

        time.sleep(0.25)  # rate limit

        if len(klines) < limit_per_request:
            break  # no more data

    print("\nFinished fetching data.")

    df = pd.DataFrame(all_klines, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_asset_volume", "number_of_trades",
        "taker_buy_base_volume", "taker_buy_quote_volume", "ignore"
    ])
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")
    df.set_index("open_time", inplace=True)
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)

    # Save for later use
    df.to_csv(data_file)
    print(f"Saved data to {data_file}")

    return df

# --- MANUAL VWAP ---
def add_vwap(df, window=14):
    typical_price = (df['high'] + df['low'] + df['close']) / 3
    pv = typical_price * df['volume']
    vwap = pv.rolling(window=window).sum() / df['volume'].rolling(window=window).sum()
    df['vwap'] = vwap
    return df

# --- INDICATORS ---
def add_indicators(df):
    df["ema9"] = EMAIndicator(df["close"], window=9).ema_indicator()
    df["ema21"] = EMAIndicator(df["close"], window=21).ema_indicator()
    df["rsi"] = RSIIndicator(df["close"], window=14).rsi()
    df = add_vwap(df)
    return df

# --- STRATEGIES ---
def strategy_ema_rsi_vwap(row, prev_row):
    if prev_row is None:
        return False
    price_cross_vwap = (prev_row["close"] < prev_row["vwap"]) and (row["close"] > row["vwap"])
    ema_cross = (prev_row["ema9"] < prev_row["ema21"]) and (row["ema9"] > row["ema21"])
    rsi_cross = (prev_row["rsi"] < 30) and (row["rsi"] > 30)
    return price_cross_vwap and ema_cross and rsi_cross

def strategy_breakout_retest(df, idx):
    if idx < 13:
        return False
    window = df.iloc[idx-13:idx-1]
    resistance = window["high"].max()
    current = df.iloc[idx]
    prev = df.iloc[idx-1]
    breakout = (prev["close"] <= resistance) and (current["close"] > resistance)
    retest = (idx + 1 < len(df)) and (df.iloc[idx+1]["low"] >= resistance)
    return breakout and retest

def strategy_scalping_vwap(row, prev_row):
    if prev_row is None:
        return False
    bounce = (row["low"] <= row["vwap"] * 1.002) and (row["close"] > row["vwap"])
    rsi_good = 40 <= row["rsi"] <= 60
    return bounce and rsi_good

# --- BACKTEST ---
def backtest_strategy(df, strategy_name, config):
    balance = initial_balance
    position = 0
    buy_price = 0
    trade_log = []

    sl_hits = 0
    tp_hits = 0

    for i in range(1, len(df)-1):
        row = df.iloc[i]
        prev_row = df.iloc[i-1]

        signal = False
        if strategy_name == "EMA_RSI_VWAP":
            signal = strategy_ema_rsi_vwap(row, prev_row)
        elif strategy_name == "BREAKOUT_RETEST":
            signal = strategy_breakout_retest(df, i)
        elif strategy_name == "SCALPING_VWAP":
            signal = strategy_scalping_vwap(row, prev_row)

        if position == 0 and signal:
            buy_price = row["close"]
            position = balance / buy_price
            balance = 0
            trade_log.append((df.index[i], "BUY", buy_price))

        elif position > 0:
            current_price = row["close"]
            if current_price >= buy_price * (1 + config["take_profit_pct"]):
                balance = position * current_price
                trade_log.append((df.index[i], "SELL_TP", current_price))
                position = 0
                tp_hits += 1
            elif current_price <= buy_price * (1 - config["stop_loss_pct"]):
                balance = position * current_price
                trade_log.append((df.index[i], "SELL_SL", current_price))
                position = 0
                sl_hits += 1

    # Close position at end if still open
    if position > 0:
        last_price = df["close"].iloc[-1]
        balance = position * last_price
        trade_log.append((df.index[-1], "SELL_EOD", last_price))
        position = 0

    return balance, trade_log, sl_hits, tp_hits

# --- MAIN ---
def main():
    df = fetch_data(symbol, interval, days)
    print(f"Data loaded: {len(df)} rows")

    df = add_indicators(df)
    print("Indicators added.")

    results = []

    for strat in strategies:
        print(f"\nBacktesting strategy: {strat}")
        final_balance, trades, sl_hits, tp_hits = backtest_strategy(df, strat, config)
        net_return_pct = ((final_balance - initial_balance) / initial_balance) * 100
        num_trades = len([t for t in trades if t[1] == "BUY"])
        print(f"Initial Balance: ${initial_balance}")
        print(f"Final Balance:   ${final_balance:.2f}")
        print(f"Net Return:      {net_return_pct:.2f}%")
        print(f"Total trades:    {num_trades}")
        print(f"Take-Profit hits: {tp_hits}")
        print(f"Stop-Loss hits:   {sl_hits}")

        results.append({
            "strategy": strat,
            "initial_balance": initial_balance,
            "final_balance": final_balance,
            "net_return_pct": net_return_pct,
            "total_trades": num_trades,
            "tp_hits": tp_hits,
            "sl_hits": sl_hits,
            "trade_log": trades
        })

    # Print summary table
    summary_df = pd.DataFrame(results)
    print("\nSummary of all strategies:")
    print(summary_df[["strategy", "initial_balance", "final_balance", "net_return_pct", "total_trades", "tp_hits", "sl_hits"]])

if __name__ == "__main__":
    main()
