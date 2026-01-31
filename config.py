import os
from typing import Optional

class Config:
    # Telegram API Configuration
    API_ID: int = int(os.getenv("API_ID", 0))
    API_HASH: str = os.getenv("API_HASH", "")
    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
    
    # Wasabi S3 Configuration
    WASABI_ACCESS_KEY: str = os.getenv("WASABI_ACCESS_KEY", "")
    WASABI_SECRET_KEY: str = os.getenv("WASABI_SECRET_KEY", "")
    WASABI_BUCKET: str = os.getenv("WASABI_BUCKET", "")
    WASABI_REGION: str = os.getenv("WASABI_REGION", "us-east-1")
    WASABI_ENDPOINT: str = f"https://s3.{WASABI_REGION}.wasabisys.com"
    
    # Upload Configuration
    MAX_FILE_SIZE: int = 4 * 1024 * 1024 * 1024  # 4GB
    URL_EXPIRY: int = 24 * 3600  # 24 hours in seconds
    DOWNLOAD_DIR: str = "downloads"
    
    # Multipart Upload Configuration
    MULTIPART_THRESHOLD: int = 25 * 1024 * 1024  # 25MB
    MULTIPART_CHUNKSIZE: int = 25 * 1024 * 1024  # 25MB chunks
    MAX_CONCURRENCY: int = 10
    
    # Rate Limiting
    PROGRESS_UPDATE_INTERVAL: float = 1.5  # seconds
    
    # User Limits
    MAX_CONCURRENT_UPLOADS_PER_USER: int = 1
    DAILY_UPLOAD_LIMIT: Optional[int] = None  # Set to None for unlimited
    
    # Logging
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    LOG_FILE: Optional[str] = os.getenv("LOG_FILE")

# Create config instance
config = Config()
