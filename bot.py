import os
import asyncio
import time
import math
import mimetypes
import re
import logging
from pathlib import Path
from typing import Dict, Optional
from collections import defaultdict

# Telegram API Client
from pyrogram import Client, filters, idle
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import FloodWait, RPCError

# S3 Client for Wasabi
import boto3
from botocore.exceptions import ClientError
from boto3.s3.transfer import TransferConfig

# Import configuration
from config import config

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class WasabiUploadBot:
    def __init__(self):
        # Validate configuration
        self.validate_config()
        
        # Initialize Pyrogram Client
        self.app = Client(
            "wasabi_bot",
            api_id=config.API_ID,
            api_hash=config.API_HASH,
            bot_token=config.BOT_TOKEN
        )
        
        # Initialize S3 client for Wasabi
        self.s3_client = boto3.client(
            's3',
            endpoint_url=config.WASABI_ENDPOINT,
            aws_access_key_id=config.WASABI_ACCESS_KEY,
            aws_secret_access_key=config.WASABI_SECRET_KEY,
            region_name=config.WASABI_REGION
        )
        
        # Upload configuration
        self.transfer_config = TransferConfig(
            multipart_threshold=config.MULTIPART_THRESHOLD,
            max_concurrency=config.MAX_CONCURRENCY,
            multipart_chunksize=config.MULTIPART_CHUNKSIZE,
            use_threads=True
        )
        
        # User task tracking
        self.user_tasks: Dict[int, asyncio.Task] = {}
        self.user_status: Dict[int, dict] = {}
        
        # Register handlers
        self.register_handlers()
        
    def validate_config(self):
        """Validate all required configuration variables"""
        required_attrs = [
            'API_ID', 'API_HASH', 'BOT_TOKEN',
            'WASABI_ACCESS_KEY', 'WASABI_SECRET_KEY', 'WASABI_BUCKET'
        ]
        
        missing = []
        for attr in required_attrs:
            if not getattr(config, attr, None):
                missing.append(attr)
        
        if missing:
            raise ValueError(f"Missing configuration: {', '.join(missing)}")
        
        # Create necessary directories
        os.makedirs(config.DOWNLOAD_DIR, exist_ok=True)
        logger.info("Configuration validated successfully")
    
    def sanitize_filename(self, filename: str) -> str:
        """Sanitize filename to prevent path traversal and special characters"""
        if not filename:
            return f"file_{int(time.time())}"
        
        # Remove path components
        filename = Path(filename).name
        
        # Remove special characters but keep some safe ones
        safe_chars = re.sub(r'[^\w\-.@() ]', '', filename)
        
        # Replace spaces with underscores
        safe_chars = safe_chars.replace(' ', '_')
        
        # Ensure filename is not too long
        if len(safe_chars) > 255:
            name, ext = os.path.splitext(safe_chars)
            safe_chars = name[:250] + ext
        
        return safe_chars if safe_chars else f"file_{int(time.time())}"
    
    def human_size(self, size_bytes: int) -> str:
        """Convert bytes to human readable format"""
        if not size_bytes:
            return "0B"
        
        size_name = ("B", "KB", "MB", "GB", "TB", "PB")
        i = int(math.floor(math.log(size_bytes, 1024)))
        p = math.pow(1024, i)
        s = round(size_bytes / p, 2)
        return f"{s} {size_name[i]}"
    
    async def progress_bar(
        self,
        current: int,
        total: int,
        status_msg: Message,
        start_time: float,
        operation: str = "Processing"
    ) -> None:
        """Update progress bar with rate limiting"""
        try:
            # Use function attributes for rate limiting
            if not hasattr(self.progress_bar, 'last_update'):
                self.progress_bar.last_update = {}
            
            chat_id = status_msg.chat.id
            now = time.time()
            
            # Rate limit updates to every 1.5 seconds
            last_update = self.progress_bar.last_update.get(chat_id, 0)
            if now - last_update < 1.5 and current != total:
                return
            
            self.progress_bar.last_update[chat_id] = now
            
            elapsed = now - start_time
            speed = current / elapsed if elapsed > 0 else 0
            percentage = (current / total) * 100 if total > 0 else 0
            
            # Progress bar
            filled = min(20, int(20 * percentage // 100))
            bar = "▰" * filled + "▱" * (20 - filled)
            
            # Time calculations
            eta = (total - current) / speed if speed > 0 else 0
            
            # Format message
            progress_text = (
                f"**{operation}...**\n\n"
                f"`{bar}`\n"
                f"**Progress:** `{percentage:.1f}%`\n"
                f"**Size:** `{self.human_size(current)} / {self.human_size(total)}`\n"
                f"**Speed:** `{self.human_size(speed)}/s`\n"
                f"**ETA:** `{time.strftime('%H:%M:%S', time.gmtime(eta)) if eta > 0 else 'Calculating...'}`"
            )
            
            await status_msg.edit_text(progress_text)
            
        except FloodWait as e:
            await asyncio.sleep(e.value)
        except Exception as e:
            logger.error(f"Progress bar error: {e}")
    
    def register_handlers(self):
        """Register all message handlers"""
        
        @self.app.on_message(filters.command("start"))
        async def start_handler(client, message: Message):
            await message.reply_text(
                "🚀 **Wasabi High-Speed Upload Bot**\n\n"
                "Send me any file (up to 4GB) and I'll upload it to Wasabi storage "
                "and generate streaming links for VLC/MX Player.\n\n"
                "**Features:**\n"
                "• High-speed uploads via multipart\n"
                "• 4GB file support\n"
                "• Direct streaming links\n"
                "• Progress tracking\n\n"
                "**Commands:**\n"
                "/start - Show this message\n"
                "/cancel - Cancel current upload\n"
                "/status - Check upload status"
            )
        
        @self.app.on_message(filters.command("cancel"))
        async def cancel_handler(client, message: Message):
            user_id = message.from_user.id
            
            if user_id in self.user_tasks:
                task = self.user_tasks[user_id]
                task.cancel()
                del self.user_tasks[user_id]
                
                if user_id in self.user_status:
                    file_path = self.user_status[user_id].get('file_path')
                    if file_path and os.path.exists(file_path):
                        os.remove(file_path)
                    del self.user_status[user_id]
                
                await message.reply_text("✅ Upload cancelled and cleaned up.")
            else:
                await message.reply_text("❌ No active upload to cancel.")
        
        @self.app.on_message(filters.command("status"))
        async def status_handler(client, message: Message):
            user_id = message.from_user.id
            
            if user_id in self.user_status:
                status = self.user_status[user_id]
                text = (
                    f"**Upload Status:**\n"
                    f"File: `{status.get('filename', 'Unknown')}`\n"
                    f"Progress: `{status.get('progress', 0):.1f}%`\n"
                    f"Speed: `{status.get('speed', '0B')}/s`\n"
                    f"Elapsed: `{status.get('elapsed', 0):.1f}s`"
                )
                await message.reply_text(text)
            else:
                await message.reply_text("No active uploads.")
        
        @self.app.on_message(filters.document | filters.video | filters.audio)
        async def file_handler(client: Client, message: Message):
            # Get media from message
            media = message.document or message.video or message.audio
            if not media:
                return
            
            # Check file size limit
            if media.file_size > config.MAX_FILE_SIZE:
                await message.reply_text(
                    f"❌ File size exceeds {self.human_size(config.MAX_FILE_SIZE)} limit."
                )
                return
            
            # Check concurrent uploads
            if message.from_user.id in self.user_tasks:
                await message.reply_text(
                    "⏳ You already have an upload in progress. "
                    "Use /cancel to stop it first."
                )
                return
            
            # Start upload task
            task = asyncio.create_task(
                self.process_upload(client, message, media)
            )
            self.user_tasks[message.from_user.id] = task
            
            try:
                await task
            except asyncio.CancelledError:
                logger.info(f"Upload cancelled for user {message.from_user.id}")
            except Exception as e:
                logger.error(f"Upload error: {e}")
                await message.reply_text(f"❌ Upload failed: {str(e)}")
            finally:
                # Cleanup
                if message.from_user.id in self.user_tasks:
                    del self.user_tasks[message.from_user.id]
                if message.from_user.id in self.user_status:
                    del self.user_status[message.from_user.id]
    
    async def process_upload(
        self,
        client: Client,
        message: Message,
        media
    ) -> None:
        """Process file upload from Telegram to Wasabi"""
        user_id = message.from_user.id
        
        # Sanitize filename
        original_filename = media.file_name or f"file_{int(time.time())}"
        safe_filename = self.sanitize_filename(original_filename)
        
        # Create user-specific download path
        user_dir = os.path.join(config.DOWNLOAD_DIR, str(user_id))
        os.makedirs(user_dir, exist_ok=True)
        file_path = os.path.join(user_dir, safe_filename)
        
        # Status tracking
        self.user_status[user_id] = {
            'filename': safe_filename,
            'file_path': file_path,
            'progress': 0,
            'speed': '0B/s',
            'elapsed': 0,
            'start_time': time.time()
        }
        
        # Step 1: Download from Telegram
        status_msg = await message.reply_text("📥 **Downloading from Telegram...**")
        download_start = time.time()
        
        try:
            # Download with progress
            await client.download_media(
                message,
                file_name=file_path,
                progress=self.progress_bar,
                progress_args=(status_msg, download_start, "Downloading")
            )
            
            # Update status
            download_time = time.time() - download_start
            self.user_status[user_id]['elapsed'] = download_time
            
            # Step 2: Upload to Wasabi
            await status_msg.edit_text("📤 **Uploading to Wasabi Storage...**")
            upload_start = time.time()
            
            # Get MIME type
            mime_type = mimetypes.guess_type(file_path)[0] or "application/octet-stream"
            
            # Upload to Wasabi with multipart
            s3_key = f"{user_id}/{int(time.time())}_{safe_filename}"
            
            s3_client.upload_file(
                file_path,
                config.WASABI_BUCKET,
                s3_key,
                ExtraArgs={
                    'ContentType': mime_type,
                    'Metadata': {
                        'original_filename': original_filename,
                        'telegram_user': str(user_id),
                        'upload_timestamp': str(int(time.time()))
                    }
                },
                Config=self.transfer_config
            )
            
            upload_time = time.time() - upload_start
            
            # Step 3: Generate streaming URLs
            stream_url = self.s3_client.generate_presigned_url(
                'get_object',
                Params={
                    'Bucket': config.WASABI_BUCKET,
                    'Key': s3_key,
                    'ResponseContentDisposition': f'attachment; filename="{original_filename}"'
                },
                ExpiresIn=config.URL_EXPIRY
            )
            
            # Cleanup local file
            if os.path.exists(file_path):
                os.remove(file_path)
            
            # Prepare response
            total_time = download_time + upload_time
            avg_speed = media.file_size / total_time if total_time > 0 else 0
            
            buttons = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔗 Direct Download", url=stream_url)],
                [InlineKeyboardButton("📺 Open in MX Player", 
                    url=f"intent:{stream_url}#Intent;package=com.mxtech.videoplayer.ad;end")],
                [InlineKeyboardButton("🎬 Open in VLC", 
                    url=f"vlc://{stream_url}")]
            ])
            
            await status_msg.edit_text(
                f"✅ **Upload Complete!**\n\n"
                f"**File:** `{original_filename}`\n"
                f"**Size:** `{self.human_size(media.file_size)}`\n"
                f"**Download Time:** `{download_time:.1f}s`\n"
                f"**Upload Time:** `{upload_time:.1f}s`\n"
                f"**Average Speed:** `{self.human_size(avg_speed)}/s`\n\n"
                f"**Streaming Links:**\n"
                f"• Direct: `{stream_url[:60]}...`\n\n"
                f"Links expire in {config.URL_EXPIRY//3600} hours.",
                reply_markup=buttons
            )
            
            logger.info(f"Upload completed for user {user_id}: {original_filename}")
            
        except Exception as e:
            logger.error(f"Upload process error: {e}")
            await status_msg.edit_text(f"❌ **Error:** {str(e)}")
            
            # Cleanup on error
            if os.path.exists(file_path):
                os.remove(file_path)
            
            raise
    
    async def run(self):
        """Start the bot"""
        logger.info("Starting Wasabi Upload Bot...")
        await self.app.start()
        
        # Get bot info
        me = await self.app.get_me()
        logger.info(f"Bot started as @{me.username}")
        
        # Idle until stopped
        await idle()
        
        # Stop the bot
        await self.app.stop()
        logger.info("Bot stopped")

def main():
    """Main entry point"""
    try:
        bot = WasabiUploadBot()
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        logger.info("Bot interrupted by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}")

if __name__ == "__main__":
    main()
