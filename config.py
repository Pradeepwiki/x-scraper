"""
Configuration for the X.com Likes Scraper.
Fill in your details below before running.
"""

# Your X.com username (without @) -- this is needed for the likes page URL
# e.g., if your handle is @elonmusk, set this to "elonmusk"
X_USERNAME = "NephliumDark"

# Your X.com authentication cookie (auth_token).
# How to get it:
#   1. Log into X.com in Chrome
#   2. Open DevTools (F12) -> Application -> Cookies -> x.com
#   3. Copy the value of the "auth_token" cookie
#   4. Paste it below
AUTH_TOKEN = "d422f53effa34a71add229c05c6ad28047a486f5"

# Additional cookies that might be needed (optional)
# Some setups may also require "ct0" (CSRF token) cookie
CT0_TOKEN = ""  # Optional, but recommended for some requests

# How many likes to process in this run (start small, scale up)
MAX_LIKES = 2000  # High limit — will stop when no more posts are available

# Delay between actions (seconds) -- mimics human behavior
MIN_DELAY = 2.0
MAX_DELAY = 4.0
SCROLL_PAUSE = 6.0             # Pause between scrolls (higher = less rate limiting)

# Take a longer break every N posts to avoid X.com rate limiting
BREAK_INTERVAL = 12       # Wait after every N posts (lower = more breaks)
BREAK_DURATION = 120      # Wait duration in seconds (longer = more human-like)

# Rate-limit retry settings
MAX_RATE_LIMIT_RETRIES = 30      # Max consecutive retries before giving up
RETRY_BASE_DELAY = 120            # Initial wait (seconds) before retry
RETRY_MAX_DELAY = 900            # Max wait (15 minutes) between retries

# Checkpoint / resume settings
CHECKPOINT_INTERVAL = 5          # Save checkpoint every N posts (more frequent for safety)
CHECKPOINT_DIR = "checkpoints"  # Directory for checkpoint files

# Output settings
OUTPUT_DIR = "output"
OUTPUT_FORMAT = "json"  # "json" or "markdown"

# Whether to download media for processing
DOWNLOAD_MEDIA = True
MEDIA_DIR = "media"

# Processing options
OCR_IMAGES = True       # OCR images in posts using EasyOCR
TRANSCRIBE_VIDEOS = True   # Transcribe videos using faster-whisper
