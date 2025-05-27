import os
import ccxt
import time
import pymongo
import pandas as pd
import requests
import threading
from ta.trend import EMAIndicator
from ta.momentum import RSIIndicator
from ta.volatility import AverageTrueRange
from dotenv import load_dotenv
from datetime import datetime, timedelta, timezone

# Load environment variables
load_dotenv()
API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
MONGO_URI = os.getenv("MONGO_URI")

# MongoDB setup
client = pymongo.MongoClient(MONGO_URI)
db = client["tradingbot"]
positions_collection = db["positions"]

# Binance testnet setup
exchange = ccxt.binance({
    'apiKey': API_KEY,
    'secret': API_SECRET,
    'enableRateLimit': True,
    'options': {'defaultType': 'spot'}
})
exchange.set_sandbox_mode(True)

# Globals
active_trades = set()

# Telegram alert function
def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": message})

# MongoDB position functions
def load_positions():
    return {pos["symbol"]: pos for pos in positions_collection.find()}

def save_position(symbol, position):
    positions_collection.update_one({"symbol": symbol}, {"$set": position}, upsert=True)

def delete_position(symbol):
    positions_collection.delete_one({"symbol": symbol})

# Fetch OHLCV data
def fetch_ohlcv(symbol="BTC/USDT", timeframe='5m', limit=100):
    data = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    df = pd.DataFrame(data, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    return df

# Apply indicators
def apply_indicators(df):
    df["EMA9"] = EMAIndicator(close=df["close"], window=9).ema_indicator()
    df["EMA21"] = EMAIndicator(close=df["close"], window=21).ema_indicator()
    df["RSI"] = RSIIndicator(close=df["close"], window=14).rsi()
    df["ATR"] = AverageTrueRange(high=df["high"], low=df["low"], close=df["close"], window=14).average_true_range()
    df["VWAP"] = (df["volume"] * (df["high"] + df["low"] + df["close"]) / 3).cumsum() / df["volume"].cumsum()
    return df

# Buy condition
def should_buy(df):
    latest = df.iloc[-1]
    return (
        latest["EMA9"] > latest["EMA21"] and
        40 < latest["RSI"] < 70 and
        latest["close"] > latest["VWAP"]
    )

# Sell condition
def should_sell(df, entry_price):
    latest = df.iloc[-1]
    return (
        latest["EMA9"] < latest["EMA21"] or
        latest["RSI"] > 70 or
        latest["close"] < latest["VWAP"] or
        latest["close"] < entry_price - 1.5 * latest["ATR"]
    )

# Execute trade
def execute_trade(symbol, side, amount):
    return exchange.create_market_order(symbol, side, amount)

# Get balance
def get_balance(asset="USDT"):
    balance = exchange.fetch_balance()
    return balance[asset]["free"]

# Trade logic
def trade_symbol(symbol, per_trade_usdt, base_asset="USDT"):
    if symbol in active_trades:
        print(f"Trade already active for {symbol}. Skipping.")
        return

    active_trades.add(symbol)
    positions = load_positions()

    try:
        if symbol in positions:
            print(f"Position already open for {symbol}. Skipping.")
            return

        df = fetch_ohlcv(symbol)
        df = apply_indicators(df)

        if should_buy(df):
            amount = per_trade_usdt / df["close"].iloc[-1]
            amount = round(amount, 5)
            order = execute_trade(symbol, "buy", amount)
            buy_price = df["close"].iloc[-1]
            stop_loss = buy_price * 0.994
            take_profit = buy_price * 1.012

            position = {
                "symbol": symbol,
                "amount": amount,
                "buy_price": buy_price,
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            save_position(symbol, position)

            send_telegram(f"📈 {symbol} Buy @ {buy_price:.2f} | TP: {take_profit:.2f}, SL: {stop_loss:.2f}")

            start_time = datetime.now(timezone.utc)
            while True:
                df = fetch_ohlcv(symbol)
                df = apply_indicators(df)
                price = exchange.fetch_ticker(symbol)["last"]

                if price >= take_profit:
                    execute_trade(symbol, "sell", amount)
                    send_telegram(f"✅ {symbol} TP Hit: Sold @ {price:.2f}")
                    break
                elif price <= stop_loss:
                    execute_trade(symbol, "sell", amount)
                    send_telegram(f"🛑 {symbol} SL Hit: Sold @ {price:.2f}")
                    break
                elif should_sell(df, buy_price):
                    execute_trade(symbol, "sell", amount)
                    send_telegram(f"🔻 {symbol} Sell Signal: Sold @ {price:.2f}")
                    break
                elif datetime.now(timezone.utc) > start_time + timedelta(hours=2):
                    execute_trade(symbol, "sell", amount)
                    send_telegram(f"⏳ {symbol} Timeout (2h): Sold @ {price:.2f}")
                    break

                time.sleep(30)

            delete_position(symbol)
        else:
            print(f"No buy signal for {symbol}.")
    except Exception as e:
        send_telegram(f"⚠️ Error with {symbol}: {str(e)}")
    finally:
        active_trades.remove(symbol)

# Run bot
def run_bot():
    symbols = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT"]
    usdt_balance = get_balance("USDT")
    per_trade_usdt = (usdt_balance * 0.98) / len(symbols)
    threads = []

    for symbol in symbols:
        thread = threading.Thread(target=trade_symbol, args=(symbol, per_trade_usdt))
        thread.start()
        threads.append(thread)

    for thread in threads:
        thread.join()

# Continuous loop
while True:
    run_bot()
    time.sleep(300)  # 5 minutes
