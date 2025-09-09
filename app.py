import os
import threading
from flask import Flask
import bot  # this imports your bot.py

app = Flask(__name__)

@app.route("/")
def home():
    return "✅ Fᴛᴍ Dᴇᴠᴇʟᴏᴘᴇʀᴢ Saavn Bot is running successfully on Render!"

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

def run_bot():
    bot.main()   # call the new main() in bot.py

if __name__ == "__main__":
    threading.Thread(target=run_flask).start()
    run_bot()
