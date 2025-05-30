import requests
import pandas as pd
import numpy as np
import datetime

# Config
symbol = "BTCUSDT"
interval = "15m"
days = 365  # 1 year
initial_balance = 1000.0

config = {
    "stop_loss_pct": 0.10,   # 10% stop loss
    "take_profit_pct": 0.03, # 3% take profit
}

# 1. Fetch historical klines from Binance public API
def fetch_data(symbol, interval, days):
    limit = 1000  # max per request
    end_time = int(datetime.datetime.now().timestamp() * 1000)
    start_time = end_time - days * 24 * 60 * 60 * 1000

    all_klines = []

    while start_time < end_time:
        url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&startTime={start_time}&limit={limit}"
        data = requests.get(url).json()
        if not data:
            break

        all_klines.extend(data)

        last_time = data[-1][0]
        start_time = last_time + 1

        if len(data) < limit:
            break

    # Create DataFrame
    df = pd.DataFrame(all_klines, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_asset_volume", "number_of_trades",
        "taker_buy_base", "taker_buy_quote", "ignore"
    ])

    # Convert types
    df["open_time"] = pd.to_datetime(df["open_time"], unit='ms')
    df["close_time"] = pd.to_datetime(df["close_time"], unit='ms')
    df[["open", "high", "low", "close", "volume"]] = df[["open", "high", "low", "close", "volume"]].astype(float)
    return df

# 2. Add Indicators (EMA, RSI, VWAP)
def add_indicators(df):
    df = df.copy()
    df["EMA20"] = df["close"].ewm(span=20, adjust=False).mean()

    # RSI calculation
    delta = df["close"].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    df["RSI"] = 100 - (100 / (1 + rs))

    # VWAP calculation
    typical_price = (df["high"] + df["low"] + df["close"]) / 3
    cum_vol_price = (typical_price * df["volume"]).cumsum()
    cum_volume = df["volume"].cumsum()
    df["VWAP"] = cum_vol_price / cum_volume

    return df

# 3. Strategies

# EMA_RSI_VWAP: buy if close > EMA20, RSI < 30, close > VWAP
def strategy_ema_rsi_vwap(row, prev_row=None):
    return (row["close"] > row["EMA20"]) and (row["RSI"] < 30) and (row["close"] > row["VWAP"])

# BREAKOUT_RETEST: buy if close breaks above previous high and retests support (simple version)
def strategy_breakout_retest(df, idx):
    if idx < 2:
        return False
    prev_high = df["high"].iloc[idx-2]
    prev_close = df["close"].iloc[idx-1]
    current_close = df["close"].iloc[idx]

    breakout = (current_close > prev_high)
    retest = (prev_close < prev_high) and (current_close > prev_close)
    return breakout and retest

# SCALPING_VWAP: buy if price crosses VWAP from below
def strategy_scalping_vwap(row, prev_row):
    if prev_row is None:
        return False
    crossed = (prev_row["close"] < prev_row["VWAP"]) and (row["close"] > row["VWAP"])
    return crossed

# 4. Backtest single strategy
def backtest_strategy(df, strategy_func, config, strategy_name):
    balance = initial_balance
    position = 0
    buy_price = 0
    trades = []

    wins = 0
    losses = 0
    total_profit = 0

    for i in range(1, len(df)-1):
        row = df.iloc[i]
        prev_row = df.iloc[i-1]

        # Signal depends on strategy signature
        if strategy_name == "EMA_RSI_VWAP":
            signal = strategy_func(row, prev_row)
        else:
            signal = strategy_func(df, i)

        if position == 0 and signal:
            buy_price = row["close"]
            position = balance / buy_price
            balance = 0
            trades.append(("BUY", df.index[i], buy_price))

        elif position > 0:
            current_price = row["close"]
            if current_price >= buy_price * (1 + config["take_profit_pct"]):
                profit = (current_price - buy_price) * position
                total_profit += profit
                wins += 1
                balance = position * current_price
                trades.append(("SELL_TP", df.index[i], current_price))
                position = 0

            elif current_price <= buy_price * (1 - config["stop_loss_pct"]):
                profit = (current_price - buy_price) * position
                total_profit += profit
                losses += 1
                balance = position * current_price
                trades.append(("SELL_SL", df.index[i], current_price))
                position = 0

    # Close any open position at the end
    if position > 0:
        last_price = df["close"].iloc[-1]
        profit = (last_price - buy_price) * position
        total_profit += profit
        if profit > 0:
            wins += 1
        else:
            losses += 1
        balance = position * last_price
        trades.append(("SELL_EOD", df.index[-1], last_price))
        position = 0

    total_trades = wins + losses
    win_rate = (wins / total_trades * 100) if total_trades > 0 else 0

    return {
        "strategy": strategy_name,
        "final_balance": balance,
        "total_profit": total_profit,
        "total_trades": total_trades,
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "trades": trades
    }

def main():
    print(f"Fetching {symbol} {interval} data for last {days} days...")
    df = fetch_data(symbol, interval, days)
    print(f"Data fetched: {len(df)} rows")

    df = add_indicators(df)
    print("Indicators added.")

    results = []
    results.append(backtest_strategy(df, strategy_ema_rsi_vwap, config, "EMA_RSI_VWAP"))
    results.append(backtest_strategy(df, strategy_breakout_retest, config, "BREAKOUT_RETEST"))
    results.append(backtest_strategy(df, strategy_scalping_vwap, config, "SCALPING_VWAP"))

    print(f"\nInitial Balance: ${initial_balance}\n")

    for res in results:
        print(f"Strategy: {res['strategy']}")
        print(f"  Final Balance: ${res['final_balance']:.2f}")
        print(f"  Total Profit:  ${res['total_profit']:.2f}")
        print(f"  Trades:        {res['total_trades']}")
        print(f"  Wins:          {res['wins']}")
        print(f"  Losses:        {res['losses']}")
        print(f"  Win Rate:      {res['win_rate']:.2f}%")
        print("-" * 40)

if __name__ == "__main__":
    main()
