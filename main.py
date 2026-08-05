"""
Entrypoint for Render's free Web Service tier.

Render's free tier requires a process bound to $PORT answering HTTP requests
(the health check), and only runs one process/thread model per service -- it
does not support a separate "worker" process on the free plan. So: Flask
handles the health-check port on the main thread, and the Telegram bot +
APScheduler run together in a background thread, sharing one asyncio event
loop (APScheduler's jobs are started from bot.py's post_init, which runs
inside that same loop -- see bot.py).

`stop_signals=None` is required because run_polling() normally tries to
register OS signal handlers, which only works on the main thread; here it's
running on a background thread.
"""
import asyncio
import logging
import os
import threading

from flask import Flask

from bot import build_application

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__)


@app.route("/")
def health():
    return "ok", 200


def run_bot():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN environment variable is not set.")

    # This thread has no event loop by default (Python 3.10+ no longer auto-creates
    # one outside the main thread; 3.12+/3.14 raise instead of just warning). PTB's
    # run_polling() internally calls asyncio.get_event_loop(), which needs one
    # already set as "current" on this thread -- so create and set it explicitly.
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    application = build_application(token)
    application.run_polling(stop_signals=None)


if __name__ == "__main__":
    bot_thread = threading.Thread(target=run_bot, daemon=True, name="telegram-bot")
    bot_thread.start()

    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
