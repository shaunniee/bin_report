from datetime import datetime, timezone, timedelta
from collections import Counter

# Initialize logger with MongoDB collections
def init_logger(db):
    global buy_signals, skipped_signals
    buy_signals = db["buy_signals"]
    skipped_signals = db["skipped_signals"]


# Log skipped buy signals with reasons
def log_skipped_signal(symbol, reasons, frame="5m"):
    skipped_signals.insert_one(
        {
            "symbol": symbol,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "reasons": reasons,
            "frame": frame,
        }
    )


# Log successful buy signal with passed indicator reasons
def log_successful_buy(symbol, reasons, frame="5m"):
    buy_signals.insert_one(
        {
            "symbol": symbol,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "reasons": reasons,
            "frame": frame,
            "status": "open",
        }
    )


# Log trade outcome and PnL to close out an open buy signal
def log_trade_pnl(symbol, exit_price, pnl):
    buy_signals.update_one(
        {"symbol": symbol, "status": "open"},
        {
            "$set": {
                "status": "closed",
                "exit_price": exit_price,
                "pnl": pnl,
                "close_time": datetime.now(timezone.utc).isoformat(),
            }
        },
        sort=[("timestamp", -1)],
    )


# Summarize most common skipped signal reasons from past X days
def summarize_skipped_signals(days=7):
    since = datetime.now(timezone.utc) - timedelta(days=days)
    skips = skipped_signals.find({"timestamp": {"$gte": since.isoformat()}})

    counter = Counter()
    for skip in skips:
        for reason in skip["reasons"]:
            counter[reason] += 1

    if not counter:
        return "✅ No skipped signals in the last 7 days."

    summary = "📉 Weekly Skipped Signal Reasons:\n"
    for reason, count in counter.most_common():
        summary += f"- {reason}: {count} times\n"
    return summary


# Provide summary stats: buy count, skipped count, and success rate
def weekly_signal_summary(days=7):
    since = datetime.now(timezone.utc) - timedelta(days=days)
    buy_count = buy_signals.count_documents({"timestamp": {"$gte": since.isoformat()}})
    skip_count = skipped_signals.count_documents(
        {"timestamp": {"$gte": since.isoformat()}}
    )

    total = buy_count + skip_count
    if total == 0:
        return "📊 No buy or skipped signals in the last 7 days."

    success_rate = 100 * buy_count / total
    return (
        f"📊 Weekly Signal Summary:\n"
        f"✅ Buys Executed: {buy_count}\n"
        f"❌ Skipped: {skip_count}\n"
        f"📈 Success Rate: {success_rate:.2f}%"
    )
