import os
import pandas as pd
import logging
from datetime import datetime, timedelta
from pymongo import MongoClient
from binance.client import Client
from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, ContextTypes
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import nest_asyncio
import asyncio

# === CONFIGURATION ===
API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
MONGO_URI = os.getenv("MONGO_URI")
SYMBOL = "XRPUSDT"

# === LOGGING ===
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# === INIT ===
client = Client(API_KEY, API_SECRET)
client.API_URL = 'https://testnet.binance.vision/api'  # Remove for live trading
bot = Bot(token=TELEGRAM_TOKEN)
mongo_client = MongoClient(MONGO_URI)
db = mongo_client["trading_bot"]
trades_collection = db["trades"]

# === UTILS ===
async def send_telegram(msg, document=None):
    try:
        await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=msg)
        if document:
            await bot.send_document(chat_id=TELEGRAM_CHAT_ID, document=document)
    except Exception as e:
        logger.error(f"Telegram error: {e}")

def fetch_trades():
    try:
        return client.get_my_trades(symbol=SYMBOL)
    except Exception as e:
        logger.error(f"Binance fetch error: {e}")
    return []

def log_trades_to_db(trades):
    try:
        for trade in trades:
            if not trades_collection.find_one({"id": trade["id"]}):
                trade["timestamp"] = datetime.utcfromtimestamp(trade["time"] / 1000)
                trades_collection.insert_one(trade)
    except Exception as e:
        logger.error(f"MongoDB logging error: {e}")

def group_trades_by_order_id(trades):
    grouped = {}
    for trade in trades:
        oid = trade["orderId"]
        if oid not in grouped:
            grouped[oid] = {
                "isBuyer": trade["isBuyer"],
                "qty": 0,
                "price_sum": 0,
                "commission": 0,
                "trades": []
            }
        grouped[oid]["qty"] += float(trade["qty"])
        grouped[oid]["price_sum"] += float(trade["qty"]) * float(trade["price"])
        grouped[oid]["commission"] += float(trade["commission"])
        grouped[oid]["trades"].append(trade)
    for g in grouped.values():
        g["price"] = g["price_sum"] / g["qty"]
    return grouped

# === TELEGRAM COMMAND: YTD REPORT ===
async def ytd_report_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        from_date = datetime.strptime(context.args[0], '%Y-%m-%d')
        to_date = datetime.strptime(context.args[1], '%Y-%m-%d')

        trades = list(trades_collection.find({
            "timestamp": {"$gte": from_date, "$lt": to_date}
        }))

        if not trades:
            await update.message.reply_text("⚠️ No trades found in the given date range.")
            return

        df = pd.DataFrame(trades)
        df['trade_type'] = df['isBuyer'].apply(lambda x: 'BUY' if x else 'SELL')
        df['timestamp'] = pd.to_datetime(df['time'], unit='ms')

        grouped = group_trades_by_order_id(trades)
        buys = [g for g in grouped.values() if g["isBuyer"]]
        sells = [g for g in grouped.values() if not g["isBuyer"]]
        buy_queue = []
        pnl_map = {}

        for b in buys:
            buy_queue.append({"qty": b["qty"], "price": b["price"], "fee": b["commission"]})

        for s in sells:
            sell_qty = s["qty"]
            sell_price = s["price"]
            sell_fee = s["commission"]
            pnl = 0
            while sell_qty > 0 and buy_queue:
                buy = buy_queue[0]
                matched = min(sell_qty, buy["qty"])
                pnl += matched * (sell_price - buy["price"])
                sell_qty -= matched
                buy["qty"] -= matched
                if buy["qty"] == 0:
                    buy_queue.pop(0)
            pnl -= sell_fee
            for trade in s["trades"]:
                pnl_map[trade["id"]] = pnl / len(s["trades"])

        df["pnl"] = df["id"].map(pnl_map).fillna(0)
        total_profits = df[df["pnl"] > 0]["pnl"].sum()
        total_losses = df[df["pnl"] < 0]["pnl"].sum()

        filename = f"ytd_report_{from_date.strftime('%Y%m%d')}_{to_date.strftime('%Y%m%d')}.xlsx"
        with pd.ExcelWriter(filename, engine='xlsxwriter') as writer:
            df.to_excel(writer, index=False, sheet_name='Trades')
            worksheet = writer.sheets['Trades']
            worksheet.write(len(df) + 1, 0, 'Total Profits')
            worksheet.write(len(df) + 1, 1, total_profits)
            worksheet.write(len(df) + 2, 0, 'Total Losses')
            worksheet.write(len(df) + 2, 1, total_losses)

        await update.message.reply_document(document=open(filename, "rb"))

    except Exception as e:
        await update.message.reply_text("⚠️ Failed to generate YTD report.")

# === MAIN ===
if __name__ == "__main__":
    nest_asyncio.apply()

    async def main():
        application = Application.builder().token(TELEGRAM_TOKEN).build()
        application.add_handler(CommandHandler("ytd_report", ytd_report_command))

        scheduler = AsyncIOScheduler()
        scheduler.start()

        await application.run_polling()

    asyncio.run(main())
