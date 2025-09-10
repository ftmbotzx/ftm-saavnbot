#!/usr/bin/env python3
"""
FTM Saavn Telegram Bot with MP3 conversion + Progress Messages + Webport
✅ Clean logging
✅ Correct metadata fields with cover art
✅ Progress updates every 10%
✅ Webport status endpoint
✅ /start command
✅ Startup notification to log channel
"""

import logging
import os
import requests
import tempfile
import time
from datetime import timedelta
import threading
import atexit
from flask import Flask
from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes
from pydub import AudioSegment
from mutagen.id3 import ID3, APIC, TIT2, TPE1, TALB, TDRC, COMM

# ================== CONFIG ==================
TOKEN = "8464849046:AAGESCUWnURGjuPhmFWb65MvPW6k5Bd3tZI"
DUMP_ID = -1002973965692  # Channel to send songs
LOG_CHANNEL_ID = -1002884716564  # Channel for logs/startup
API_URL = "https://ftm-saavn.vercel.app/result/?query="
WEB_PORT = 5000
# ============================================

# --- Logging Setup ---
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO
)
logging.getLogger("telegram.bot").setLevel(logging.WARNING)
logging.getLogger("telegram.ext.dispatcher").setLevel(logging.WARNING)
logging.getLogger("telegram.vendor.ptb_urllib3").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)
logging.getLogger("telegram.ext").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("werkzeug").setLevel(logging.ERROR)
atexit.register(lambda: logging.info("🛑 Bot stopped"))

# --- Flask Web Server ---
app_flask = Flask("FTM_Saavn_Bot")

@app_flask.route("/")
def index():
    return "<h2>🚀 FTM Saavn Bot Running Successfully!</h2>"

def run_webserver():
    logging.info(f"🌐 Starting Webport on http://0.0.0.0:{WEB_PORT}")
    app_flask.run(host="0.0.0.0", port=WEB_PORT, debug=False, use_reloader=False)

# --- Embed metadata + cover art into MP3 ---
def embed_metadata(file_path, song):
    try:
        try:
            audio = ID3(file_path)
        except:
            audio = ID3()
            
        audio.add(TIT2(encoding=3, text=song.get("song", "")))              # Title
        audio.add(TPE1(encoding=3, text=song.get("primaryArtists", "")))    # Artist
        audio.add(TALB(encoding=3, text=song.get("album", "")))             # Album
        audio.add(TDRC(encoding=3, text=str(song.get("year", ""))))         # Year
        audio.add(COMM(encoding=3, lang='eng', desc='Comment',
                       text=f"MusicID:{song.get('id','')} | AlbumID:{song.get('albumid','')}"))

        # Embed cover image if available
        thumb_url = song.get("image", "")
        if thumb_url:
            try:
                resp = requests.get(thumb_url)
                resp.raise_for_status()
                audio.add(APIC(
                    encoding=3,
                    mime='image/jpeg',
                    type=3,  # front cover
                    desc='Cover',
                    data=resp.content
                ))
            except Exception as e:
                logging.warning(f"⚠️ Failed to embed thumbnail: {e}")

        audio.save(file_path)
        logging.info(f"🎵 Metadata embedded with artist & cover: {song.get('song','Unknown')}")
    except Exception as e:
        logging.warning(f"Metadata error: {e}")

# --- Download with progress + Telegram update ---
async def download_song(url, save_path, song_title, album_idx, album_total, song_idx, song_total, update_msg, context):
    last_percent = 0
    with requests.get(url, stream=True) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length", 0))
        downloaded = 0
        start_time = time.time()
        with open(save_path, "wb") as f:
            for chunk in r.iter_content(1024 * 512):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    elapsed = time.time() - start_time
                    speed = downloaded / (elapsed + 1e-6)
                    eta = (total - downloaded) / (speed + 1e-6) if total else 0
                    percent = int(downloaded / total * 100) if total else 0
                    if percent - last_percent >= 10:
                        logging.info(f"⬇️ {song_title}: {percent}% downloaded ({downloaded/1e6:.2f}/{total/1e6:.2f} MB)")
                        last_percent = percent
                    text = (
                        f"📀 Album {album_idx}/{album_total}\n"
                        f"🎶 {song_title} ({song_idx}/{song_total})\n\n"
                        f"⏳ {percent:.1f}% | {downloaded/1e6:.2f}/{total/1e6:.2f} MB\n"
                        f"⚡ ETA: {timedelta(seconds=int(eta))}"
                    )
                    try:
                        await context.bot.edit_message_text(
                            chat_id=update_msg.chat_id,
                            message_id=update_msg.message_id,
                            text=text
                        )
                    except Exception:
                        pass
    return save_path

# --- Convert to MP3 ---
def convert_to_mp3(input_path):
    output_path = input_path.rsplit(".", 1)[0] + ".mp3"
    audio = AudioSegment.from_file(input_path)
    audio.export(output_path, format="mp3", bitrate="320k")
    logging.info(f"🎶 Converted to MP3: {os.path.basename(output_path)}")
    return output_path

# --- Format caption ---
def format_caption(song, file_size):
    duration_sec = int(song.get('duration',0))
    perma_url = song.get('perma_url', 'N/A')
    return f"""🎶 {song.get('song','Unknown')}
👤 Artist: {song.get('primaryArtists','Unknown')}

🆔 Music ID: {song.get('id','N/A')}
💽 Album: {song.get('album','N/A')}
📀 Album ID: {song.get('albumid','N/A')}
📅 Year: {song.get('year','N/A')}
🌐 Language: {song.get('language','N/A')}
⏱ Duration: {duration_sec//60}:{duration_sec%60:02d} minutes ({duration_sec} seconds)

Source: {perma_url}
📁 Size: {file_size:.2f} MB"""

# --- Handle TXT file ---
async def handle_txt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    if not doc.file_name.endswith(".txt"):
        await update.message.reply_text("⚠️ Please send a valid .txt file with album links.")
        return
    file = await doc.get_file()
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        await file.download_to_drive(tmp.name)
        with open(tmp.name, "r") as f:
            album_links = [line.strip() for line in f if line.strip()]
    logging.info(f"📀 Received TXT: {len(album_links)} album links")
    await update.message.reply_text(f"📀 Found {len(album_links)} albums. Starting download...")

    for album_index, album_url in enumerate(album_links, start=1):
        api_url = API_URL + album_url
        try:
            resp = requests.get(api_url).json()
            songs = resp.get("songs", [])
            album_name = resp.get("name", f"Album {album_index}")
            logging.info(f"📀 Album {album_index}/{len(album_links)}: {album_name} | {len(songs)} songs found")
        except Exception as e:
            logging.error(f"API error for {album_url}: {e}")
            continue
        for song_idx, song in enumerate(songs, start=1):
            song_title = song.get("song", f"Song {song_idx}")
            logging.info(f"🎶 Starting download: {song_title} ({song_idx}/{len(songs)})")
            progress_msg = await update.message.reply_text(
                f"📀 Album {album_index}/{len(album_links)}: {album_name}\n"
                f"🎶 Starting {song_title} ({song_idx}/{len(songs)})..."
            )
            with tempfile.NamedTemporaryFile(delete=False, suffix=".m4a") as tmp_song:
                try:
                    await download_song(
                        song.get("downloadUrl"), tmp_song.name, song_title,
                        album_index, len(album_links),
                        song_idx, len(songs),
                        progress_msg, context
                    )
                except Exception as e:
                    logging.error(f"Download failed: {song_title} | {e}")
                    continue
                mp3_file = convert_to_mp3(tmp_song.name)
                os.remove(tmp_song.name)
                embed_metadata(mp3_file, song)
                size_mb = os.path.getsize(mp3_file) / (1024 * 1024)
                caption = format_caption(song, size_mb)
                try:
                    await context.bot.send_audio(
                        chat_id=DUMP_ID,
                        audio=open(mp3_file, "rb"),
                        caption=caption,
                        performer=song.get("primaryArtists", ""),
                        title=song.get("song", "")
                    )
                    logging.info(f"✅ Uploaded Song: {song_title} | Size: {size_mb:.2f} MB | Album: {album_name}")
                except Exception as e:
                    logging.error(f"Telegram send failed for {song_title}: {e}")
                finally:
                    os.remove(mp3_file)
            try:
                await context.bot.edit_message_text(
                    chat_id=progress_msg.chat_id,
                    message_id=progress_msg.message_id,
                    text=f"✅ Finished: {song_title} ({song_idx}/{len(songs)})"
                )
            except Exception:
                pass

# --- /start Command ---
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🚀 Welcome to FTM Saavn Bot!\n\n"
        "Send me a `.txt` file containing album links and I will download them for you."
    )

# --- Startup Notification ---
async def notify_startup(application):
    try:
        await application.bot.send_message(
            chat_id=LOG_CHANNEL_ID,
            text="🤖 FTM Saavn Bot has started successfully! 🚀"
        )
        logging.info("✅ Startup notification sent to log channel")
    except Exception as e:
        logging.error(f"❌ Failed to send startup notification: {e}")

# --- Main ---
def main():
    # Start Flask in a separate thread
    threading.Thread(target=run_webserver, daemon=True).start()

    # Start Telegram Bot
    app = Application.builder().token(TOKEN).build()
    app.add_handler(MessageHandler(filters.Document.FileExtension("txt"), handle_txt))
    app.add_handler(CommandHandler("start", start_command))
        
    # Notify startup
    app.post_init = notify_startup
    
    logging.info("🚀 FTM Saavn Bot started successfully!")
    app.run_polling()

if __name__ == "__main__":
    main()
