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
days = 730  # 2 years approx
limit_per_request = 1000
initial_balance = 10000
data_file = f"{symbol}_{interval}_{days}d.csv"

strategies = ["BREAKOUT_RETEST", "SCALPING_VWAP"]

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
    profit_sl_hits = 0
    loss_sl_hits = 0

    trailing_sl_price = None

    monthly_profits = {}

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
            trailing_sl_price = buy_price * (1 - config["stop_loss_pct"])
            trade_log.append({"time": df.index[i], "type": "BUY", "price": buy_price})

        elif position > 0:
            current_price = row["close"]

            price_move_pct = (current_price - buy_price) / buy_price
            if price_move_pct >= 0.01:
                new_trailing_sl = buy_price * (1 + int(price_move_pct * 100) / 100)
                if new_trailing_sl > trailing_sl_price:
                    trailing_sl_price = new_trailing_sl

            if current_price >= buy_price * (1 + config["take_profit_pct"]):
                balance = position * current_price
                profit = (current_price - buy_price) * position
                month = df.index[i].strftime("%Y-%m")
                monthly_profits[month] = monthly_profits.get(month, 0) + profit
                trade_log.append({"time": df.index[i], "type": "SELL_TP", "price": current_price, "profit": profit})
                position = 0
                tp_hits += 1
                trailing_sl_price = None

            elif current_price <= trailing_sl_price:
                balance = position * current_price
                profit = (current_price - buy_price) * position
                month = df.index[i].strftime("%Y-%m")
                monthly_profits[month] = monthly_profits.get(month, 0) + profit
                trade_log.append({"time": df.index[i], "type": "SELL_SL", "price": current_price, "profit": profit})

                sl_hits += 1
                if current_price > buy_price:
                    profit_sl_hits += 1
                else:
                    loss_sl_hits += 1

                position = 0
                trailing_sl_price = None

    # Close position at end if still open
    if position > 0:
        last_price = df["close"].iloc[-1]
        balance = position * last_price
        profit = (last_price - buy_price) * position
        month = df.index[-1].strftime("%Y-%m")
        monthly_profits[month] = monthly_profits.get(month, 0) + profit
        trade_log.append({"time": df.index[-1], "type": "SELL_EOD", "price": last_price, "profit": profit})
        position = 0

    return balance, trade_log, sl_hits, tp_hits, profit_sl_hits, loss_sl_hits, monthly_profits


# --- MAIN ---
def main():
    df = fetch_data(symbol, interval, days)
    print(f"Data loaded: {len(df)} rows")

    df = add_indicators(df)
    print("Indicators added.")

    stop_loss_percents = [x / 100 for x in range(1, 21)]  # 1% to 20%
    take_profit_percents = [x / 100 for x in range(1, 9)]  # 1% to 8%

    all_results = []

    for strat in strategies:
        print(f"\nStrategy: {strat}")
        for sl in stop_loss_percents:
            for tp in take_profit_percents:
                config = {
                    "stop_loss_pct": sl,
                    "take_profit_pct": tp,
                }
                final_balance, trades, sl_hits, tp_hits, profit_sl_hits, loss_sl_hits, monthly_profits = backtest_strategy(df, strat, config)
                net_return_pct = ((final_balance - initial_balance) / initial_balance) * 100
                num_trades = len([t for t in trades if t["type"] == "BUY"])

                print(f"SL: {sl*100:.1f}%, TP: {tp*100:.1f}%, Final: ${final_balance:.2f}, Return: {net_return_pct:.2f}%, Trades: {num_trades}, TP hits: {tp_hits}, SL hits: {sl_hits}, Profit SL: {profit_sl_hits}, Loss SL: {loss_sl_hits}")

                all_results.append({
                    "strategy": strat,
                    "stop_loss_pct": sl,
                    "take_profit_pct": tp,
                    "final_balance": final_balance,
                    "net_return_pct": net_return_pct,
                    "num_trades": num_trades,
                    "take_profit_hits": tp_hits,
                    "stop_loss_hits": sl_hits,
                    "profit_stop_loss_hits": profit_sl_hits,
                    "loss_stop_loss_hits": loss_sl_hits,
                    "monthly_profits": monthly_profits
                })

    # Save results to CSV
    result_rows = []
    for res in all_results:
        base = {
            "strategy": res["strategy"],
            "stop_loss_pct": res["stop_loss_pct"],
            "take_profit_pct": res["take_profit_pct"],
            "final_balance": res["final_balance"],
            "net_return_pct": res["net_return_pct"],
            "num_trades": res["num_trades"],
            "take_profit_hits": res["take_profit_hits"],
            "stop_loss_hits": res["stop_loss_hits"],
            "profit_stop_loss_hits": res["profit_stop_loss_hits"],
            "loss_stop_loss_hits": res["loss_stop_loss_hits"],
        }
        for month, profit in res["monthly_profits"].items():
            base[f"profit_{month}"] = profit
        result_rows.append(base)

    df_results = pd.DataFrame(result_rows)
    df_results.to_csv("backtest_combinations_results.csv", index=False)
    print("\nSaved all results to backtest_combinations_results.csv")

    return all_results
