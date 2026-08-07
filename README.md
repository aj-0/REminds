# Telegram Reminder Bot 
<img width="1753" height="1178" alt="image" src="https://github.com/user-attachments/assets/8103700d-891b-4142-980b-759a54af6a1e" />

## I built this bot for my personal use, designed to suit my workflow. It provides flexible and customizable task reminders.
username : @REminds_Assist_of_AJ_bot

## Deploy on Render

1. push this repo in your github
2. render.com : New → Web Service → connect repo (uses `render.yaml`).
3. Set environment var `BOT_TOKEN` in the Render dashboard.
4. Deploy. Free tier needs the bot to bind to `$PORT` — already handled via
   a tiny aiohttp keep-alive server started in `post_init` (same event loop,
   no extra threads, so it won't hit the Python 3.14 strict-threading issue).

Note: Render's free disk isn't guaranteed persistent across deploys — `reminders.db`
survives restarts/sleep but not a fresh deploy. Fine for personal use; swap in a
Render Postgres/persistent disk if that matters to you.

Features 
* Recurring reminders
* Bulk reminder creation with a single command
* Date and time-based reminders
* Telegram notification delivery
* Customizable reminder messages
* Reminder editing and deletion
* Simple command-based interface
* Designed to suit my personal workflow
