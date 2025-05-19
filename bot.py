import os
import logging
import asyncio
import nest_asyncio
from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

# === CONFIGURATION ===
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# === LOGGING ===
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# === INIT ===
bot = Bot(token=TELEGRAM_TOKEN)

# === COMMAND HANDLERS ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 Hello! I am your trading bot. Use /test to check the connection.")

async def test(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✅ Bot is connected and working!")

async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"Echo: {update.message.text}")

# === TEST FUNCTION ===
async def test_bot():
    try:
        await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text="✅ Bot is connected and working!")
    except Exception as e:
        logger.error(f"Error sending test message: {e}")

# === MAIN FUNCTION ===
async def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("test", test))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))
    await app.run_polling()

# === ENTRY POINT ===
if __name__ == "__main__":
    nest_asyncio.apply()
    loop = asyncio.get_event_loop()
    loop.run_until_complete(test_bot())
    loop.run_until_complete(main())
