
from flask import Flask, request, jsonify
import asyncio
import logging
from bot import FTMBot
from telegram import Update

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Initialize Flask app
app = Flask(__name__)

# Initialize bot instance
bot = FTMBot()

@app.route('/')
def index():
    return jsonify({
        "status": "FTM Professional Bot v2.0 is running",
        "bot": "Online",
        "version": "2.0",
        "features": [
            "Music ID Processing",
            "Album ID Processing", 
            "URL Processing",
            "File Upload Support",
            "Progress Tracking",
            "Quality Download Management"
        ],
        "endpoints": {
            "/": "Bot status and info",
            "/webhook": "Telegram webhook endpoint",
            "/health": "Health check endpoint"
        }
    })

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        # Get update from Telegram
        json_data = request.get_json(force=True)
        update = Update.de_json(json_data, bot.application.bot)
        
        # Process update asynchronously
        asyncio.create_task(bot.application.process_update(update))
        
        return jsonify({"ok": True})
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/health')
def health():
    return jsonify({
        "status": "healthy",
        "bot_token_set": bool(bot.bot_token),
        "dump_channel_set": bool(bot.dump_channel_id),
        "logs_channel_set": bool(bot.logs_channel_id),
        "stats": bot.progress_stats,
        "processed_songs_count": len(bot.processed_songs)
    })

@app.route('/stats')
def stats():
    return jsonify({
        "session_stats": bot.progress_stats,
        "processed_songs": len(bot.processed_songs),
        "current_progress": bot.current_progress
    })

async def initialize_bot():
    """Initialize bot application"""
    try:
        # Setup bot application
        bot.setup_bot_application()
        
        # Initialize the application
        await bot.application.initialize()
        
        # Send startup notification
        await bot.send_startup_notification_async()
        
        logger.info("Bot initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize bot: {e}")

if __name__ == "__main__":
    # Initialize bot in background
    asyncio.create_task(initialize_bot())
    
    logger.info("FTM Professional Bot v2.0 Web Server starting...")
    logger.info("Server running on http://0.0.0.0:5000")
    
    # Run Flask app
    try:
        app.run(host='0.0.0.0', port=5000, debug=False)
    except KeyboardInterrupt:
        logger.info("Server stopped by user")
    except Exception as e:
        logger.error(f"Server stopped due to error: {e}")
