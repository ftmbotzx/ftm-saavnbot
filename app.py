
from flask import Flask, request, jsonify, render_template_string
from bot import FTMBot
import logging
import threading
import asyncio

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Initialize Flask app
app = Flask(__name__)

# Initialize bot instance
bot = None
bot_thread = None

# HTML template for the web interface
HTML_TEMPLATE = '''
<!DOCTYPE html>
<html>
<head>
    <title>FTM Professional Bot v2.0</title>
    <style>
        body { font-family: Arial, sans-serif; margin: 40px; background: #f5f5f5; }
        .container { max-width: 800px; margin: 0 auto; background: white; padding: 30px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
        .header { text-align: center; color: #2c3e50; margin-bottom: 30px; }
        .status { padding: 15px; border-radius: 5px; margin: 15px 0; }
        .online { background: #d4edda; color: #155724; border: 1px solid #c3e6cb; }
        .offline { background: #f8d7da; color: #721c24; border: 1px solid #f5c6cb; }
        .stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 15px; margin: 20px 0; }
        .stat-box { background: #e9ecef; padding: 15px; border-radius: 5px; text-align: center; }
        .stat-number { font-size: 24px; font-weight: bold; color: #495057; }
        .stat-label { font-size: 12px; color: #6c757d; margin-top: 5px; }
        .features { background: #f8f9fa; padding: 20px; border-radius: 5px; margin: 20px 0; }
        .feature-list { list-style: none; padding: 0; }
        .feature-list li { padding: 5px 0; }
        .feature-list li:before { content: "✅ "; margin-right: 10px; }
        .refresh-btn { background: #007bff; color: white; padding: 10px 20px; border: none; border-radius: 5px; cursor: pointer; margin: 10px 0; }
        .refresh-btn:hover { background: #0056b3; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🎵 FTM Professional Bot v2.0</h1>
            <p>Music Download Bot Dashboard</p>
        </div>
        
        <div class="status {{ 'online' if bot_running else 'offline' }}">
            <strong>Bot Status:</strong> {{ 'Online and Running' if bot_running else 'Offline' }}
        </div>
        
        {% if bot_running %}
        <div class="stats">
            <div class="stat-box">
                <div class="stat-number">{{ stats.downloaded }}</div>
                <div class="stat-label">Downloaded</div>
            </div>
            <div class="stat-box">
                <div class="stat-number">{{ stats.processed }}</div>
                <div class="stat-label">Processed</div>
            </div>
            <div class="stat-box">
                <div class="stat-number">{{ stats.duplicates }}</div>
                <div class="stat-label">Duplicates</div>
            </div>
            <div class="stat-box">
                <div class="stat-number">{{ stats.failed }}</div>
                <div class="stat-label">Failed</div>
            </div>
            <div class="stat-box">
                <div class="stat-number">{{ stats.skipped }}</div>
                <div class="stat-label">Skipped</div>
            </div>
            <div class="stat-box">
                <div class="stat-number">{{ processed_songs_count }}</div>
                <div class="stat-label">Unique Songs</div>
            </div>
        </div>
        {% endif %}
        
        <div class="features">
            <h3>📋 Available Features</h3>
            <ul class="feature-list">
                <li>Music ID Processing (alphanumeric IDs)</li>
                <li>Album ID Processing (numeric IDs)</li>
                <li>URL Processing from files</li>
                <li>File Upload Support (.txt files)</li>
                <li>Progress Tracking for administrators</li>
                <li>High Quality Downloads (320kbps)</li>
                <li>Metadata extraction and tagging</li>
                <li>Duplicate prevention system</li>
            </ul>
        </div>
        
        <div class="features">
            <h3>🤖 How to Use the Bot</h3>
            <ul class="feature-list">
                <li>Send <code>/start</code> to see available commands</li>
                <li>Use <code>/music &lt;id&gt;</code> for single music downloads</li>
                <li>Use <code>/album &lt;id&gt;</code> for album downloads</li>
                <li>Send text messages with IDs directly</li>
                <li>Upload .txt files with multiple IDs</li>
                <li>Reply to .txt files with URLs using <code>/get</code></li>
            </ul>
        </div>
        
        <button class="refresh-btn" onclick="location.reload()">🔄 Refresh Status</button>
        
        <div style="text-align: center; margin-top: 30px; color: #6c757d; font-size: 12px;">
            <p>FTM Professional Bot v2.0 - Powered by Replit</p>
            <p>Bot Token: {{ bot_token_set }}</p>
            <p>Dump Channel: {{ dump_channel_set }}</p>
        </div>
    </div>
</body>
</html>
'''

def run_flask_in_thread():
    """Run Flask in a separate thread"""
    try:
        logger.info("Starting Flask web interface in background thread...")
        app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False)
    except Exception as e:
        logger.error(f"Error running Flask: {e}")

@app.route('/')
def index():
    """Main dashboard page"""
    global bot
    
    bot_running = bot is not None and hasattr(bot, 'application') and bot.application is not None
    
    if bot_running:
        stats = bot.progress_stats
        processed_songs_count = len(bot.processed_songs)
        bot_token_set = "✅ Configured" if bot.bot_token else "❌ Not Set"
        dump_channel_set = "✅ Configured" if bot.dump_channel_id else "❌ Not Set"
    else:
        stats = {'downloaded': 0, 'processed': 0, 'duplicates': 0, 'failed': 0, 'skipped': 0}
        processed_songs_count = 0
        bot_token_set = "❌ Bot Not Running"
        dump_channel_set = "❌ Bot Not Running"
    
    return render_template_string(HTML_TEMPLATE, 
                                bot_running=bot_running,
                                stats=stats,
                                processed_songs_count=processed_songs_count,
                                bot_token_set=bot_token_set,
                                dump_channel_set=dump_channel_set)

@app.route('/api/status')
def api_status():
    """API endpoint for bot status"""
    global bot
    
    bot_running = bot is not None and hasattr(bot, 'application') and bot.application is not None
    
    if bot_running:
        return jsonify({
            "status": "online",
            "bot_running": True,
            "stats": bot.progress_stats,
            "processed_songs_count": len(bot.processed_songs),
            "bot_token_set": bool(bot.bot_token),
            "dump_channel_set": bool(bot.dump_channel_id)
        })
    else:
        return jsonify({
            "status": "offline",
            "bot_running": False,
            "stats": {'downloaded': 0, 'processed': 0, 'duplicates': 0, 'failed': 0, 'skipped': 0},
            "processed_songs_count": 0,
            "bot_token_set": False,
            "dump_channel_set": False
        })

@app.route('/api/logs')
def api_logs():
    """API endpoint for recent logs (placeholder)"""
    return jsonify({
        "logs": [
            "Bot started successfully",
            "Polling mode active",
            "Ready to process requests"
        ]
    })

if __name__ == "__main__":
    # Start Flask in background thread
    flask_thread = threading.Thread(target=run_flask_in_thread, daemon=True)
    flask_thread.start()
    
    logger.info("Starting FTM Professional Bot v2.0 with web interface...")
    logger.info("Web interface running in background on port 5000")
    logger.info("Bot will run in main thread (polling mode)")
    
    # Initialize and run bot in main thread
    try:
        bot = FTMBot()
        bot.run_polling()  # This runs in the main thread
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Bot error: {e}")
