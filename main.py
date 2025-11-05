from flask import Flask
from threading import Thread
import os
import logging
import re
import json
from datetime import datetime, time, timedelta
import pytz
from collections import defaultdict
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

app = Flask('')

@app.route('/')
def home():
    logger.info("Health check ping received")
    return "Bot is running!"

def run():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run)
    t.start()

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

raid_data = defaultdict(lambda: defaultdict(lambda: {"username": "", "count": 0}))

EST = pytz.timezone("America/New_York")
UTC = pytz.timezone("UTC")

BOT_TOKEN = os.environ.get("BOT_TOKEN")
TARGET_GROUP_ID = int(os.environ.get("GROUP_ID", "-1002374333782"))

async def is_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user = update.message.from_user
    chat = update.message.chat
    try:
        member = await context.bot.get_chat_member(chat.id, user.id)
        return member.status in ["creator", "administrator"]
    except:
        return False

def get_today_date():
    now_utc = datetime.now(UTC)
    # 8:30 PM EST = 1:30 AM UTC (next day)
    # If it's past 1:30 AM UTC, we roll over to the "next" raid day
    # The raid day runs from 1:30 AM UTC to 1:30 AM UTC next day
    if now_utc.hour > 1 or (now_utc.hour == 1 and now_utc.minute >= 30):
        raid_day = now_utc.date()
    else:
        raid_day = (now_utc - timedelta(days=1)).date()
    logger.info(f"Current UTC time: {now_utc}, Raid day: {raid_day}")
    return raid_day

def get_ordinal_suffix(day):
    if 10 <= day % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    return f"{day}{suffix}"

def cleanup_old_dates():
    today = get_today_date()
    dates_to_remove = []
    logger.info(f"=== CLEANUP: Today is {today} ===")
    for date in list(raid_data.keys()):
        logger.info(f"Checking date {date}: older than today? {date < today}")
        if date < today:
            dates_to_remove.append(date)
    for date in dates_to_remove:
        logger.info(f"Removing old date: {date}")
        del raid_data[date]
    logger.info(f"Cleanup done. Remaining dates: {list(raid_data.keys())}")
def save_raid_data():
    try:
        logger.info("=== SAVING RAID DATA ===")
        cleanup_old_dates()
        data_to_save = {}
        for date, users in raid_data.items():
            date_str = date.isoformat()
            # Convert user IDs to strings for JSON
            users_dict = {str(user_id): user_data for user_id, user_data in users.items()}
            data_to_save[date_str] = dict(users)
            logger.info(f"Saving date: {date_str} with {len(users)} users")
        logger.info(f"Total data structure: {data_to_save}")
        with open("raid_data.json", "w") as f:
            json.dump(data_to_save, f, indent=2)
        logger.info("Raid data saved successfully")
    except Exception as e:
        logger.error(f"Error saving: {e}", exc_info=True)

def load_raid_data():
    try:
        logger.info("=== LOADING RAID DATA ===")
        if os.path.exists("raid_data.json"):
            with open("raid_data.json", "r") as f:
                data = json.load(f)
            logger.info(f"Loaded {len(data)} dates from file")
            for date_str, users in data.items():
                date = datetime.fromisoformat(date_str).date()
                for user_id_str, user_data in users.items():
                    user_id = int(user_id_str)
                    raid_data[date][user_id] = user_data
            logger.info("Load complete")
            cleanup_old_dates()
        else:
            logger.info("No saved data found")
    except Exception as e:
        logger.error(f"Error loading: {e}", exc_info=True)
def extract_x_link(text):
    if not text:
        return None
    patterns = [r'https?://(?:www\.)?x\.com/\S+/status/\d+', r'https?://(?:www\.)?twitter\.com/\S+/status/\d+']
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(0)
    return None

async def track_x_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("=== MESSAGE RECEIVED ===")
    if not update.message:
        logger.info("No message in update")
        return
    message = update.message
    user = message.from_user
    text = message.text or ""
    logger.info(f"Message from {user.username or user.first_name}: {text[:50]}")
    
    if user.is_bot:
        logger.info("Message from bot, skipping")
        return
    
    x_link = extract_x_link(text)
    logger.info(f"X link extracted: {x_link}")
    
    if x_link:
        username = user.username or user.first_name or f"User{user.id}"
        today = get_today_date()
        raid_data[today][user.id]["username"] = username
        raid_data[today][user.id]["count"] += 1
        logger.info(f"Raid tracked for @{username}. Total: {raid_data[today][user.id]['count']}")
        save_raid_data()
    else:
        logger.info("No X link found in message")

async def manual_track(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    today = get_today_date()
    username = user.username or user.first_name or f"User{user.id}"
    raid_data[today][user.id]["username"] = username
    raid_data[today][user.id]["count"] += 1
    await update.message.reply_text(f"Raid tracked for @{username}!\nTotal today: {raid_data[today][user.id]['count']}")
    save_raid_data()
async def add_raid_for_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        await update.message.reply_text("Only admins can use this command!")
        return
    if not context.args:
        await update.message.reply_text("Usage: /addraid @username")
        return
    target_username = context.args[0].replace("@", "")
    today = get_today_date()
    found = False
    for user_id, data in raid_data[today].items():
        if data["username"] == target_username:
            data["count"] += 1
            found = True
            await update.message.reply_text(f"Added 1 raid for @{target_username}. New total: {data['count']}")
            save_raid_data()
            return
    if not found:
        new_user_id = hash(target_username)
        raid_data[today][new_user_id]["username"] = target_username
        raid_data[today][new_user_id]["count"] = 1
        await update.message.reply_text(f"Added 1 raid for @{target_username}. Total: 1")
        save_raid_data()

async def remove_raid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        await update.message.reply_text("Only admins can use this command!")
        return
    if not context.args:
        await update.message.reply_text("Usage: /removeraid @username")
        return
    target_username = context.args[0].replace("@", "")
    today = get_today_date()
    found = False
    for user_id, data in raid_data[today].items():
        if data["username"] == target_username:
            if data["count"] > 0:
                data["count"] -= 1
                found = True
                await update.message.reply_text(f"Removed 1 raid from             @{target_username}. New total: {data['count']}")
                save_raid_data()
                break
    if not found:
        await update.message.reply_text(f"User @{target_username} not found or has 0 raids today")

async def leaderboard_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    today = get_today_date()
    message_text = generate_leaderboard_message(today)
    await update.message.reply_text(message_text)

def generate_leaderboard_message(date):
    if date not in raid_data or not raid_data[date]:
        return "No raids tracked today yet!"
    sorted_users = sorted(raid_data[date].items(), key=lambda x: x[1]["count"], reverse=True)
    now_utc = datetime.now(UTC)
    now_est = now_utc.astimezone(EST)
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
    logger.info("=== DAILY REPORT JOB TRIGGERED ===")
    try:
        # Use the same date logic as tracking
        today = get_today_date()
        
        message_text = generate_leaderboard_message(today)
        await context.bot.send_message(chat_id=TARGET_GROUP_ID, text=message_text)
        logger.info(f"Daily report sent for date: {today}")
    except Exception as e:
        logger.error(f"ERROR in daily report: {e}", exc_info=True)

async def setup_daily_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        await update.message.reply_text("Only admins can use this command!")
        return
    job_queue = context.application.job_queue
    current_jobs = job_queue.get_jobs_by_name("daily_raid_report")
    for job in current_jobs:
        job.schedule_removal()
    
    # FOR TESTING: Run every 2 minutes, first run in 30 seconds
    job_queue.run_repeating(send_daily_report, interval=120, first=10, name="daily_raid_report", chat_id=TARGET_GROUP_ID)
    logger.info(f"TEST MODE: Daily report will fire in 10 seconds, then every 2 minutes")
    await update.message.reply_text("TEST MODE: Report will fire in 10 seconds, then every 2 minutes!")

async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        await update.message.reply_text("Only admins can use this command!")
        return
    today = get_today_date()
    count = len(raid_data[today]) if today in raid_data else 0
    if today in raid_data:
        raid_data[today].clear()
    save_raid_data()
    await update.message.reply_text(f"Reset complete! {count} users cleared")

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    today = get_today_date()
    if today in raid_data and user.id in raid_data[today]:
        count = raid_data[today][user.id]["count"]
        username = raid_data[today][user.id]["username"]
        raids_text = "Raid" if count == 1 else "Raids"
        await update.message.reply_text(f"Your Stats\n\n@{username}\n{count} {raids_text}")
    else:
        await update.message.reply_text("You have not completed any raids today yet!")

async def test_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        await update.message.reply_text("Only admins can use this command!")
        return
    await send_daily_report(context)
    await update.message.reply_text("Test report sent!")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Raid Tracking Bot Active!\n\n"
        "/trackraid - Log a raid\n"
        "/addraid @user - Add raid (admin)\n"
        "/removeraid @user - Remove raid (admin)\n"
        "/stats - View leaderboard\n"
        "/mystats - Your stats\n"
        "/setupreport - Daily 8:30PM reports (admin)\n"
        "/resettoday - Reset data (admin)\n"
        "/testreport - Test daily report (admin)"
    )
def main():
    keep_alive()
    load_raid_data()
    
    # Build application (JobQueue is now automatic)
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Add command handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("trackraid", manual_track))
    application.add_handler(CommandHandler("addraid", add_raid_for_user))
    application.add_handler(CommandHandler("removeraid", remove_raid))
    application.add_handler(CommandHandler("stats", leaderboard_command))
    application.add_handler(CommandHandler("mystats", stats_command))
    application.add_handler(CommandHandler("setupreport", setup_daily_report))
    application.add_handler(CommandHandler("resettoday", reset_command))
    application.add_handler(CommandHandler("testreport", test_report))
    
    # Add message handler for tracking X links
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, track_x_link))
    
    logger.info("Bot starting...")
    logger.info(f"Monitoring: {TARGET_GROUP_ID}")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()