import os
import pandas as pd
import logging
from datetime import datetime, timedelta
from pymongo import MongoClient
from binance.client import Client
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
    try:
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
    except Exception as e:
        logger.error(f"Grouping error: {e}")
    return grouped

def calculate_fifo_pnl(trades):
    try:
        grouped = group_trades_by_order_id(trades)
        buys = [g for g in grouped.values() if g["isBuyer"]]
        sells = [g for g in grouped.values() if not g["isBuyer"]]
        buy_queue = []
        pnl = 0

        for b in buys:
            buy_queue.append({"qty": b["qty"], "price": b["price"], "fee": b["commission"]})

        for s in sells:
            sell_qty = s["qty"]
            sell_price = s["price"]
            sell_fee = s["commission"]

            while sell_qty > 0 and buy_queue:
                buy = buy_queue[0]
                matched = min(sell_qty, buy["qty"])
                pnl += matched * (sell_price - buy["price"])
                sell_qty -= matched
                buy["qty"] -= matched
                if buy["qty"] == 0:
                    buy_queue.pop(0)

            pnl -= sell_fee

        return pnl
    except Exception as e:
        logger.error(f"PnL calculation error: {e}")
        return 0

def calculate_win_loss_ratio(trades):
    try:
        grouped = group_trades_by_order_id(trades)
        buys = [g for g in grouped.values() if g["isBuyer"]]
        sells = [g for g in grouped.values() if not g["isBuyer"]]
        buy_queue = []
        wins = losses = 0

        for b in buys:
            buy_queue.append({"qty": b["qty"], "price": b["price"]})

        for s in sells:
            sell_qty = s["qty"]
            sell_price = s["price"]

            while sell_qty > 0 and buy_queue:
                buy = buy_queue[0]
                matched = min(sell_qty, buy["qty"])
                if sell_price > buy["price"]:
                    wins += 1
                else:
                    losses += 1
                sell_qty -= matched
                buy["qty"] -= matched
                if buy["qty"] == 0:
                    buy_queue.pop(0)

        return wins, losses, wins / losses if losses > 0 else float("inf")
    except Exception as e:
        logger.error(f"Win/loss calculation error: {e}")
        return 0, 0, 0

def generate_report(trades, label):
    try:
        grouped = group_trades_by_order_id(trades)
        total_trades = len(grouped)
        total_volume = sum(g["qty"] * g["price"] for g in grouped.values())
        total_fees = sum(g["commission"] for g in grouped.values())
        pnl = calculate_fifo_pnl(trades)
        wins, losses, ratio = calculate_win_loss_ratio(trades)

        return (
            f"📊 {label} Trading Report\n"
            f"----------------------------------------\n"
            f"🛒 Trades Executed: {total_trades}\n"
            f"💰 Total Volume: {total_volume:.2f} USDT\n"
            f"📈 Net PnL: {pnl:.2f} USDT\n"
            f"💸 Total Fees: {total_fees:.2f} USDT\n"
            f"✅ Wins: {wins} | ❌ Losses: {losses}\n"
            f"📊 Win/Loss Ratio: {ratio:.2f}\n"
        )
    except Exception as e:
        logger.error(f"Report generation error: {e}")
        return "⚠️ Failed to generate report."

def generate_monthly_report(trades, year, month):
    try:
        report = generate_report(trades, f"Monthly {year}-{month:02d}")
        df = pd.DataFrame(trades)
        filename = f"monthly_report_{year}_{month:02d}.xlsx"
        df.to_excel(filename, index=False)
        return report, filename
    except Exception as e:
        logger.error(f"Monthly report error: {e}")
        return "⚠️ Failed to generate monthly report.", None

# === REPORT SCHEDULING ===
async def send_reports():
    try:
        now = datetime.utcnow()
        start_day = datetime(now.year, now.month, now.day)
        start_year = datetime(now.year, 1, 1)
        daily = list(trades_collection.find({"timestamp": {"$gte": start_day}}))
        ytd = list(trades_collection.find({"timestamp": {"$gte": start_year}}))
        await send_telegram(generate_report(daily, "Daily"))
        await send_telegram(generate_report(ytd, "YTD"))
    except Exception as e:
        logger.error(f"Scheduled report error: {e}")

async def send_monthly_report():
    try:
        now = datetime.utcnow()
        start_month = datetime(now.year, now.month, 1)
        end_last = start_month - timedelta(days=1)
        start_last = datetime(end_last.year, end_last.month, 1)
        trades = list(trades_collection.find({"timestamp": {"$gte": start_last, "$lt": start_month}}))
        report, file = generate_monthly_report(trades, end_last.year, end_last.month)
        await send_telegram(report, document=open(file, "rb"))
    except Exception as e:
        logger.error(f"Monthly report error: {e}")

# === TELEGRAM COMMANDS ===
async def report_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        trades = fetch_trades()
        log_trades_to_db(trades)
        await send_reports()
    except Exception as e:
        logger.error(f"/report command error: {e}")
        await update.message.reply_text("⚠️ Failed to generate report.")

async def monthly_report_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        year = int(context.args[0])
        month = int(context.args[1])
        start = datetime(year, month, 1)
        end = datetime(year + (month // 12), (month % 12) + 1, 1)
        trades = list(trades_collection.find({"timestamp": {"$gte": start, "$lt": end}}))
        report, file = generate_monthly_report
        report, file = generate_monthly_report(trades, year, month)
        await send_telegram(report, document=open(file, "rb"))
    except Exception as e:
        logger.error(f"/monthly_report command error: {e}")
        await update.message.reply_text("⚠️ Failed to generate monthly report. Usage: /monthly_report <year> <month>")

# === MAIN ===
async def main():
    try:
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
    except Exception as e:
        logger.error(f"Bot startup error: {e}")

# === ENTRY POINT ===
if __name__ == "__main__":
    import asyncio
    try:
        import nest_asyncio
        nest_asyncio.apply()
        loop = asyncio.get_event_loop()
        loop.create_task(main())
    except RuntimeError:
        asyncio.run(main())

