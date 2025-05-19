import os
import pandas as pd
from datetime import datetime, timedelta
from pymongo import MongoClient
from binance.client import Client
from binance.enums import *
from telegram import Update, Bot, InputFile
from telegram.ext import Application, CommandHandler, ContextTypes
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# === CONFIGURATION ===
API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
MONGO_URI = os.getenv("MONGO_URI")
SYMBOL = "XRPUSDT"

# === INIT ===
client = Client(API_KEY, API_SECRET)
client.API_URL = 'https://testnet.binance.vision/api'  # Remove for live trading
bot = Bot(token=TELEGRAM_TOKEN)

mongo_client = MongoClient(MONGO_URI)
db = mongo_client["trading_bot"]
trades_collection = db["trades"]

# === UTILS ===
async def send_telegram(msg, document=None):
    await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=msg)
    if document:
        await bot.send_document(chat_id=TELEGRAM_CHAT_ID, document=document)

def fetch_trades():
    return client.get_my_trades(symbol=SYMBOL)

def log_trades_to_db(trades):
    for trade in trades:
        trade_id = trade['id']
        if not trades_collection.find_one({"id": trade_id}):
            trade["timestamp"] = datetime.utcfromtimestamp(trade["time"] / 1000)
            trades_collection.insert_one(trade)

def calculate_fifo_pnl(trades):
    buy_trades = [t for t in trades if t['isBuyer']]
    sell_trades = [t for t in trades if not t['isBuyer']]
    buy_queue = []

    for buy in buy_trades:
        buy_queue.append({
            "qty": float(buy['qty']),
            "price": float(buy['price']),
            "fee": float(buy['commission'])
        })

    pnl = 0
    for sell in sell_trades:
        sell_qty = float(sell['qty'])
        sell_price = float(sell['price'])
        sell_fee = float(sell['commission'])

        while sell_qty > 0 and buy_queue:
            buy = buy_queue[0]
            matched_qty = min(sell_qty, buy['qty'])
            pnl += matched_qty * (sell_price - buy['price'])
            sell_qty -= matched_qty
            buy['qty'] -= matched_qty

            if buy['qty'] == 0:
                buy_queue.pop(0)

        pnl -= sell_fee

    return pnl

def calculate_win_loss_ratio(trades):
    buy_trades = [t for t in trades if t['isBuyer']]
    sell_trades = [t for t in trades if not t['isBuyer']]
    buy_queue = []

    for buy in buy_trades:
        buy_queue.append({
            "qty": float(buy['qty']),
            "price": float(buy['price'])
        })

    wins = 0
    losses = 0
    for sell in sell_trades:
        sell_qty = float(sell['qty'])
        sell_price = float(sell['price'])

        while sell_qty > 0 and buy_queue:
            buy = buy_queue[0]
            matched_qty = min(sell_qty, buy['qty'])
            if sell_price > buy['price']:
                wins += 1
            else:
                losses += 1
            sell_qty -= matched_qty
            buy['qty'] -= matched_qty

            if buy['qty'] == 0:
                buy_queue.pop(0)

    win_loss_ratio = wins / losses if losses > 0 else float('inf')
    return wins, losses, win_loss_ratio

def generate_report(trades, period_name):
    total_trades = len(trades)
    total_volume = sum(float(t['qty']) * float(t['price']) for t in trades)
    total_fees = sum(float(t['commission']) for t in trades)
    pnl = calculate_fifo_pnl(trades)
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

def generate_monthly_report(trades, year, month):
    report = generate_report(trades, f"Monthly {year}-{month:02d}")
    df = pd.DataFrame(trades)
    filename = f"monthly_report_{year}_{month:02d}.xlsx"
    df.to_excel(filename, index=False)
    return report, filename

# === REPORT SCHEDULING ===
async def send_reports():
    now = datetime.utcnow()
    start_of_day = datetime(now.year, now.month, now.day)
    start_of_year = datetime(now.year, 1, 1)

    daily_trades = list(trades_collection.find({"timestamp": {"$gte": start_of_day}}))
    ytd_trades = list(trades_collection.find({"timestamp": {"$gte": start_of_year}}))

    await send_telegram(generate_report(daily_trades, "Daily"))
    await send_telegram(generate_report(ytd_trades, "YTD"))

async def send_monthly_report():
    now = datetime.utcnow()
    start_of_month = datetime(now.year, now.month, 1)
    end_of_last_month = start_of_month - timedelta(days=1)
    start_of_last_month = datetime(end_of_last_month.year, end_of_last_month.month, 1)

    trades = list(trades_collection.find({"timestamp": {"$gte": start_of_last_month, "$lt": start_of_month}}))
    report, filename = generate_monthly_report(trades, end_of_last_month.year, end_of_last_month.month)
    await send_telegram(report, document=open(filename, "rb"))

# === TELEGRAM COMMANDS ===
async def report_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    trades = fetch_trades()
    log_trades_to_db(trades)
    await send_reports()

async def monthly_report_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        year = int(context.args[0])
        month = int(context.args[1])
        start = datetime(year, month, 1)
        end = datetime(year + (month // 12), (month % 12) + 1, 1)
        trades = list(trades_collection.find({"timestamp": {"$gte": start, "$lt": end}}))
        report, filename = generate_monthly_report(trades, year, month)
        await send_telegram(report, document=open(filename, "rb"))
    except Exception:
        await update.message.reply_text("Usage: /monthly_report <year> <month>")

# === MAIN ===
async def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("report", report_command))
    app.add_handler(CommandHandler("monthly_report", monthly_report_command))

    scheduler = AsyncIOScheduler()
    scheduler.add_job(send_reports, 'cron', hour=23, minute=59)
    scheduler.add_job(send_monthly_report, 'cron', day=1, hour=0, minute=0)
    scheduler.start()

    trades = fetch_trades()
    log_trades_to_db(trades)

    await app.run_polling()

if __name__ == "__main__":
    import asyncio
    try:
        asyncio.get_running_loop().run_until_complete(main())
    except RuntimeError:
        asyncio.run(main())
