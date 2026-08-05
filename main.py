# main.py
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
    # Don't crash import, log error so Gunicorn starts Flask (healthcheck) but bot won't work
    logger.critical("❌ BOT_TOKEN not set! Bot thread will not start.")

# --- Flask App (Health Check) ---
flask_app = Flask(__name__)
flask_app.secret_key = SECRET_KEY

@flask_app.route("/")
def health():
    return "🤖 Reminder Bot is running!", 200

@flask_app.route("/health")
def health_check():
    # Check if bot thread is alive
    status = "ok" if bot_thread and bot_thread.is_alive() else "bot_thread_dead"
    return {"status": status, "scheduler_running": status == "ok"}, 200

# --- Bot Thread Globals ---
bot_thread: threading.Thread = None
ptb_application = None
bot_loop: asyncio.AbstractEventLoop = None

def run_bot_loop():
    global bot_loop, ptb_application
    bot_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(bot_loop)
    
    logger.info("🧵 Bot Thread: Starting PTB Application...")
    
    try:
        # Build PTB Application
        ptb_application = build_application(BOT_TOKEN)
        
        # Initialize Scheduler (needs DB URL)
        init_scheduler(DATABASE_URL)
        
        # Post-init: Initialize PTB, Reload Jobs, Start Polling
        async def post_init(app):
            await app.initialize()
            logger.info("🔄 Reloading jobs from DB...")
            reload_all_jobs(app.bot)
            await app.start()
            await app.updater.start_polling(drop_pending_updates=True, allowed_updates=[])
            logger.info("✅ PTB Polling started in background thread.")

        async def post_shutdown(app):
            logger.info("🛑 Shutting down PTB...")
            await app.updater.stop()
            await app.stop()
            await app.shutdown()
            shutdown_scheduler()

        ptb_application.post_init = post_init
        ptb_application.post_shutdown = post_shutdown
        
        # Run the loop forever
        bot_loop.run_forever()
        
    except Exception as e:
        logger.critical(f"💥 Bot Thread Crashed: {e}", exc_info=True)
    finally:
        logger.info("🧵 Bot Thread: Loop closed.")
        bot_loop.close()

def start_bot_thread():
    global bot_thread
    if not BOT_TOKEN:
        logger.error("Cannot start bot thread: BOT_TOKEN missing.")
        return
    if bot_thread and bot_thread.is_alive():
        logger.warning("Bot thread already running.")
        return
        
    bot_thread = threading.Thread(target=run_bot_loop, daemon=True, name="PTB-Bot-Thread")
    bot_thread.start()
    logger.info("🚀 Bot thread spawned.")

# ============================================================
# 🔥 CRITICAL: Run on IMPORT (Gunicorn) NOT just __main__
# ============================================================
# 1. Init DB
init_db()

# 2. Start Bot in Background Thread
start_bot_thread()

# ============================================================
# Local Dev Entry Point (python main.py)
# ============================================================
if __name__ == "__main__":
    # When running locally: `python main.py`
    # Flask runs in MAIN thread (blocking).
    # Bot thread is already running above (daemon=True).
    logger.info(f"🌐 Starting Flask Dev Server on port {PORT}...")
    flask_app.run(host="0.0.0.0", port=PORT)
