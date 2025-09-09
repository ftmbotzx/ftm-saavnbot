import os
import re
import asyncio
import logging
import tempfile
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from urllib.parse import quote
from datetime import datetime
import requests
from telegram import Update, Document
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes


# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Reduce httpx logging noise
logging.getLogger("httpx").setLevel(logging.WARNING)

class FTMBot:
    def __init__(self):
        self.bot_token = os.getenv('BOT_TOKEN')
        self.dump_channel_id = os.getenv('DUMP_CHANNEL_ID')
        self.logs_channel_id = "-1002668011073"  # Logs channel for detailed notifications
        self.admin_id = "7744665378"  # Admin ID for progress updates
        
        if not self.bot_token or not self.dump_channel_id:
            raise ValueError("BOT_TOKEN and DUMP_CHANNEL_ID must be set")
        
        # API endpoints
        self.jiosaavn_songs_api = 'https://jiosaavn.funtoonsmultimedia.workers.dev/api/songs'
        self.jiosaavn_albums_api = 'https://jiosaavn.funtoonsmultimedia.workers.dev/api/albums'
        self.ftm_saavn_api = 'https://ftm-saavn.vercel.app/album'
        self.ftm_result_api = 'https://ftm-saavn.vercel.app/result/'
        
        # Download settings
        self.max_file_size = 50 * 1024 * 1024  # 50MB
        self.download_timeout = 300  # 5 minutes
        
        # Create directories
        Path('downloads').mkdir(exist_ok=True)
        Path('thumbnails').mkdir(exist_ok=True)
        Path('temp').mkdir(exist_ok=True)
        
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': 'FTM-Bot/2.0'})
        
        # Progress tracking
        self.current_progress = {}
        self.progress_messages = {}  # Store progress message IDs for editing
        self.progress_stats = {  # Track detailed statistics
            'downloaded': 0,
            'duplicates': 0,
            'failed': 0,
            'skipped': 0,
            'processed': 0
        }
        
        # Duplicate prevention - track processed song IDs
        self.processed_songs = set()
        
        # Send startup notification
        self.send_startup_notification = True
    
    async def send_logs_notification(self, context: ContextTypes.DEFAULT_TYPE, message: str, message_type: str = "INFO") -> None:
        """Send detailed notification to logs channel"""
        try:
            # Format message with timestamp and type
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            formatted_message = f"🕐 **{timestamp}** | {message_type}\n{message}"
            
            await context.bot.send_message(
                chat_id=self.logs_channel_id,
                text=formatted_message,
                parse_mode='Markdown'
            )
        except Exception as e:
            logger.error(f"Failed to send logs notification: {e}")
    
    async def send_admin_progress(self, context: ContextTypes.DEFAULT_TYPE, current: int, total: int, status: str = "ᴘʀᴏᴄᴇssɪɴɢ", key: str = "default", current_album: str = "", current_song: str = "") -> None:
        """Send or edit progress updates to admin with fancy styling"""
        try:
            progress_text = self.create_fancy_progress_bar(
                current, total, status,
                self.progress_stats['downloaded'],
                self.progress_stats['duplicates'],
                self.progress_stats['failed'],
                self.progress_stats['skipped'],
                self.progress_stats['processed'],
                current_album,
                current_song
            )
            
            # Send to admin (editable progress bar)
            if key in self.progress_messages:
                # Edit existing message
                try:
                    await context.bot.edit_message_text(
                        chat_id=self.admin_id,
                        message_id=self.progress_messages[key],
                        text=progress_text
                    )
                except Exception as edit_error:
                    # If edit fails, send new message
                    logger.warning(f"Failed to edit message, sending new one: {edit_error}")
                    sent_msg = await context.bot.send_message(
                        chat_id=self.admin_id,
                        text=progress_text
                    )
                    self.progress_messages[key] = sent_msg.message_id
            else:
                # Send new message and store message ID
                sent_msg = await context.bot.send_message(
                    chat_id=self.admin_id,
                    text=progress_text
                )
                self.progress_messages[key] = sent_msg.message_id
            
            # Also send to logs channel (non-editable, detailed)
            await self.send_logs_notification(
                context,
                progress_text,
                "PROGRESS"
            )
            
        except Exception as e:
            logger.error(f"Failed to send admin progress: {e}")
    
    async def send_startup_message(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Send startup notification to logs channel"""
        startup_msg = (
            "🚀 **FTM Professional Bot v2.0 Started**\n\n"
            "✅ **Bot Status:** Online\n"
            "🔧 **Features Loaded:**\n"
            "• Music ID Processing\n"
            "• Album ID Processing\n"
            "• URL Processing\n"
            "• File Upload Support\n"
            "• Progress Tracking\n"
            "• Quality Download Management\n\n"
            "📊 **Ready to process music requests!**"
        )
        await self.send_logs_notification(context, startup_msg, "STARTUP")
    
    async def send_shutdown_message(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Send shutdown notification to logs channel"""
        shutdown_msg = (
            "🛑 **FTM Professional Bot v2.0 Stopped**\n\n"
            "❌ **Bot Status:** Offline\n"
            f"📊 **Session Statistics:**\n"
            f"• Downloaded: {self.progress_stats['downloaded']}\n"
            f"• Duplicates: {self.progress_stats['duplicates']}\n"
            f"• Failed: {self.progress_stats['failed']}\n"
            f"• Skipped: {self.progress_stats['skipped']}\n"
            f"• Processed: {self.progress_stats['processed']}\n\n"
            "👋 **Bot has been stopped gracefully**"
        )
        await self.send_logs_notification(context, shutdown_msg, "SHUTDOWN")
    
    def create_progress_bar(self, current: int, total: int, width: int = 20) -> str:
        """Create a professional progress bar"""
        if total == 0:
            return "▓" * width + " 0%"
        
        percentage = (current / total) * 100
        filled_width = int(width * current // total)
        bar = "▓" * filled_width + "░" * (width - filled_width)
        return f"{bar} {percentage:.1f}%"
    
    def extract_ids_from_text(self, text: str) -> List[str]:
        """Extract valid music/album IDs from text content"""
        ids = []
        lines = text.split('\n')
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
            
            # First, try to extract IDs from formatted lines like "🆔 Music ID: Sh-3oyLG"
            music_id_patterns = [
                r'music\s+id\s*[:：]\s*([a-zA-Z0-9\-_]{3,})',
                r'musicid\s*[:：]\s*([a-zA-Z0-9\-_]{3,})',
                r'music_id\s*[:：]\s*([a-zA-Z0-9\-_]{3,})',
                r'🆔\s*music\s+id\s*[:：]\s*([a-zA-Z0-9\-_]{3,})'
            ]
            
            for pattern in music_id_patterns:
                match = re.search(pattern, line, re.IGNORECASE)
                if match:
                    ids.append(match.group(1))
                    break
            else:
                # Try to extract album IDs from formatted lines like "💽 Album: 14101426"
                album_id_patterns = [
                    r'album\s*[:：]\s*(\d{6,})',
                    r'albumid\s*[:：]\s*(\d{6,})',
                    r'album_id\s*[:：]\s*(\d{6,})',
                    r'💽\s*album\s*[:：]\s*(\d{6,})'
                ]
                
                for pattern in album_id_patterns:
                    match = re.search(pattern, line, re.IGNORECASE)
                    if match:
                        ids.append(match.group(1))
                        break
                else:
                    # Skip lines with common separators or formatting
                    if re.match(r'^[=\-_#*🎵👤📅🌐⏱🔗🖼]+$', line):
                        continue
                    
                    # Skip common single words that are not IDs
                    skip_words = {
                        'music', 'album', 'song', 'name', 'year', 'language', 'duration', 
                        'image', 'thumb', 'download', 'url', 'id', 'https', 'http',
                        'version', 'caption', 'format', 'bot', 'ftm', 'title', 'artist'
                    }
                    
                    if line.lower() in skip_words:
                        continue
                    
                    # Skip lines that are just headers/labels without actual IDs
                    header_patterns = [
                        r'^🎵\s*music\s*\d*$',
                        r'^🎶\s*title\s*[:：]',
                        r'^👤\s*artist\s*[:：]',
                        r'^📅\s*year\s*[:：]',
                        r'^🌐\s*language\s*[:：]',
                        r'^⏱\s*duration\s*[:：]',
                        r'^🔗\s*url\s*[:：]',
                        r'^🖼\s*thumb\s*[:：]'
                    ]
                    
                    if any(re.match(pattern, line, re.IGNORECASE) for pattern in header_patterns):
                        continue
                    
                    # Extract standalone MUSIC ID pattern (alphanumeric with hyphens, 3+ chars)
                    music_id_match = re.match(r'^[a-zA-Z0-9\-_]{3,}$', line)
                    if music_id_match and not line.isdigit():
                        ids.append(line)
                        continue
                    
                    # Extract standalone ALBUM ID pattern (numeric, 6+ digits)
                    album_id_match = re.match(r'^\d{6,}$', line)
                    if album_id_match:
                        ids.append(line)
                        continue
                    
                    # Try to extract ID from URLs
                    extracted_id = self.extract_id_from_url(line)
                    if extracted_id:
                        ids.append(extracted_id)
        
        # Remove duplicates and return
        return list(dict.fromkeys(ids))
    
    def extract_id_from_url(self, text: str) -> Optional[str]:
        """Extract ID from various URL formats"""
        # JioSaavn URL patterns
        jiosaavn_patterns = [
            r'jiosaavn\.com/.*?/([a-zA-Z0-9\-_]+)',
            r'saavn\.com/.*?/([a-zA-Z0-9\-_]+)',
            r'(?:id=)([a-zA-Z0-9\-_]+)'
        ]
        
        for pattern in jiosaavn_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return match.group(1)
        
        return None
    
    def is_album_id(self, id_str: str) -> bool:
        """Check if ID is an album ID (numeric)"""
        return bool(re.match(r'^\d{6,}$', id_str))
    
    async def get_song_by_id(self, music_id: str) -> Optional[Dict]:
        """Get song details by MUSIC ID"""
        try:
            url = f"{self.jiosaavn_songs_api}/{music_id}"
            response = self.session.get(url, timeout=30)
            response.raise_for_status()
            
            data = response.json()
            if data.get('success') == True and data.get('data'):
                return data
            else:
                logger.error(f"API error for song {music_id}: {data.get('message', 'No data found')}")
                return None
        except Exception as e:
            logger.error(f"Error fetching song {music_id}: {e}")
            return None
    
    async def get_album_by_id(self, album_id: str) -> Optional[Dict]:
        """Get album details by ALBUM ID"""
        try:
            url = f"{self.jiosaavn_albums_api}?id={album_id}"
            response = self.session.get(url, timeout=30)
            response.raise_for_status()
            
            data = response.json()
            if data.get('success') == True and data.get('data'):
                return data
            else:
                logger.error(f"API error for album {album_id}: {data.get('message', 'No data found')}")
                return None
        except Exception as e:
            logger.error(f"Error fetching album {album_id}: {e}")
            return None
    
    async def get_album_metadata(self, album_url: str) -> Optional[Dict]:
        """Get detailed album metadata from FTM Saavn API with fallback to primary API"""
        try:
            # First try FTM Saavn API (secondary API)
            encoded_url = quote(album_url, safe='')
            url = f"{self.ftm_saavn_api}/?query={encoded_url}&lyrics=true"
            
            response = self.session.get(url, timeout=30)
            response.raise_for_status()
            
            data = response.json()
            
            # Check if the response is valid and has songs
            if data and data.get('songs') and len(data.get('songs', [])) > 0:
                # Also check if songs have valid download URLs
                valid_songs = [song for song in data['songs'] if self.get_download_urls(song)]
                if valid_songs:
                    data['songs'] = valid_songs  # Only keep songs with download URLs
                    logger.info(f"Successfully fetched album metadata from FTM API for: {album_url}")
                    return data
                else:
                    logger.warning(f"FTM API returned songs without download URLs for {album_url}")
            
            # If FTM API fails or has no valid data, try fallback
            if data is None:
                logger.warning(f"FTM API returned null for {album_url}, album may not exist or be restricted")
            else:
                logger.warning(f"FTM API returned empty/invalid data for {album_url}, trying fallback")
            
            return await self.get_album_metadata_fallback(album_url)
                
        except Exception as e:
            logger.error(f"Error fetching album metadata from FTM API for {album_url}: {e}")
            logger.info(f"Trying fallback API for: {album_url}")
            return await self.get_album_metadata_fallback(album_url)
    
    async def get_album_metadata_fallback(self, album_url: str) -> Optional[Dict]:
        """Fallback method to get album data from JioSaavn APIs with FTM results API enhancement"""
        try:
            # Extract album ID from URL
            album_id_match = re.search(r'/([a-zA-Z0-9\-_,]+)/?$', album_url)
            if not album_id_match:
                logger.error(f"Could not extract album ID from URL: {album_url}")
                return None
            
            album_id = album_id_match.group(1)
            
            # Try the album endpoint directly first
            for id_to_try in [album_id, album_id.replace('-', '_')]:
                try:
                    # Try getting album data directly 
                    album_data = await self.get_album_by_id(id_to_try)
                    
                    if album_data and album_data.get('success') and album_data.get('data'):
                        data = album_data['data']
                        if isinstance(data, list) and len(data) > 0:
                            data = data[0]
                        
                        # Get songs from album data
                        songs = data.get('songs', [])
                        if songs:
                            # For each song, try to get download URLs
                            songs_with_urls = []
                            for song in songs:
                                song_id = song.get('id')
                                song_url = song.get('perma_url') or song.get('url')
                                song_name = song.get('name') or song.get('song') or 'Unknown'
                                
                                if song_id:
                                    # Step 1: Try to get download URLs from song detail API
                                    song_detail = await self.get_song_by_id(song_id)
                                    
                                    updated_song = None
                                    
                                    if song_detail and song_detail.get('success') and song_detail.get('data'):
                                        song_data = song_detail['data']
                                        if isinstance(song_data, list) and len(song_data) > 0:
                                            song_data = song_data[0]
                                        
                                        # Check if song detail has download URLs
                                        if song_data.get('downloadUrl'):
                                            # Copy all important fields from original song
                                            updated_song = {
                                                'id': song_id,
                                                'name': song.get('name') or song_data.get('name') or 'Unknown',
                                                'song': song.get('song') or song_data.get('song') or song.get('name') or 'Unknown',
                                                'downloadUrl': song_data['downloadUrl'],
                                                'image': song.get('image') or song_data.get('image'),
                                                'duration': song.get('duration') or song_data.get('duration'),
                                                'year': song.get('year') or song_data.get('year'),
                                                'language': song.get('language') or song_data.get('language'),
                                                'artists': song.get('artists') or song_data.get('artists'),
                                                'album': song.get('album') or song_data.get('album')
                                            }
                                            songs_with_urls.append(updated_song)
                                            logger.info(f"✅ Got download URL from song detail API for: {updated_song['name']}")
                                        else:
                                            logger.warning(f"❌ No download URL in song detail for: {song_name}")
                                            
                                            # Step 2: If no download URL, try FTM results API with song URL
                                            if song_url:
                                                logger.info(f"🔄 Trying FTM results API for: {song_name}")
                                                try:
                                                    ftm_song_data = await self.get_song_from_ftm_results(song_url)
                                                    if ftm_song_data:
                                                        # Merge data from both sources
                                                        updated_song = {
                                                            'id': song_id,
                                                            'name': song.get('name') or song_data.get('name') or ftm_song_data.get('name') or 'Unknown',
                                                            'song': song.get('song') or song_data.get('song') or ftm_song_data.get('song') or song.get('name') or 'Unknown',
                                                            'downloadUrl': ftm_song_data.get('downloadUrl', []),
                                                            'image': song.get('image') or song_data.get('image') or ftm_song_data.get('image'),
                                                            'duration': song.get('duration') or song_data.get('duration') or ftm_song_data.get('duration'),
                                                            'year': song.get('year') or song_data.get('year') or ftm_song_data.get('year'),
                                                            'language': song.get('language') or song_data.get('language') or ftm_song_data.get('language'),
                                                            'artists': song.get('artists') or song_data.get('artists') or ftm_song_data.get('artists'),
                                                            'album': song.get('album') or song_data.get('album') or ftm_song_data.get('album')
                                                        }
                                                        songs_with_urls.append(updated_song)
                                                        logger.info(f"✅ Got download URL from FTM results API for: {updated_song['name']}")
                                                    else:
                                                        logger.warning(f"❌ FTM results API failed for: {song_name}")
                                                except Exception as ftm_error:
                                                    logger.error(f"❌ Error using FTM results API for {song_name}: {ftm_error}")
                                            else:
                                                logger.warning(f"❌ No song URL available for FTM results API: {song_name}")
                                    else:
                                        logger.warning(f"❌ Could not get song detail for: {song_id}")
                                        
                                        # Try FTM results API as last resort if we have song URL
                                        if song_url:
                                            logger.info(f"🔄 Trying FTM results API as last resort for: {song_name}")
                                            try:
                                                ftm_song_data = await self.get_song_from_ftm_results(song_url)
                                                if ftm_song_data:
                                                    updated_song = {
                                                        'id': song_id,
                                                        'name': song.get('name') or ftm_song_data.get('name') or 'Unknown',
                                                        'song': song.get('song') or ftm_song_data.get('song') or song.get('name') or 'Unknown',
                                                        'downloadUrl': ftm_song_data.get('downloadUrl', []),
                                                        'image': song.get('image') or ftm_song_data.get('image'),
                                                        'duration': song.get('duration') or ftm_song_data.get('duration'),
                                                        'year': song.get('year') or ftm_song_data.get('year'),
                                                        'language': song.get('language') or ftm_song_data.get('language'),
                                                        'artists': song.get('artists') or ftm_song_data.get('artists'),
                                                        'album': song.get('album') or ftm_song_data.get('album')
                                                    }
                                                    songs_with_urls.append(updated_song)
                                                    logger.info(f"✅ Got download URL from FTM results API (last resort) for: {updated_song['name']}")
                                            except Exception as ftm_error:
                                                logger.error(f"❌ Error using FTM results API (last resort) for {song_name}: {ftm_error}")
                            
                            if songs_with_urls:
                                # Convert to FTM API format
                                result = {
                                    'songs': songs_with_urls,
                                    'name': data.get('name', 'Unknown Album'),
                                    'image': data.get('image', []),
                                    'year': data.get('year'),
                                    'language': data.get('language'),
                                    'id': data.get('id') or data.get('albumid')
                                }
                                
                                logger.info(f"Successfully fetched album from enhanced fallback API: {len(songs_with_urls)} songs with download URLs")
                                return result
                            else:
                                logger.warning(f"Album found but no songs have download URLs after all attempts: {id_to_try}")
                        else:
                            logger.warning(f"Album found but has no songs: {id_to_try}")
                    else:
                        logger.warning(f"No album data found for ID: {id_to_try}")
                        
                except Exception as inner_e:
                    logger.warning(f"Enhanced fallback attempt failed for ID {id_to_try}: {inner_e}")
                    continue
            
            logger.error(f"All enhanced fallback attempts failed for: {album_url}")
            return None
            
        except Exception as e:
            logger.error(f"Error in enhanced fallback album metadata fetch for {album_url}: {e}")
            return None
    
    async def get_song_from_ftm_results(self, song_url: str) -> Optional[Dict]:
        """Get song data from FTM results API"""
        try:
            encoded_url = quote(song_url, safe='')
            url = f"{self.ftm_result_api}?query={encoded_url}"
            
            response = self.session.get(url, timeout=30)
            response.raise_for_status()
            
            data = response.json()
            
            if data and isinstance(data, dict):
                # Extract relevant song data
                song_data = {
                    'name': data.get('name') or data.get('song') or data.get('title'),
                    'song': data.get('song') or data.get('name') or data.get('title'),
                    'downloadUrl': data.get('downloadUrl', []),
                    'image': data.get('image'),
                    'duration': data.get('duration'),
                    'year': data.get('year'),
                    'language': data.get('language'),
                    'artists': data.get('artists') or data.get('artist'),
                    'album': data.get('album'),
                    'album_url': data.get('album_url')
                }
                
                # Validate that we have essential data
                if song_data.get('downloadUrl') and song_data.get('name'):
                    logger.info(f"Successfully fetched song data from FTM results API: {song_data['name']}")
                    return song_data
                else:
                    logger.warning(f"FTM results API returned incomplete data for: {song_url}")
                    return None
            else:
                logger.warning(f"FTM results API returned invalid data format for: {song_url}")
                return None
                
        except Exception as e:
            logger.error(f"Error fetching song from FTM results API for {song_url}: {e}")
            return None
    
    def get_download_urls(self, song: Dict) -> List[str]:
        """Extract and prioritize download URLs by quality (highest first)"""
        urls = []
        
        # FTM API format - direct downloadUrl field (PRIMARY)
        if 'downloadUrl' in song:
            download_url = song['downloadUrl']
            if isinstance(download_url, list):
                urls.extend([url for url in download_url if url])
            elif download_url and isinstance(download_url, str):
                urls.append(download_url)
        
        # First API format - quality-specific fields (FALLBACK)
        quality_fields = ['320kbps', '160kbps', '96kbps']
        for quality in quality_fields:
            if quality in song and song[quality] and song[quality] != 'true' and song[quality] != 'false':
                if isinstance(song[quality], list):
                    urls.extend([url for url in song[quality] if url and isinstance(url, str)])
                elif isinstance(song[quality], str) and song[quality].startswith('http'):
                    urls.append(song[quality])
        
        # Check for media_url (additional fallback)
        if 'media_url' in song:
            media_url = song['media_url']
            if isinstance(media_url, list):
                urls.extend([url for url in media_url if url and isinstance(url, str)])
            elif media_url and isinstance(media_url, str):
                urls.append(media_url)
        
        # Filter valid URLs and remove duplicates while preserving order
        valid_urls = []
        seen_urls = set()
        
        for url in urls:
            if url and isinstance(url, str) and url.startswith('http') and url not in seen_urls:
                valid_urls.append(url)
                seen_urls.add(url)
        
        # Sort URLs by quality (highest quality first)
        return self.sort_urls_by_quality(valid_urls)
    
    def sort_urls_by_quality(self, urls: List[str]) -> List[str]:
        """Sort URLs by quality, highest first"""
        def get_quality_score(url: str) -> int:
            """Assign quality scores to URLs based on indicators"""
            url_lower = url.lower()
            
            # Explicit quality indicators
            if '320' in url_lower or '_h.' in url_lower or 'high' in url_lower:
                return 320
            elif '256' in url_lower:
                return 256
            elif '192' in url_lower:
                return 192
            elif '160' in url_lower or '_m.' in url_lower or 'medium' in url_lower:
                return 160
            elif '128' in url_lower:
                return 128
            elif '96' in url_lower or '_l.' in url_lower or 'low' in url_lower:
                return 96
            elif '64' in url_lower:
                return 64
            
            # File extension preferences (higher quality formats)
            if url_lower.endswith('.flac'):
                return 400  # Lossless
            elif url_lower.endswith('.m4a') or url_lower.endswith('.aac'):
                return 200  # Generally higher quality than MP3
            elif url_lower.endswith('.mp3'):
                return 150  # Standard quality
            
            # Default score for unknown quality
            return 100
        
        # Sort by quality score (descending - highest first)
        sorted_urls = sorted(urls, key=get_quality_score, reverse=True)
        
        logger.info(f"Sorted {len(urls)} URLs by quality: {[get_quality_score(url) for url in sorted_urls[:3]]}")
        
        return sorted_urls
    
    def download_file(self, url: str, filepath: str) -> bool:
        """Download file from URL to local path"""
        try:
            response = self.session.get(url, stream=True, timeout=self.download_timeout)
            response.raise_for_status()
            
            # Check file size
            content_length = response.headers.get('content-length')
            if content_length and int(content_length) > self.max_file_size:
                logger.error(f"File too large: {content_length} bytes")
                return False
            
            with open(filepath, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
            
            logger.info(f"Downloaded: {filepath}")
            return True
        except Exception as e:
            logger.error(f"Error downloading {url}: {e}")
            return False
    
    def format_duration(self, duration) -> str:
        """Format duration from seconds to MM:SS"""
        try:
            if not duration:
                return "Unknown"
            
            if isinstance(duration, str):
                # If duration is already formatted as MM:SS
                if ':' in duration:
                    return duration
                # Try to convert string to number
                try:
                    duration = float(duration)
                except:
                    return duration if duration else "Unknown"
            
            # Convert to integer seconds
            seconds = int(float(duration))
            if seconds <= 0:
                return "Unknown"
                
            minutes = seconds // 60
            remaining_seconds = seconds % 60
            return f"{minutes}:{remaining_seconds:02d}"
        except Exception as e:
            logger.error(f"Error formatting duration {duration}: {e}")
            return "Unknown"
    
    def create_fancy_progress_bar(self, current: int, total: int, status: str = "ᴘʀᴏᴄᴇssɪɴɢ", downloaded: int = 0, duplicates: int = 0, failed: int = 0, skipped: int = 0, processed: int = 0, current_album: str = "", current_song: str = "") -> str:
        """Create a fancy styled progress bar for music processing"""
        if total == 0:
            percentage = 0
        else:
            percentage = int((current / total) * 100)
        
        # Truncate long names for better display
        album_display = current_album[:30] + "..." if len(current_album) > 30 else current_album
        song_display = current_song[:30] + "..." if len(current_song) > 30 else current_song
        
        progress_msg = (
            f"╔════❰ ᴍᴜsɪᴄ ᴘʀᴏᴄᴇssɪɴɢ sᴛᴀᴛᴜs ❱═❍⊱❁\n"
            f"║╭━━━━━━━━━━━━━━━➣\n"
            f"║┣⪼🎵 ᴛᴏᴛᴀʟ sᴏɴɢs: {total}\n"
            f"║┃\n"
            f"║┣⪼🎶 ᴘʀᴏᴄᴇssᴇᴅ sᴏɴɢs: {current}\n"
            f"║┃\n"
            f"║┣⪼⬇️ ᴅᴏᴡɴʟᴏᴀᴅᴇᴅ sᴏɴɢs: {downloaded}\n"
            f"║┃\n"
            f"║┣⪼🔄 ᴅᴜᴘʟɪᴄᴀᴛᴇ sᴏɴɢs: {duplicates}\n"
            f"║┃\n"
            f"║┣⪼❌ ғᴀɪʟᴇᴅ ᴅᴏᴡɴʟᴏᴀᴅs: {failed}\n"
            f"║┃\n"
            f"║┣⪼⏭️ sᴋɪᴘᴘᴇᴅ sᴏɴɢs: {skipped}\n"
            f"║┃\n"
            f"║┣⪼✅ sᴜᴄᴄᴇssғᴜʟ ᴘʀᴏᴄᴇssɪɴɢ: {processed}\n"
            f"║┃\n"
        )
        
        # Add current album and song if available
        if current_album:
            progress_msg += f"║┣⪼💿 ᴄᴜʀʀᴇɴᴛ ᴀʟʙᴜᴍ: {album_display}\n║┃\n"
        
        if current_song:
            progress_msg += f"║┣⪼🎧 ᴄᴜʀʀᴇɴᴛ sᴏɴɢ: {song_display}\n║┃\n"
        
        progress_msg += (
            f"║┣⪼📊 ᴄᴜʀʀᴇɴᴛ sᴛᴀᴛᴜs: {status}\n"
            f"║┃\n"
            f"║┣⪼📈 ᴘᴇʀᴄᴇɴᴛᴀɢᴇ: {percentage}%\n"
            f"║╰━━━━━━━━━━━━━━━➣\n"
            f"╚════❰ {'ᴄᴏᴍᴘʟᴇᴛᴇᴅ' if current >= total else 'ɪɴ ᴘʀᴏɢʀᴇss'} ❱══❍⊱❁"
        )
        return progress_msg
    
    def song_id_to_music_id(self, song_id: str) -> str:
        """Convert Song ID to Music ID format"""
        if not song_id:
            return song_id
        # Simple conversion - you may need to adjust this based on actual API response format
        return song_id.replace('_', '-') if song_id else song_id
    
    def clean_song_name(self, name: str) -> str:
        """Clean song name by replacing underscores with spaces"""
        if not name:
            return name
        return name.replace('_', ' ')
    
    def format_caption(self, metadata: Dict) -> str:
        """Format caption for Telegram message"""
        caption = ""
        
        # Convert Song ID to Music ID format and show only Music ID
        if metadata.get('songId'):
            music_id = self.song_id_to_music_id(metadata['songId'])
            caption += f"🆔 Music ID: {music_id}\n"
        elif metadata.get('musicId'):
            caption += f"🆔 Music ID: {metadata['musicId']}\n"
        
        if metadata.get('albumId'):
            caption += f"💽 Album: {metadata['albumId']}\n"
        
        if metadata.get('year'):
            caption += f"📅 Year: {metadata['year']}\n"
        
        if metadata.get('language'):
            caption += f"🌐 Language: {metadata['language']}\n"
        
        if metadata.get('duration'):
            duration = metadata['duration']
            if duration and str(duration) != "Unknown":
                # Format duration as MM:SS for caption
                formatted_duration = self.format_duration(duration)
                caption += f"⏱ Duration: {formatted_duration}\n"
        
        return caption.rstrip()
    
    def sanitize_filename(self, filename: str) -> str:
        """Sanitize filename for filesystem"""
        # Remove invalid characters
        filename = re.sub(r'[<>:"/\\|?*]', '', filename)
        # Replace spaces with underscores
        filename = re.sub(r'\s+', '_', filename)
        # Limit length
        return filename[:100]
    
    async def process_song_downloads(self, song: Dict, album_metadata: Dict, original_id: str, context: ContextTypes.DEFAULT_TYPE, progress_info: Dict = None) -> None:
        """Process and send song downloads"""
        song_id = song.get('id') or song.get('song_id')
        song_name = self.clean_song_name(song.get('name') or song.get('song') or song.get('title') or 'Unknown')
        
        # Check for duplicates
        if song_id and song_id in self.processed_songs:
            logger.info(f"Skipping duplicate song ID: {song_id}")
            await self.send_logs_notification(
                context,
                f"🔄 **Duplicate Song Skipped**\n"
                f"🎵 **Song:** {song_name}\n"
                f"🆔 **ID:** {song_id}\n"
                f"📝 **Reason:** Already processed in this session",
                "DUPLICATE"
            )
            self.progress_stats['duplicates'] += 1
            return
        
        # Get clean song name (replace underscores with spaces)
        raw_song_name = song.get('name') or song.get('song') or song.get('title') or 'Unknown'
        song_name = self.clean_song_name(raw_song_name)
        download_urls = self.get_download_urls(song)
        
        if not download_urls:
            logger.error(f"No download URLs found for song: {song_name}")
            self.progress_stats['skipped'] += 1
            return
        
        # Mark as processed
        if song_id:
            self.processed_songs.add(song_id)
        
        # Track as processed (successful processing)
        self.progress_stats['processed'] += 1
        
        # Send progress to admin
        if progress_info:
            await self.send_admin_progress(
                context, 
                progress_info['current'], 
                progress_info['total'],
                f"📥 {song_name}",
                "album_progress"
            )
        
        # Download thumbnail once
        thumbnail_path = None
        thumbnail_url = song.get('image') or album_metadata.get('image')
        if thumbnail_url:
            thumbnail_filename = self.sanitize_filename(f"thumb_{raw_song_name}.jpg")
            thumbnail_path = f"thumbnails/{thumbnail_filename}"
            self.download_file(thumbnail_url, thumbnail_path)
        
        # Process only the highest quality download URL (first one after sorting)
        if download_urls:
            download_url = download_urls[0]  # Highest quality URL
            try:
                logger.info(f"Downloading highest quality version of {song_name}")
                
                # Determine file extension
                file_ext = '.mp3'
                if '.flac' in download_url.lower():
                    file_ext = '.flac'
                elif '.m4a' in download_url.lower() or '.aac' in download_url.lower():
                    file_ext = '.m4a'
                elif '.mp4' in download_url.lower():
                    file_ext = '.mp4'
                
                # Create filename (keep underscores for filenames)
                audio_filename = self.sanitize_filename(f"{raw_song_name}{file_ext}")
                audio_path = f"downloads/{audio_filename}"
                
                # Download audio file
                if not self.download_file(download_url, audio_path):
                    logger.error(f"Failed to download {song_name}")
                    return
                
                # Extract artist name from multiple possible sources
                artist_name = (
                    song.get('artists', {}).get('primary') or
                    song.get('primaryArtists') or 
                    song.get('artists') or 
                    song.get('artist') or
                    album_metadata.get('primaryArtists') or 
                    album_metadata.get('artists') or
                    song.get('artist_name') or
                    song.get('singers') or
                    'Unknown'
                )
                
                # Handle artist list/object formats
                if isinstance(artist_name, list):
                    # Extract names from list of objects or strings
                    names = []
                    for artist in artist_name:
                        if isinstance(artist, dict):
                            names.append(artist.get('name', str(artist)))
                        else:
                            names.append(str(artist))
                    artist_name = ', '.join(names) if names else 'Unknown'
                elif isinstance(artist_name, dict):
                    artist_name = artist_name.get('name', 'Unknown')
                
                # Clean artist name
                artist_name = self.clean_song_name(str(artist_name)) if artist_name != 'Unknown' else 'Unknown'
                
                # Get duration in seconds format
                duration_seconds = song.get('duration') or song.get('length') or album_metadata.get('duration')
                duration_for_file = None  # Duration in seconds for file metadata
                
                if duration_seconds:
                    # Convert to integer seconds if it's a string or float
                    try:
                        if isinstance(duration_seconds, str):
                            # If it's already in MM:SS format, convert to seconds
                            if ':' in duration_seconds:
                                minutes, seconds = duration_seconds.split(':')
                                duration_for_file = int(minutes) * 60 + int(seconds)
                            else:
                                duration_for_file = int(float(duration_seconds))
                        else:
                            duration_for_file = int(float(duration_seconds))
                    except:
                        duration_for_file = None
                
                # Prepare caption metadata
                caption_metadata = {
                    'musicId': original_id if not self.is_album_id(original_id) else None,
                    'albumId': song.get('albumid') or song.get('album_id') or album_metadata.get('albumid') or album_metadata.get('id'),
                    'songId': song.get('id') or song.get('song_id'),
                    'albumName': album_metadata.get('name') or album_metadata.get('title') or song.get('album_name') or song.get('album'),
                    'artistName': artist_name,
                    'year': album_metadata.get('year') or song.get('year'),
                    'language': album_metadata.get('language') or song.get('language'),
                    'duration': duration_for_file
                }
                
                caption = self.format_caption(caption_metadata)
                
                # Ensure duration is valid for Telegram (must be positive integer)
                telegram_duration = None
                if duration_for_file and duration_for_file > 0:
                    telegram_duration = int(duration_for_file)
                
                # Send audio file with clean title (spaces instead of underscores) and duration
                if thumbnail_path and os.path.exists(thumbnail_path):
                    # Open both files together to keep them open during send
                    with open(audio_path, 'rb') as audio_file, open(thumbnail_path, 'rb') as thumb_file:
                        await context.bot.send_audio(
                            chat_id=self.dump_channel_id,
                            audio=audio_file,
                            thumbnail=thumb_file,
                            caption=caption,
                            title=song_name,  # Clean title for display
                            performer=artist_name if artist_name != 'Unknown' else None,
                            duration=telegram_duration,  # Validated duration for file metadata
                            parse_mode='Markdown'
                        )
                else:
                    # Send without thumbnail
                    with open(audio_path, 'rb') as audio_file:
                        await context.bot.send_audio(
                            chat_id=self.dump_channel_id,
                            audio=audio_file,
                            caption=caption,
                            title=song_name,  # Clean title for display
                            performer=artist_name if artist_name != 'Unknown' else None,
                            duration=telegram_duration,  # Validated duration for file metadata
                            parse_mode='Markdown'
                        )
                
                logger.info(f"Successfully sent highest quality {song_name} to dump channel")
                
                # Send detailed success notification to logs channel
                self.progress_stats['downloaded'] += 1
                await self.send_logs_notification(
                    context,
                    f"✅ **Song Downloaded Successfully**\n"
                    f"🎵 **Song:** {song_name}\n"
                    f"👤 **Artist:** {artist_name}\n"
                    f"💽 **Album:** {caption_metadata.get('albumName', 'Unknown')}\n"
                    f"🆔 **Music ID:** {caption_metadata.get('musicId', 'N/A')}\n"
                    f"⏱ **Duration:** {self.format_duration(duration_for_file) if duration_for_file else 'Unknown'}\n"
                    f"🌐 **Language:** {caption_metadata.get('language', 'Unknown')}\n"
                    f"📅 **Year:** {caption_metadata.get('year', 'Unknown')}\n"
                    f"📁 **Quality:** {file_ext.upper()[1:]} - Highest Available\n"
                    f"📤 **Status:** Sent to dump channel",
                    "SUCCESS"
                )
                
                # Cleanup audio file
                try:
                    os.remove(audio_path)
                except:
                    pass
                
            except Exception as e:
                logger.error(f"Error processing highest quality download for {song_name}: {e}")
                self.progress_stats['failed'] += 1
                await self.send_logs_notification(
                    context,
                    f"❌ **Song Download Failed**\n"
                    f"🎵 **Song:** {song_name}\n"
                    f"🆔 **ID:** {song_id or 'Unknown'}\n"
                    f"📝 **Error:** {str(e)}\n"
                    f"🔗 **Download URL:** Available\n"
                    f"📊 **Status:** Failed during processing",
                    "ERROR"
                )
        else:
            logger.error(f"No download URLs available for {song_name}")
            self.progress_stats['skipped'] += 1
            await self.send_logs_notification(
                context,
                f"⏭️ **Song Skipped**\n"
                f"🎵 **Song:** {song_name}\n"
                f"🆔 **ID:** {song_id or 'Unknown'}\n"
                f"📝 **Reason:** No download URLs available\n"
                f"📊 **Status:** Skipped - No valid download links found",
                "SKIP"
            )
        
        # Cleanup thumbnail
        if thumbnail_path and os.path.exists(thumbnail_path):
            try:
                os.remove(thumbnail_path)
            except:
                pass
    
    async def process_id(self, id_str: str, context: ContextTypes.DEFAULT_TYPE, progress_info: Dict = None) -> None:
        """Process a single ID using the appropriate endpoint"""
        try:
            logger.info(f"Processing ID: {id_str}")
            
            album_urls = []
            
            if self.is_album_id(id_str):
                # Use albums endpoint for numeric IDs
                logger.info(f"Processing as ALBUM ID: {id_str}")
                album_data = await self.get_album_by_id(id_str)
                
                if album_data and album_data.get('data'):
                    albums = album_data['data'] if isinstance(album_data['data'], list) else [album_data['data']]
                    for album in albums:
                        if album.get('url'):
                            album_urls.append(album['url'])
                            logger.info(f"Found album directly: {album['url']}")
            else:
                # Use songs endpoint for alphanumeric IDs
                logger.info(f"Processing as MUSIC ID: {id_str}")
                song_data = await self.get_song_by_id(id_str)
                
                if song_data and song_data.get('data'):
                    songs = song_data['data'] if isinstance(song_data['data'], list) else [song_data['data']]
                    
                    # Extract album URLs from songs
                    for song in songs:
                        if song.get('album', {}).get('url'):
                            album_urls.append(song['album']['url'])
                            logger.info(f"Found album from song: {song['album']['url']}")
            
            # Remove duplicates
            album_urls = list(dict.fromkeys(album_urls))
            
            if not album_urls:
                logger.warning(f"No albums found for ID: {id_str}")
                return
            
            logger.info(f"Found {len(album_urls)} album(s) for ID: {id_str}")
            
            # Process each album
            for album_url in album_urls:
                await self.process_album(album_url, id_str, context, progress_info)
                
        except Exception as e:
            logger.error(f"Error processing ID {id_str}: {e}")
    
    async def process_album(self, album_url: str, original_id: str, context: ContextTypes.DEFAULT_TYPE, progress_info: Dict = None) -> None:
        """Process an album URL and download all songs"""
        try:
            logger.info(f"Fetching metadata for album: {album_url}")
            
            album_metadata = await self.get_album_metadata(album_url)
            if not album_metadata or not album_metadata.get('songs'):
                logger.error(f"No songs found in album: {album_url}")
                return
            
            songs = album_metadata['songs']
            logger.info(f"Found {len(songs)} songs in album")
            
            # Send album info to admin
            await self.send_admin_progress(
                context,
                0,
                len(songs),
                "sᴛᴀʀᴛɪɴɢ ᴀʟʙᴜᴍ",
                "album_progress"
            )
            
            # Process each song
            for idx, song in enumerate(songs, 1):
                song_progress = {
                    'current': idx,
                    'total': len(songs)
                }
                await self.process_song_downloads(song, album_metadata, original_id, context, song_progress)
            
            # Send completion message to admin
            await self.send_admin_progress(
                context,
                len(songs),
                len(songs),
                "ᴄᴏᴍᴘʟᴇᴛᴇᴅ",
                "album_progress"
            )
                
        except Exception as e:
            logger.error(f"Error processing album {album_url}: {e}")
    
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /start command"""
        welcome_message = (
            "🎵 **Welcome to FTM Professional Music Bot v2.0!**\n\n"
            "**📋 Available Methods:**\n"
            "1️⃣ **Direct Command**: `/music <ID>` or `/album <ID>`\n"
            "2️⃣ **Text Message**: Send IDs directly as text\n"
            "3️⃣ **File Upload**: Upload .txt file with IDs\n\n"
            "**🆔 Supported ID Formats:**\n"
            "• **Music ID**: `s-iNUkwV` (alphanumeric)\n"
            "• **Album ID**: `55666576` (numeric)\n\n"
            "**✨ Professional Features:**\n"
            "• Multiple download formats\n"
            "• High-quality thumbnails\n"
            "• Detailed metadata\n"
            "• Progress tracking (Admin)\n"
            "• Duration display\n\n"
            "🤖 **Powered by FTM Professional Bot**"
        )
        await update.message.reply_text(welcome_message, parse_mode='Markdown')
    
    async def music_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /music <id> command"""
        if not context.args:
            await update.message.reply_text(
                "❌ **Usage**: `/music <music_id>`\n"
                "📝 **Example**: `/music s-iNUkwV`",
                parse_mode='Markdown'
            )
            return
        
        # Clear processed songs for new request
        self.processed_songs.clear()
        
        music_id = context.args[0].strip()
        
        if self.is_album_id(music_id):
            await update.message.reply_text(
                "❌ **This looks like an Album ID!**\n"
                f"💡 **Use**: `/album {music_id}` instead",
                parse_mode='Markdown'
            )
            return
        
        await update.message.reply_text(
            f"🎵 **Processing Music ID**: `{music_id}`\n"
            "⏳ **Starting download...**",
            parse_mode='Markdown'
        )
        
        await self.process_id(music_id, context)
        
        await update.message.reply_text(
            f"✅ **Completed processing Music ID**: `{music_id}`",
            parse_mode='Markdown'
        )
    
    async def album_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /album <id> command"""
        if not context.args:
            await update.message.reply_text(
                "❌ **Usage**: `/album <album_id>`\n"
                "📝 **Example**: `/album 55666576`",
                parse_mode='Markdown'
            )
            return
        
        # Clear processed songs for new request
        self.processed_songs.clear()
        
        album_id = context.args[0].strip()
        
        if not self.is_album_id(album_id):
            await update.message.reply_text(
                "❌ **This looks like a Music ID!**\n"
                f"💡 **Use**: `/music {album_id}` instead",
                parse_mode='Markdown'
            )
            return
        
        await update.message.reply_text(
            f"💽 **Processing Album ID**: `{album_id}`\n"
            "⏳ **Starting download...**",
            parse_mode='Markdown'
        )
        
        await self.process_id(album_id, context)
        
        await update.message.reply_text(
            f"✅ **Completed processing Album ID**: `{album_id}`",
            parse_mode='Markdown'
        )
    
    async def handle_text_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle direct text messages with IDs"""
        try:
            text = update.message.text.strip()
            
            # Clear processed songs for new request
            self.processed_songs.clear()
            
            # Extract IDs from text
            ids = self.extract_ids_from_text(text)
            
            if not ids:
                await update.message.reply_text(
                    "❌ **No valid IDs found in your message**\n\n"
                    "📝 **Send IDs in these formats:**\n"
                    "• Music ID: `s-iNUkwV`\n"
                    "• Album ID: `55666576`\n"
                    "• Or use commands: `/music <id>` or `/album <id>`",
                    parse_mode='Markdown'
                )
                return
            
            await update.message.reply_text(
                f"📋 **Found {len(ids)} ID(s)**: `{', '.join(ids)}`\n"
                "⏳ **Processing started...**",
                parse_mode='Markdown'
            )
            
            # Send initial progress to admin
            await self.send_admin_progress(
                context,
                0,
                len(ids),
                "sᴛᴀʀᴛɪɴɢ ᴘʀᴏᴄᴇssɪɴɢ",
                "batch_progress"
            )
            
            # Process each ID
            for idx, id_str in enumerate(ids, 1):
                batch_progress = {
                    'current': idx,
                    'total': len(ids)
                }
                await self.process_id(id_str, context, batch_progress)
            
            await update.message.reply_text(
                f"✅ **Finished processing {len(ids)} ID(s)**\n"
                "🎵 **All files sent to dump channel!**",
                parse_mode='Markdown'
            )
            
            # Send completion to admin
            await self.send_admin_progress(
                context,
                len(ids),
                len(ids),
                "ᴄᴏᴍᴘʟᴇᴛᴇᴅ",
                "batch_progress"
            )
            
        except Exception as e:
            logger.error(f"Error handling text message: {e}")
            await update.message.reply_text(f"❌ **Error processing message**: {str(e)}")
    
    async def handle_document(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle document uploads"""
        try:
            document: Document = update.message.document
            
            # Check if it's a text file
            if not document.mime_type or 'text' not in document.mime_type:
                await update.message.reply_text(
                    "❌ **Please send a text file (.txt)**\n"
                    "📝 **File should contain Music ID or Album ID**",
                    parse_mode='Markdown'
                )
                return
            
            # Download the file
            file = await context.bot.get_file(document.file_id)
            
            with tempfile.NamedTemporaryFile(mode='w+b', delete=False) as temp_file:
                await file.download_to_drive(temp_file.name)
                
                # Read file content
                with open(temp_file.name, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
                
                # Clear processed songs for new request
                self.processed_songs.clear()
                
                # Extract IDs
                ids = self.extract_ids_from_text(content)
                
                # Cleanup temp file
                os.unlink(temp_file.name)
            
            if not ids:
                await update.message.reply_text(
                    "❌ **No valid Music ID or Album ID found**\n"
                    "📝 **Please check your file format**",
                    parse_mode='Markdown'
                )
                return
            
            await update.message.reply_text(
                f"📋 **Found {len(ids)} ID(s)**: `{', '.join(ids)}`\n"
                "⏳ **Processing started...**",
                parse_mode='Markdown'
            )
            
            # Send initial progress to admin
            await self.send_admin_progress(
                context,
                0,
                len(ids),
                "sᴛᴀʀᴛɪɴɢ ғɪʟᴇ ᴘʀᴏᴄᴇssɪɴɢ",
                "file_progress"
            )
            
            # Process each ID
            for idx, id_str in enumerate(ids, 1):
                file_progress = {
                    'current': idx,
                    'total': len(ids)
                }
                await self.process_id(id_str, context, file_progress)
            
            await update.message.reply_text(
                f"✅ **Finished processing {len(ids)} ID(s)**\n"
                "🎵 **All files sent to dump channel!**",
                parse_mode='Markdown'
            )
            
            # Send completion to admin
            await self.send_admin_progress(
                context,
                len(ids),
                len(ids),
                "ᴄᴏᴍᴘʟᴇᴛᴇᴅ",
                "file_progress"
            )
            
        except Exception as e:
            logger.error(f"Error handling document: {e}")
            await update.message.reply_text(f"❌ **Error processing file**: {str(e)}")
    
    def extract_urls_from_text(self, text: str) -> List[str]:
        """Extract JioSaavn URLs from text content"""
        urls = []
        lines = text.split('\n')
        
        # URL patterns for JioSaavn
        jiosaavn_patterns = [
            r'https?://(?:www\.)?jiosaavn\.com/(?:song|album)/[^\s]+',
            r'https?://(?:www\.)?saavn\.com/(?:song|album)/[^\s]+'
        ]
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
                
            for pattern in jiosaavn_patterns:
                matches = re.findall(pattern, line, re.IGNORECASE)
                for match in matches:
                    urls.append(match)
        
        # Remove duplicates while preserving order
        return list(dict.fromkeys(urls))
    
    def is_song_url(self, url: str) -> bool:
        """Check if URL is a song URL"""
        return '/song/' in url.lower()
    
    def is_album_url(self, url: str) -> bool:
        """Check if URL is an album URL"""
        return '/album/' in url.lower()
    
    async def get_album_url_from_song(self, song_url: str) -> Optional[str]:
        """Get album URL from song URL using FTM result API"""
        try:
            encoded_url = quote(song_url, safe='')
            url = f"{self.ftm_result_api}?query={encoded_url}"
            
            response = self.session.get(url, timeout=30)
            response.raise_for_status()
            
            data = response.json()
            
            if data and 'album_url' in data:
                logger.info(f"Successfully got album URL from song: {song_url}")
                return data['album_url']
            else:
                logger.error(f"No album URL found for song: {song_url}")
                return None
                
        except Exception as e:
            logger.error(f"Error getting album URL from song {song_url}: {e}")
            return None
    
    async def get_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /get command - must be used as reply to a .txt file with URLs"""
        try:
            # Check if this is a reply to a message
            if not update.message.reply_to_message:
                await update.message.reply_text(
                    "❌ **Please use /get as a reply to a .txt file**\n"
                    "📝 **File should contain JioSaavn song or album URLs**",
                    parse_mode='Markdown'
                )
                return
            
            replied_message = update.message.reply_to_message
            
            # Check if replied message has a document
            if not replied_message.document:
                await update.message.reply_text(
                    "❌ **Please reply to a .txt file**\n"
                    "📝 **File should contain JioSaavn URLs**",
                    parse_mode='Markdown'
                )
                return
            
            document = replied_message.document
            
            # Check if it's a text file
            if not document.mime_type or 'text' not in document.mime_type:
                await update.message.reply_text(
                    "❌ **Please reply to a .txt file**\n"
                    "📝 **File should contain JioSaavn URLs**",
                    parse_mode='Markdown'
                )
                return
            
            # Download and read the file
            file = await context.bot.get_file(document.file_id)
            
            with tempfile.NamedTemporaryFile(mode='w+b', delete=False) as temp_file:
                await file.download_to_drive(temp_file.name)
                
                # Read file content
                with open(temp_file.name, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
                
                # Extract URLs
                urls = self.extract_urls_from_text(content)
                
                # Cleanup temp file
                os.unlink(temp_file.name)
            
            if not urls:
                await update.message.reply_text(
                    "❌ **No JioSaavn URLs found**\n"
                    "📝 **Please check your file contains valid URLs**",
                    parse_mode='Markdown'
                )
                return
            
            await update.message.reply_text(
                f"📋 **Found {len(urls)} URL(s)**\n"
                "🔄 **Processing URLs to get albums...**",
                parse_mode='Markdown'
            )
            
            # Clear processed songs for new request
            self.processed_songs.clear()
            
            # Send initial progress to admin
            await self.send_admin_progress(
                context,
                0,
                len(urls),
                "sᴛᴀʀᴛɪɴɢ ᴜʀʟ ᴘʀᴏᴄᴇssɪɴɢ",
                "url_progress"
            )
            
            processed_count = 0
            
            # Process each URL immediately (one by one)
            for idx, url in enumerate(urls, 1):
                try:
                    # Only show URL processing for large batches to avoid spam
                    if len(urls) > 3:
                        await update.message.reply_text(
                            f"🔄 **Processing URL {idx}/{len(urls)}**",
                            parse_mode='Markdown'
                        )
                    
                    album_url = None
                    
                    if self.is_song_url(url):
                        # Get album URL from song URL
                        album_url = await self.get_album_url_from_song(url)
                        if not album_url:
                            await update.message.reply_text(
                                f"❌ **Could not get album from song URL {idx}**",
                                parse_mode='Markdown'
                            )
                            continue
                    elif self.is_album_url(url):
                        # Use album URL directly
                        album_url = url
                    else:
                        await update.message.reply_text(
                            f"❌ **Unknown URL type for URL {idx}**",
                            parse_mode='Markdown'
                        )
                        continue
                    
                    # Get album metadata and process immediately
                    album_data = await self.get_album_metadata(album_url)
                    
                    if album_data and album_data.get('songs'):
                        songs = album_data['songs']
                        album_name = album_data.get('name', 'Unknown Album')
                        
                        # Only show album details for transparency
                        await update.message.reply_text(
                            f"💿 **Album {idx}/{len(urls)}:** `{album_name}`\n"
                            f"🎵 **{len(songs)} songs** → 📥 **Downloading...**",
                            parse_mode='Markdown'
                        )
                        
                        # Send clean progress update
                        await self.send_admin_progress(
                            context,
                            idx,
                            len(urls),
                            f"⬇️ ᴅᴏᴡɴʟᴏᴀᴅɪɴɢ ᴀʟʙᴜᴍ {idx}/{len(urls)}",
                            "url_progress",
                            album_name,
                            f"{len(songs)} songs"
                        )
                        
                        # Process each song in album (no individual progress updates to avoid spam)
                        for song_idx, song in enumerate(songs, 1):
                            # Process song without progress spam
                            await self.process_song_from_album(song, album_data, context)
                        
                        processed_count += 1
                        
                        await update.message.reply_text(
                            f"✅ **{album_name}** → ✅ **Complete**\n"
                            f"📤 **{len(songs)} songs uploaded successfully**",
                            parse_mode='Markdown'
                        )
                        
                    else:
                        await update.message.reply_text(
                            f"❌ **Could not get album data for URL {idx}**",
                            parse_mode='Markdown'
                        )
                        
                except Exception as e:
                    logger.error(f"Error processing URL {url}: {e}")
                    await update.message.reply_text(
                        f"❌ **Error processing URL {idx}:** `{str(e)}`",
                        parse_mode='Markdown'
                    )
            
            await update.message.reply_text(
                f"🎉 **All URLs processed!**\n"
                f"✅ **Successfully processed:** {processed_count}/{len(urls)}\n"
                "🎵 **All files sent to dump channel!**",
                parse_mode='Markdown'
            )
            
            # Send completion to admin
            await self.send_admin_progress(
                context,
                len(urls),
                len(urls),
                "ᴄᴏᴍᴘʟᴇᴛᴇᴅ",
                "url_progress"
            )
            
        except Exception as e:
            logger.error(f"Error handling /get command: {e}")
            await update.message.reply_text(f"❌ **Error processing URLs**: {str(e)}")
    
    async def process_song_from_album(self, song: Dict, album_data: Dict, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Process a single song from album data"""
        try:
            # Extract song metadata (compatible with both FTM API and first API)
            song_metadata = {
                'songId': song.get('id') or song.get('songId'),
                'musicId': song.get('id') or song.get('songId'),  # For Music ID display
                'albumId': album_data.get('id') or album_data.get('albumid'),
                'year': song.get('year') or album_data.get('year'),
                'language': song.get('language') or album_data.get('language'),
                'duration': song.get('duration')
            }
            
            # Get download URLs
            download_urls = self.get_download_urls(song)
            
            if not download_urls:
                logger.error(f"No download URLs found for song: {song.get('song', 'Unknown')}")
                self.progress_stats['failed'] += 1
                return
            
            # Try downloading from URLs
            song_name = song.get('song') or song.get('name', 'Unknown Song')
            song_artists = song.get('primary_artists') or song.get('singers', 'Unknown Artist')
            
            # Clean song name for filename
            clean_name = re.sub(r'[^a-zA-Z0-9\s\-_]', '', song_name)
            filename = f"{clean_name}.mp3"
            filepath = f"downloads/{filename}"
            
            # Try each URL until one works
            downloaded = False
            for url in download_urls:
                if self.download_file(url, filepath):
                    downloaded = True
                    break
            
            if not downloaded:
                logger.error(f"Failed to download song: {song_name}")
                self.progress_stats['failed'] += 1
                return
            
            # Create caption
            caption = self.format_caption(song_metadata)
            
            # Send to dump channel
            try:
                with open(filepath, 'rb') as audio_file:
                    await context.bot.send_audio(
                        chat_id=self.dump_channel_id,
                        audio=audio_file,
                        caption=caption,
                        performer=song_artists,
                        title=song_name,
                        duration=int(song.get('duration', 0)) if song.get('duration') else None
                    )
                
                self.progress_stats['downloaded'] += 1
                self.progress_stats['processed'] += 1
                logger.info(f"Successfully sent song: {song_name}")
                
            except Exception as send_error:
                logger.error(f"Error sending song {song_name}: {send_error}")
                self.progress_stats['failed'] += 1
            
            # Cleanup downloaded file
            try:
                os.unlink(filepath)
            except:
                pass
                
        except Exception as e:
            logger.error(f"Error processing song from album: {e}")
            self.progress_stats['failed'] += 1
    
    async def post_init(self, application):
        """Send startup notification after bot is initialized"""
        if self.send_startup_notification:
            await self.send_startup_message(application.bot.get_chat(self.logs_channel_id))
            self.send_startup_notification = False
    
    def setup_bot_application(self):
        """Setup bot application with handlers"""
        # Initialize bot application
        self.application = Application.builder().token(self.bot_token).build()
        
        # Add handlers
        self.application.add_handler(CommandHandler("start", self.start_command))
        self.application.add_handler(CommandHandler("music", self.music_command))
        self.application.add_handler(CommandHandler("album", self.album_command))
        self.application.add_handler(CommandHandler("get", self.get_command))
        self.application.add_handler(MessageHandler(filters.Document.ALL, self.handle_document))
        self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_text_message))
    
    async def send_startup_notification_async(self):
        """Send startup notification"""
        try:
            startup_msg = (
                "🚀 **FTM Professional Bot v2.0 Started (Webhook Mode)**\n\n"
                "✅ **Bot Status:** Online\n"
                "🌐 **Mode:** Web Server + Webhook\n"
                "🔧 **Features Loaded:**\n"
                "• Music ID Processing\n"
                "• Album ID Processing\n"
                "• URL Processing\n"
                "• File Upload Support\n"
                "• Progress Tracking\n"
                "• Quality Download Management\n\n"
                "📊 **Ready to process music requests!**"
            )
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            formatted_message = f"🕐 **{timestamp}** | STARTUP\n{startup_msg}"
            
            await self.application.bot.send_message(
                chat_id=self.logs_channel_id,
                text=formatted_message,
                parse_mode='Markdown'
            )
        except Exception as e:
            logger.error(f"Failed to send startup notification: {e}")
    
    def run_polling(self):
        """Run the bot with polling (for testing without webhook)"""
        # Setup bot application
        self.setup_bot_application()
        
        logger.info("FTM Professional Bot v2.0 started successfully in polling mode!")
        
        # Run bot with polling
        try:
            self.application.run_polling(
                poll_interval=1.0,
                timeout=30,
                bootstrap_retries=-1,
                read_timeout=30,
                write_timeout=30,
                connect_timeout=30,
                pool_timeout=30
            )
        except KeyboardInterrupt:
            logger.info("Bot stopped by user")
        except Exception as e:
            logger.error(f"Bot stopped due to error: {e}")

if __name__ == "__main__":
    bot = FTMBot()
    bot.run_polling()
