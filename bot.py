import os
import ccxt
import time
import pymongo
import pandas as pd
import requests
import threading
import functools
from ta.trend import EMAIndicator, MACD, ADXIndicator
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
active_collection = db["active_trades"]
trade_logs_collection = db["trade_logs"]
from logger import init_logger,log_skipped_signal,log_successful_buy,log_trade_pnl,summarize_skipped_signals,weekly_signal_summary
init_logger(db)

# Binance testnet setup
exchange = ccxt.binance({
    "apiKey": API_KEY,
    "secret": API_SECRET,
    "enableRateLimit": True,
    "options": {"defaultType": "spot"},
})
exchange.set_sandbox_mode(True)
markets = exchange.load_markets()

# Retry decorator
def retry_on_exception(max_retries=3, delay=5):
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    print(f"Error: {e}. Retrying {func.__name__} ({attempt+1}/{max_retries})...")
                    time.sleep(delay)
            raise Exception(f"Failed after {max_retries} retries: {func.__name__}")
        return wrapper
    return decorator

@retry_on_exception()
def fetch_ohlcv(symbol="BTC/USDT", timeframe="5m", limit=100):
    data = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    df = pd.DataFrame(data, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    return df

def apply_indicators(df):
    df["EMA9"] = EMAIndicator(close=df["close"], window=9).ema_indicator()
    df["EMA21"] = EMAIndicator(close=df["close"], window=21).ema_indicator()
    df["RSI"] = RSIIndicator(close=df["close"], window=14).rsi()
    df["ATR"] = AverageTrueRange(high=df["high"], low=df["low"], close=df["close"], window=14).average_true_range()
    df["VWAP"] = (df["volume"] * (df["high"] + df["low"] + df["close"]) / 3).cumsum() / df["volume"].cumsum()
    macd = MACD(close=df["close"])
    df["MACD"] = macd.macd()
    df["MACD_signal"] = macd.macd_signal()
    df["MACD_diff"] = macd.macd_diff()
    adx = ADXIndicator(high=df["high"], low=df["low"], close=df["close"], window=14)
    df["ADX"] = adx.adx()
    df["OBV"] = (df["volume"] * ((df["close"] > df["close"].shift(1)) * 2 - 1)).cumsum()
    df["Volume_SMA"] = df["volume"].rolling(window=20).mean()
    df["Volume_Spike"] = df["volume"] > 1.5 * df["Volume_SMA"]
    return df

def detect_market_regime(df):
    return "trending" if df["ADX"].iloc[-1] > 25 else "ranging"

def get_adaptive_rsi_bounds(df):
    atr = df["ATR"].iloc[-1]
    atr_mean = df["ATR"].rolling(window=20).mean().iloc[-1]
    return (45, 65) if atr > atr_mean else (40, 70)

def get_recent_pnl(limit=10):
    trades = list(trade_logs_collection.find().sort("timestamp", -1).limit(limit))
    return sum(trade.get("pnl", 0) for trade in trades)

def should_pause_trading():
    return get_recent_pnl() < -50

def should_buy(df):
    latest = df.iloc[-1]
    regime = detect_market_regime(df)
    rsi_lower, rsi_upper = get_adaptive_rsi_bounds(df)

    passed = []
    failed = []

    if regime == "trending":
        passed.append("Regime: trending (ADX > 25)")
    else:
        failed.append("Regime: not trending (ADX ≤ 25)")

    if latest["EMA9"] > latest["EMA21"]:
        passed.append("EMA: EMA9 > EMA21")
    else:
        failed.append("EMA: EMA9 ≤ EMA21")

    if rsi_lower < latest["RSI"] < rsi_upper:
        passed.append(f"RSI in bounds: {latest['RSI']:.2f}")
    else:
        failed.append(f"RSI out of bounds: {latest['RSI']:.2f} not in ({rsi_lower}, {rsi_upper})")

    if latest["close"] > latest["VWAP"]:
        passed.append("Close > VWAP")
    else:
        failed.append("Close ≤ VWAP")

    if latest["ADX"] > 20:
        passed.append("ADX > 20")
    else:
        failed.append("ADX ≤ 20")

    if latest["Volume_Spike"]:
        passed.append("Volume spike detected")
    else:
        failed.append("No volume spike")

    if latest["OBV"] > df["OBV"].iloc[-2]:
        passed.append("OBV increasing")
    else:
        failed.append("OBV not increasing")

    should_buy = len(failed) == 0
    return should_buy, passed, failed


def should_sell(df, entry_price):
    latest = df.iloc[-1]
    regime = detect_market_regime(df)
    atr = latest["ATR"]
    rsi = latest["RSI"]
    price = latest["close"]
    rsi_lower, rsi_upper = get_adaptive_rsi_bounds(df)
    if regime == "trending":
        return (
            latest["EMA9"] < latest["EMA21"]
            or rsi > rsi_upper
            or price < entry_price - 1.5 * atr
            or latest["MACD_diff"] < 0
            or latest["ADX"] < 20
        )
    else:
        return rsi > rsi_upper or price < entry_price - 1.0 * atr or price < latest["VWAP"]
@retry_on_exception()
def execute_trade(symbol, side, amount):
    return exchange.create_market_order(symbol, side, amount)

@retry_on_exception()
def get_balance(asset="USDT"):
    balance = exchange.fetch_balance()
    return balance[asset]["free"]

@retry_on_exception()
def fetch_price(symbol):
    return exchange.fetch_ticker(symbol)["last"]

def get_precision(symbol):
    return markets[symbol]["precision"]["amount"]

def send_telegram(message):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": message})
    except Exception as e:
        print(f"Telegram Error: {e}")

def mark_trade_active(symbol):
    active_collection.update_one({"symbol": symbol}, {"$set": {"active": True}}, upsert=True)

def unmark_trade_active(symbol):
    active_collection.delete_one({"symbol": symbol})

def is_trade_active(symbol):
    return active_collection.find_one({"symbol": symbol}) is not None

def load_positions():
    return {pos["symbol"]: pos for pos in positions_collection.find()}

def save_position(symbol, position):
    positions_collection.update_one({"symbol": symbol}, {"$set": position}, upsert=True)

def delete_position(symbol):
    positions_collection.update_one(
        {"symbol": symbol},
        {"$set": {"last_closed": datetime.now(timezone.utc).isoformat()}},
        upsert=True,
    )

def is_in_cooldown(symbol, cooldown_minutes=60):
    pos = positions_collection.find_one({"symbol": symbol})
    if pos and "last_closed" in pos:
        last_closed = datetime.fromisoformat(pos["last_closed"])
        return datetime.now(timezone.utc) < last_closed + timedelta(minutes=cooldown_minutes)
    return False

def confirm_higher_timeframe(symbol):
    df_15m = fetch_ohlcv(symbol, timeframe="15m", limit=50)
    df_15m = apply_indicators(df_15m)
    latest = df_15m.iloc[-1]
    return latest["EMA9"] > latest["EMA21"]

def log_trade(symbol, side, amount, price, pnl):
    trade_logs_collection.insert_one({
        "symbol": symbol,
        "side": side,
        "amount": amount,
        "price": price,
        "pnl": pnl,
        "timestamp": datetime.now(timezone.utc).isoformat()
    })
    log_trade_pnl(symbol,exit_price=price,pnl=pnl)

def trade_symbol(symbol, per_trade_usdt, base_asset="USDT"):
    if is_trade_active(symbol):
        print(f"Trade already active for {symbol}. Skipping.")
        return
    if is_in_cooldown(symbol):
        print(f"{symbol} is in cooldown. Skipping.")
        return
    if should_pause_trading():
        print("Trading paused due to recent losses.")
        return

    mark_trade_active(symbol)
    try:
        positions = load_positions()
        if symbol in positions:
            print(f"Position already open for {symbol}. Skipping.")
            return

        df = fetch_ohlcv(symbol)
        df = apply_indicators(df)
        should_buy_flag,passed_reasons,skip_reasons=should_buy(df)
        if should_buy(df) and confirm_higher_timeframe(symbol):
            log_successful_buy(symbol,passed_reasons)
            precision = get_precision(symbol)
            amount = round(per_trade_usdt / df["close"].iloc[-1], precision)
            order = execute_trade(symbol, "buy", amount)
            buy_price = df["close"].iloc[-1]
            atr = df["ATR"].iloc[-1]

            stop_loss = buy_price - 1.5 * atr
            take_profit = buy_price + 2.5 * atr
            trailing_stop = buy_price - 1.0 * atr

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
            sold = False

            while not sold:
                df = fetch_ohlcv(symbol)
                df = apply_indicators(df)
                price = fetch_price(symbol)

                trailing_stop = max(trailing_stop, price - 1.0 * atr)

                if price >= take_profit:
                    execute_trade(symbol, "sell", amount)
                    pnl = (price - buy_price) * amount
                    send_telegram(f"✅ {symbol} TP Hit: Sold @ {price:.2f} | PnL: {pnl:.2f} USDT")
                    log_trade(symbol, "sell", amount, price, pnl)
                    sold = True

                elif price <= stop_loss:
                    execute_trade(symbol, "sell", amount)
                    pnl = (price - buy_price) * amount
                    send_telegram(f"🛑 {symbol} SL Hit: Sold @ {price:.2f} | PnL: {pnl:.2f} USDT")
                    log_trade(symbol, "sell", amount, price, pnl)
                    sold = True

                elif price <= trailing_stop:
                    execute_trade(symbol, "sell", amount)
                    pnl = (price - buy_price) * amount
                    send_telegram(f"🔻 {symbol} Trailing Stop Hit: Sold @ {price:.2f} | PnL: {pnl:.2f} USDT")
                    log_trade(symbol, "sell", amount, price, pnl)
                    sold = True

                elif should_sell(df, buy_price):
                    execute_trade(symbol, "sell", amount)
                    pnl = (price - buy_price) * amount
                    send_telegram(f"🔻 {symbol} Sell Signal: Sold @ {price:.2f} | PnL: {pnl:.2f} USDT")
                    log_trade(symbol, "sell", amount, price, pnl)
                    sold = True

                elif datetime.now(timezone.utc) > start_time + timedelta(hours=2):
                    execute_trade(symbol, "sell", amount)
                    pnl = (price - buy_price) * amount
                    send_telegram(f"⏳ {symbol} Timeout (2h): Sold @ {price:.2f} | PnL: {pnl:.2f} USDT")
                    log_trade(symbol, "sell", amount, price, pnl)
                    sold = True

                time.sleep(30)

            delete_position(symbol)

        else:
            if not should_buy_flag:
                msg = f"⚠️ Skipped {symbol} - No Buy Signal.\nReasons:\n- " + "\n- ".join(skip_reasons)
                send_telegram(msg)
                log_skipped_signal(symbol, skip_reasons)
            else:
                msg = f"⚠️ Skipped {symbol} - Higher timeframe not confirmed (15m EMA9 ≤ EMA21)"
                send_telegram(msg)
                log_skipped_signal(symbol, ["Higher timeframe not confirmed (15m EMA9 ≤ EMA21)"])

    except Exception as e:
        print(e)
        send_telegram(f"⚠️ Error with {symbol}: {str(e)}")
    finally:
        unmark_trade_active(symbol)

def run_bot():
    symbols = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT"]
    usdt_balance = get_balance("USDT")
    per_trade_usdt = (usdt_balance * 0.98) / len(symbols)
    threads = []
    if datetime.utcnow().weekday() == 6:  # Run on Sundays
        send_telegram(summarize_skipped_signals())
        send_telegram(weekly_signal_summary())
    for symbol in symbols:
        thread = threading.Thread(target=trade_symbol, args=(symbol, per_trade_usdt))
        thread.start()
        threads.append(thread)
        time.sleep(2)
    for thread in threads:
        thread.join()

# Continuous loop
while True:
    run_bot()
    time.sleep(300)  # Wait 5 minutes between cycles
