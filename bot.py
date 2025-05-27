import os
import ccxt
import time
import pandas as pd
import numpy as np
import requests
from ta.trend import ema_indicator
from ta.momentum import rsi
from ta.volatility import average_true_range
from ta.volume import volume_weighted_average_price as vwap
from dotenv import load_dotenv
from datetime import datetime, timedelta

# Load environment variables
load_dotenv()
API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Binance testnet setup
exchange = ccxt.binance({
    'apiKey': API_KEY,
    'secret': API_SECRET,
    'enableRateLimit': True,
    'options': {'defaultType': 'spot'}
})
exchange.set_sandbox_mode(True)

# Telegram alert function
def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": message})

# Fetch recent OHLCV data
def fetch_ohlcv(symbol="BTC/USDT", timeframe='5m', limit=100):
    data = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    df = pd.DataFrame(data, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    return df

# Apply technical indicators
def apply_indicators(df):
    df["EMA9"] = ema_indicator(df["close"], window=9)
    df["EMA21"] = ema_indicator(df["close"], window=21)
    df["RSI"] = rsi(df["close"], window=14)
    df["VWAP"] = vwap(df["high"], df["low"], df["close"], df["volume"])
    df["ATR"] = average_true_range(df["high"], df["low"], df["close"], window=14)
    return df

# Check buy conditions
def should_buy(df):
    latest = df.iloc[-1]
    return (
        latest["EMA9"] > latest["EMA21"] and
        40 < latest["RSI"] < 70 and
        latest["close"] > latest["VWAP"]
    )

# Execute market trade
def execute_trade(symbol, side, amount):
    return exchange.create_market_order(symbol, side, amount)

# Get available USDT balance
def get_balance(asset="USDT"):
    balance = exchange.fetch_balance()
    return balance[asset]["free"]

# Bot logic for multiple symbols
def run_bot():
    symbols = ["BTC/USDT", "ETH/USDT"]
    base_asset = "USDT"

    for symbol in symbols:
        try:
            df = fetch_ohlcv(symbol)
            df = apply_indicators(df)

            if should_buy(df):
                usdt_balance = get_balance(base_asset)
                if usdt_balance > 10:
                    amount = (usdt_balance * 0.98 / len(symbols)) / df["close"].iloc[-1]
                    amount = round(amount, 5)
                    order = execute_trade(symbol, "buy", amount)
                    buy_price = df["close"].iloc[-1]
                    stop_loss = buy_price * 0.994
                    take_profit = buy_price * 1.012

                    send_telegram(f"📈 {symbol} Buy @ {buy_price:.2f} | TP: {take_profit:.2f}, SL: {stop_loss:.2f}")

                    start_time = datetime.utcnow()
                    while True:
                        price = exchange.fetch_ticker(symbol)["last"]
                        if price >= take_profit:
                            execute_trade(symbol, "sell", amount)
                            send_telegram(f"✅ {symbol} TP Hit: Sold @ {price:.2f}")
                            break
                        elif price <= stop_loss:
                            execute_trade(symbol, "sell", amount)
                            send_telegram(f"🛑 {symbol} SL Hit: Sold @ {price:.2f}")
                            break
                        elif datetime.utcnow() > start_time + timedelta(minutes=45):
                            execute_trade(symbol, "sell", amount)
                            send_telegram(f"⏳ {symbol} Timeout: Sold @ {price:.2f}")
                            break
                        time.sleep(30)
                else:
                    print(f"Not enough {base_asset} balance for {symbol}.")
            else:
                print(f"No buy signal for {symbol}.")
        except Exception as e:
            send_telegram(f"⚠️ Error with {symbol}: {str(e)}")

# Run the bot continuously
while True:
    run_bot()
    time.sleep(300)  # 5 minutes
