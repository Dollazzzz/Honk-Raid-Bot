from flask import Flask
from threading import Thread
import os
import logging
import re
from datetime import datetime, time, timedelta
import pytz
from collections import defaultdict
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

app = Flask('')

@app.route('/')
def home():
    return "Bot is running!"

def run():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run)
    t.start()

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

raid_data = defaultdict(lambda: defaultdict(lambda: {"username": "", "count": 0}))
pending_raids = {}

EST = pytz.timezone("America/New_York")
UTC = pytz.timezone("UTC")

BOT_TOKEN = os.environ.get("BOT_TOKEN")
TARGET_GROUP_ID = int(os.environ.get("GROUP_ID", "-1002374333782"))
def get_today_date():
    return datetime.now(EST).date()

def get_ordinal_suffix(day):
    if 10 <= day % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    return f"{day}{suffix}"
def extract_x_link(text):
    if not text:
        return None
    patterns = [
        r'https?://(?:www\.)?x\.com/\S+?/status/(\d+)',
        r'https?://(?:www\.)?twitter\.com/\S+?/status/(\d+)'
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            try:
                status_id = match.group(1)
                return status_id
            except IndexError:
                continue
    return None
        

def clean_old_pending_raids():
    now = datetime.now(EST)
    expired_links = []
    for link, data in pending_raids.items():
        if now - data["timestamp"] > timedelta(hours=2):
            expired_links.append(link)
    for link in expired_links:
        del pending_raids[link]
        logger.info(f"Cleaned up expired pending raid: {link}")
async def track_x_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    message = update.message
    user = message.from_user
    text = message.text or ""
    if user.is_bot:
        if "raiderbot" in (user.username or "").lower():
            await check_raid_completion(update, context)
        return
    x_link = extract_x_link(text)
    if x_link:
        username = user.username or user.first_name or f"User{user.id}"
        pending_raids[x_link] = {"user_id": user.id, "username": username, "timestamp": datetime.now(EST)}
        logger.info(f"Pending raid tracked: {username} posted {x_link}")
        clean_old_pending_raids()

async def check_raid_completion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    message = update.message
    text = message.text or ""
    if "Raid Ended" not in text and "Targets Reached" not in text:
        return
        x_link = extract_x_link(text)
    if not x_link:
        logger.warning("Raid completion detected but no link found")
        return
    if x_link in pending_raids:
        raid_info = pending_raids[x_link]
        user_id = raid_info["user_id"]
        username = raid_info["username"]
        today = get_today_date()
        raid_data[today][user_id]["username"] = username
        raid_data[today][user_id]["count"] += 1
        logger.info(f"Raid completed! Credit to @{username}. Total: {raid_data[today][user_id]['count']}")
        del pending_raids[x_link]
        await message.reply_text(f"Raid completed! Credit to @{username}\nTotal raids today: {raid_data[today][user_id]['count']}")
    else:
        logger.warning(f"Raid completed but no match for {x_link}")

async def manual_track(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    today = get_today_date()
    username = user.username or user.first_name or f"User{user.id}"
    raid_data[today][user.id]["username"] = username
    raid_data[today][user.id]["count"] += 1
    await update.message.reply_text(f"Raid tracked for @{username}!\nTotal today: {raid_data[today][user.id]['count']}")
async def leaderboard_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    today = get_today_date()
    message_text = generate_leaderboard_message(today)
    await update.message.reply_text(message_text)

def generate_leaderboard_message(date):
    if date not in raid_data or not raid_data[date]:
        return "No raids tracked today yet!"
    sorted_users = sorted(raid_data[date].items(), key=lambda x: x[1]["count"], reverse=True)
    now_est = datetime.now(EST)
    now_utc = datetime.now(UTC)
    month_name = now_est.strftime("%b")
    day_with_suffix = get_ordinal_suffix(now_est.day)
    time_str = now_utc.strftime("%I:%M%p")
    message = f"Raids {month_name} {day_with_suffix} ( {time_str} UTC 24hrs Report )\n\n"
    medals = ["🥇", "🥈", "🥉"]
    total_raids = 0
    for idx, (user_id, data) in enumerate(sorted_users):
        username = data["username"]
        count = data["count"]
        total_raids += count
        if idx < 3:
            medal = medals[idx]
        else:
            medal = "🥉" if count == sorted_users[2][1]["count"] else ""
        display_name = username if username.startswith("@") else f"@{username}"
        raids_text = "Raid" if count == 1 else "Raids"
        message += f"{display_name} {count} {raids_text} {medal}\n\n"
    message += f"Total Raids: {total_raids}"
    return message

async def send_daily_report(context: ContextTypes.DEFAULT_TYPE):
    today = get_today_date()
    message_text = generate_leaderboard_message(today)
    try:
        await context.bot.send_message(chat_id=TARGET_GROUP_ID, text=message_text)
        logger.info(f"Daily report sent for {today}")
    except Exception as e:
     logger.error(f"Error sending daily report: {e}")

async def setup_daily_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    current_jobs = context.job_queue.get_jobs_by_name("daily_raid_report")
    for job in current_jobs:
        job.schedule_removal()
    est_time = time(hour=20, minute=0, tzinfo=EST)
    context.job_queue.run_daily(send_daily_report, time=est_time, name="daily_raid_report")
    await update.message.reply_text("Daily report scheduled for 8PM EST!\nTracking X links posted by users.")  
async def pending_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    clean_old_pending_raids()
    if not pending_raids:
        await update.message.reply_text("No pending raids!")
        return
    message_text = "Pending Raids:\n\n"
    for link, data in pending_raids.items():
        username = data["username"]
        message_text += f"@{username}: {link}\n"
    await update.message.reply_text(message_text)

async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    today = get_today_date()
    count = len(raid_data[today]) if today in raid_data else 0
    if today in raid_data:
        raid_data[today].clear()
    pending_raids.clear()
    await update.message.reply_text(f"Today data reset!\n{count} users cleared")

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    today = get_today_date()
    if today in raid_data and user.id in raid_data[today]:
        count = raid_data[today][user.id]["count"]
        username = raid_data[today][user.id]["username"]
        raids_text = "Raid" if count == 1 else "Raids"
        await update.message.reply_text(f"Your Stats Today\n\n@{username}\n{count} {raids_text} completed")
    else:
        await update.message.reply_text("You have not completed any raids today yet!")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Raid Tracking Bot Active!\n\nCommands:\n/trackraid - Log a raid\n/stats - View leaderboard\n/mystats - Your stats\n/pending - Pending raids\n/setupreport - Daily 8PM reports\n/resettoday - Reset data")
def main():
    keep_alive()
    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("trackraid", manual_track))
    application.add_handler(CommandHandler("stats", leaderboard_command))
    application.add_handler(CommandHandler("mystats", stats_command))
    application.add_handler(CommandHandler("pending", pending_command))
    application.add_handler(CommandHandler("setupreport", setup_daily_report))
    application.add_handler(CommandHandler("resettoday", reset_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, track_x_link))
    logger.info("Raid Tracking Bot starting...")
    logger.info(f"Monitoring group: {TARGET_GROUP_ID}")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()