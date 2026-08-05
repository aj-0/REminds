import os
import logging
import threading
import asyncio
from flask import Flask
from dotenv import load_dotenv

load_dotenv()

# Local imports
from database import init_db, DATABASE_URL
from scheduler import init_scheduler, shutdown_scheduler, reload_all_jobs
from bot import build_application

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Config ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
PORT = int(os.getenv("PORT", 10000))
SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret")

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN not set in .env")

# --- Flask App (Health Check) ---
flask_app = Flask(__name__)
flask_app.secret_key = SECRET_KEY

@flask_app.route("/")
def health():
    return "🤖 Reminder Bot is running!", 200

@flask_app.route("/health")
def health_check():
    return {"status": "ok", "scheduler_running": True}, 200

# --- Bot Thread ---
bot_thread: threading.Thread = None
ptb_application = None
bot_loop: asyncio.AbstractEventLoop = None

def run_bot_loop():
    global bot_loop, ptb_application
    bot_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(bot_loop)
    
    # Build PTB Application
    ptb_application = build_application(BOT_TOKEN)
    
    # Initialize Scheduler (needs DB URL)
    init_scheduler(DATABASE_URL)
    
    # Reload jobs from DB into Scheduler
    # We need to pass the bot instance to scheduler for callbacks
    # PTB Application has a `bot` attribute after initialize
    async def post_init(app):
        await app.initialize()
        # Reload jobs now that bot is ready
        reload_all_jobs(app.bot)
        # Start polling in background task
        # We use start_polling (non-blocking) because we are in a thread
        await app.start()
        await app.updater.start_polling(drop_pending_updates=True)
        logger.info("PTB Polling started in background thread.")

    async def post_shutdown(app):
        await app.updater.stop()
        await app.stop()
        await app.shutdown()
        shutdown_scheduler()

    ptb_application.post_init = post_init
    ptb_application.post_shutdown = post_shutdown
    
    # Run the loop forever
    try:
        bot_loop.run_forever()
    finally:
        bot_loop.close()

def start_bot_thread():
    global bot_thread
    bot_thread = threading.Thread(target=run_bot_loop, daemon=True, name="PTB-Bot-Thread")
    bot_thread.start()
    logger.info("Bot thread started.")

# --- Main Execution ---
if __name__ == "__main__":
    # 1. Init DB
    init_db()
    
    # 2. Start Bot in Background Thread
    start_bot_thread()
    
    # 3. Run Flask (Blocking Main Thread) - Required for Render Web Service
    # Gunicorn will run this: `gunicorn main:flask_app`
    # For local dev: `python main.py`
    flask_app.run(host="0.0.0.0", port=PORT)
