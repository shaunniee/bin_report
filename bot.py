import requests
import pandas as pd
import time
from ta.momentum import RSIIndicator

# --- CONFIGURATION ---
symbol = "BTCUSDT"
interval = "15m"
days = 60
limit_per_request = 1000

# --- HAMMER PATTERN DETECTION ---
def is_hammer(o, h, l, c):
    body = abs(c - o)
    candle_range = h - l
    lower_shadow = min(o, c) - l
    upper_shadow = h - max(o, c)
    return body < candle_range * 0.3 and lower_shadow > body * 2 and upper_shadow < body

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
    df = df.astype(float)
    return df

# --- GET HISTORICAL DATA ---
print("Fetching data...")
df = fetch_data(symbol, interval, days)

# --- INDICATORS ---
df["rsi"] = RSIIndicator(df["close"], window=14).rsi()
df["hammer"] = df.apply(lambda row: is_hammer(row["open"], row["high"], row["low"], row["close"]), axis=1)
df["buy_signal"] = (df["hammer"]) & (df["rsi"] < 30)

# --- BACKTEST LOGIC ---
initial_balance = 10000
balance = initial_balance
position = 0
buy_price = 0
trade_log = []

for i in range(1, len(df)):
    row = df.iloc[i]
    time = df.index[i]

    if position == 0 and df["buy_signal"].iloc[i]:
        buy_price = row["close"]
        position = balance / buy_price
        balance = 0
        trade_log.append((time, "BUY", buy_price))

    elif position > 0:
        current_price = row["close"]
        if current_price >= buy_price * 1.03:
            balance = position * current_price
            trade_log.append((time, "SELL_TP", current_price))
            position = 0
        elif current_price <= buy_price * 0.98:
            balance = position * current_price
            trade_log.append((time, "SELL_SL", current_price))
            position = 0

# Final position value
if position > 0:
    balance = position * df["close"].iloc[-1]
    trade_log.append((df.index[-1], "SELL_EOD", df['close'].iloc[-1]))

# --- RESULTS ---
final_balance = balance
print(f"\nInitial Balance: ${initial_balance}")
print(f"Final Balance:   ${final_balance:.2f}")
print(f"Net Return:      {((final_balance - initial_balance)/initial_balance)*100:.2f}%")
print(f"Trades executed: {len(trade_log)//2}\n")

# --- TRADE LOG ---
for t in trade_log:
    print(f"{t[0]} | {t[1]} @ {t[2]:.2f}")
