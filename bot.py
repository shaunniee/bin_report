import os
import asyncio
from binance.client import Client
from binance.enums import *
from telegram import Bot
from datetime import datetime, timedelta
from pymongo import MongoClient

# === CONFIGURATION ===
API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
MONGO_URI = os.getenv("MONGO_URI")

SYMBOL = "XRPUSDT"

# === INIT ===
client = Client(API_KEY, API_SECRET)
client.API_URL = 'https://testnet.binance.vision/api'
bot = Bot(token=TELEGRAM_TOKEN)

mongo_client = MongoClient(MONGO_URI)
db = mongo_client["trading_bot"]
trades_collection = db["trades"]

# === UTILS ===
async def send_telegram(msg):
    await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=msg)

def fetch_trades():
    trades = client.get_my_trades(symbol=SYMBOL)
    return trades

def log_trades_to_db(trades):
    for trade in trades:
        trade["timestamp"] = datetime.utcfromtimestamp(trade["time"] / 1000)
        trades_collection.insert_one(trade)

def calculate_pnl(trades):
    buy_trades = [t for t in trades if t['isBuyer']]
    sell_trades = [t for t in trades if not t['isBuyer']]
    total_buy_cost = sum(float(t['qty']) * float(t['price']) for t in buy_trades)
    total_sell_value = sum(float(t['qty']) * float(t['price']) for t in sell_trades)
    return total_sell_value - total_buy_cost

def calculate_win_loss_ratio(trades):
    buy_trades = [t for t in trades if t['isBuyer']]
    sell_trades = [t for t in trades if not t['isBuyer']]
    wins = 0
    losses = 0
    for sell in sell_trades:
        sell_price = float(sell['price'])
        buy_prices = [float(buy['price']) for buy in buy_trades if buy['time'] < sell['time']]
        avg_buy_price = sum(buy_prices) / len(buy_prices) if buy_prices else 0
        if sell_price > avg_buy_price:
            wins += 1
        else:
            losses += 1
    win_loss_ratio = wins / losses if losses > 0 else float('inf')
    return wins, losses, win_loss_ratio

def generate_report(trades, period_name):
    total_trades = len(trades)
    total_volume = sum(float(t['qty']) * float(t['price']) for t in trades)
    total_fees = sum(float(t['commission']) for t in trades)
    pnl = calculate_pnl(trades)
    wins, losses, win_loss_ratio = calculate_win_loss_ratio(trades)

    report = (
        f"📊 {period_name} Trading Report\n"
        f"----------------------------------------\n"
        f"🛒 Trades Executed: {total_trades}\n"
        f"💰 Total Volume: {total_volume:.2f} USDT\n"
        f"📈 Net PnL: {pnl:.2f} USDT\n"
        f"💸 Total Fees: {total_fees:.2f} USDT\n"
        f"✅ Wins: {wins} | ❌ Losses: {losses}\n"
        f"📊 Win/Loss Ratio: {win_loss_ratio:.2f}\n"
    )
    return report

async def send_reports():
    now = datetime.utcnow()
    start_of_day = datetime(now.year, now.month, now.day)
    start_of_year = datetime(now.year, 1, 1)

    daily_trades = list(trades_collection.find({"timestamp": {"$gte": start_of_day}}))
    ytd_trades = list(trades_collection.find({"timestamp": {"$gte": start_of_year}}))

    daily_report = generate_report(daily_trades, "Daily")
    ytd_report = generate_report(ytd_trades, "YTD")

    await send_telegram(daily_report)
    await send_telegram(ytd_report)

async def main():
    trades = fetch_trades()
    log_trades_to_db(trades)
    await send_reports()

# Run the main function
asyncio.run(main())
