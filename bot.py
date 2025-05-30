import requests
import pandas as pd
import time
import numpy as np
from ta.momentum import RSIIndicator
from ta.trend import EMAIndicator, VWAP

# --- CONFIGURATION ---
symbol = "BTCUSDT"
interval = "15m"
days = 60
limit_per_request = 1000
initial_balance = 10000

config = {
    "use_ema_rsi_vwap": True,
    "use_breakout_retest": True,
    "use_scalping_vwap": True,
    "stop_loss_pct": 0.02,  # 2% stop loss
    "take_profit_pct": 0.03,  # 3% take profit
}

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
    end_time = int(time.time() * 1000)
    start_time = end_time - days * 24 * 60 * 60 * 1000
    all_klines = []

    while start_time < end_time:
        klines = get_klines(symbol, interval, start_time, end_time, limit_per_request)
        if not klines:
            break
        all_klines += klines
        start_time = klines[-1][0] + 1
        time.sleep(0.25)  # avoid rate limits

    df = pd.DataFrame(all_klines, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_asset_volume", "number_of_trades",
        "taker_buy_base_volume", "taker_buy_quote_volume", "ignore"
    ])
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")
    df.set_index("open_time", inplace=True)
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)
    return df

# --- INDICATORS ---
def add_indicators(df):
    df["ema9"] = EMAIndicator(df["close"], window=9).ema_indicator()
    df["ema21"] = EMAIndicator(df["close"], window=21).ema_indicator()
    df["rsi"] = RSIIndicator(df["close"], window=14).rsi()
    df["vwap"] = VWAP(df["high"], df["low"], df["close"], df["volume"], window=14).vwap()
    return df

# --- STRATEGIES ---

def strategy_ema_rsi_vwap(row, prev_row):
    # Buy signal:
    # Price crosses above VWAP,
    # EMA9 crosses above EMA21,
    # RSI crosses above 30 from below

    buy = False

    if prev_row is not None:
        price_cross_vwap = (prev_row["close"] < prev_row["vwap"]) and (row["close"] > row["vwap"])
        ema_cross = (prev_row["ema9"] < prev_row["ema21"]) and (row["ema9"] > row["ema21"])
        rsi_cross = (prev_row["rsi"] < 30) and (row["rsi"] > 30)

        if price_cross_vwap and ema_cross and rsi_cross:
            buy = True
    return buy

def strategy_breakout_retest(df, idx):
    # Look back 12 periods for range,
    # Buy if price breaks above resistance and retests support within 1 bar after breakout

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
    # Buy signal:
    # Price bounces off VWAP (low touches or close near VWAP),
    # and RSI is between 40 and 60 to catch small momentum
    
    if prev_row is None:
        return False

    bounce = (row["low"] <= row["vwap"] * 1.002) and (row["close"] > row["vwap"])
    rsi_good = 40 <= row["rsi"] <= 60

    return bounce and rsi_good

# --- BACKTESTING FUNCTION ---
def backtest(df, config):
    balance = initial_balance
    position = 0
    buy_price = 0
    trade_log = []

    for i in range(1, len(df)-1):
        row = df.iloc[i]
        prev_row = df.iloc[i-1]

        # Check buy signals per strategy
        signals = []

        if config["use_ema_rsi_vwap"] and strategy_ema_rsi_vwap(row, prev_row):
            signals.append("EMA_RSI_VWAP")

        if config["use_breakout_retest"] and strategy_breakout_retest(df, i):
            signals.append("BREAKOUT_RETEST")

        if config["use_scalping_vwap"] and strategy_scalping_vwap(row, prev_row):
            signals.append("SCALPING_VWAP")

        if position == 0 and signals:
            buy_price = row["close"]
            position = balance / buy_price
            balance = 0
            trade_log.append((df.index[i], "BUY", buy_price, signals))

        elif position > 0:
            current_price = row["close"]
            # Check TP and SL
            if current_price >= buy_price * (1 + config["take_profit_pct"]):
                balance = position * current_price
                trade_log.append((df.index[i], "SELL_TP", current_price, None))
                position = 0
            elif current_price <= buy_price * (1 - config["stop_loss_pct"]):
                balance = position * current_price
                trade_log.append((df.index[i], "SELL_SL", current_price, None))
                position = 0

    # If still holding at end, sell at last close
    if position > 0:
        last_price = df["close"].iloc[-1]
        balance = position * last_price
        trade_log.append((df.index[-1], "SELL_EOD", last_price, None))
        position = 0

    return balance, trade_log

# --- RUN BACKTEST ---
print("Fetching data from Binance...")
df = fetch_data(symbol, interval, days)
print(f"Fetched {len(df)} rows")
df = add_indicators(df)
print("Indicators added.")

final_balance, trades = backtest(df, config)

print(f"\nInitial Balance: ${initial_balance}")
print(f"Final Balance:   ${final_balance:.2f}")
print(f"Net Return:      {((final_balance - initial_balance)/initial_balance)*100:.2f}%")
print(f"Total trades:    {len(trades)//2}")

print("\nTrade Log:")
for t in trades:
    date_str = t[0].strftime("%Y-%m-%d %H:%M")
    action = t[1]
    price = t[2]
    signals = t[3]
    if signals:
        print(f"{date_str} | {action} @ {price:.2f} | Signals: {', '.join(signals)}")
    else:
        print(f"{date_str} | {action} @ {price:.2f}")
