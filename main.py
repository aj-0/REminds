import os
import sys
import asyncio
import logging
import threading
from flask import Flask, jsonify
from dotenv import load_dotenv

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

# Flask app for health checks
app = Flask(__name__)

@app.route('/')
def health_check():
    return jsonify({"status": "ok", "message": "Reminder Bot is running"}), 200

@app.route('/health')
def health():
    return jsonify({"status": "healthy"}), 200


def run_bot():
    """Run the bot in a separate event loop"""
    from bot import ReminderBot
    
    token = os.getenv("BOT_TOKEN")
    if not token:
        logger.error("BOT_TOKEN environment variable not set!")
        sys.exit(1)
    
    mongodb_url = os.getenv("MONGODB_URL", "mongodb://localhost:27017")
    db_name = os.getenv("MONGODB_DB_NAME", "reminder_bot")
    
    # Create bot instance
    bot = ReminderBot(token, mongodb_url, db_name)
    
    # Create new event loop for this thread
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    try:
        loop.run_until_complete(bot.start())
        loop.run_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        loop.run_until_complete(bot.stop())
    finally:
        loop.close()


def main():
    """Main entry point"""
    # Start bot in background thread
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()
    
    # Run Flask app in main thread
    port = int(os.getenv("PORT", 10000))
    logger.info(f"Starting web server on port {port}")
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
