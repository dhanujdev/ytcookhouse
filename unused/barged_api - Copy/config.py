import os
from dotenv import load_dotenv

# Load environment variables from .env file
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
load_dotenv(os.path.join(BASE_DIR, '.env'))

# Google Drive and YouTube API
# Expects GOOGLE_CLIENT_SECRET_JSON_FILENAME to be set in .env
# and the actual JSON file to be in the BASE_DIR (barged_api/)
GOOGLE_CLIENT_SECRET_FILENAME = os.getenv("GOOGLE_CLIENT_SECRET_JSON_FILENAME")
GOOGLE_CLIENT_SECRET_PATH = os.path.join(BASE_DIR, GOOGLE_CLIENT_SECRET_FILENAME) if GOOGLE_CLIENT_SECRET_FILENAME else None

# This variable will be used by services needing Google OAuth (Drive, YouTube)
# Ensure YOUTUBE_CREDENTIALS was a placeholder for this path in your services
# If a service specifically used YOUTUBE_CREDENTIALS, it should now use GOOGLE_CLIENT_SECRET_PATH
# Or we can keep YOUTUBE_CREDENTIALS if it makes sense semantically, pointing to GOOGLE_CLIENT_SECRET_PATH
YOUTUBE_CREDENTIALS_PATH = GOOGLE_CLIENT_SECRET_PATH 
GDRIVE_CREDENTIALS_PATH = GOOGLE_CLIENT_SECRET_PATH

# Google Drive specific folder to scan
GDRIVE_FOLDER_ID = os.getenv("GDRIVE_TARGET_FOLDER_ID", "...") # Default to "..." if not set

# Gemini API Key
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "...") # Default to "..." if not set

# Video storage directories
VIDEO_DIR = os.path.join(BASE_DIR, "videos") # Make video_dir relative to app root
RAW_DIR = os.path.join(VIDEO_DIR, "raw")
MERGED_DIR = os.path.join(VIDEO_DIR, "merged")
OUTPUT_DIR = os.path.join(VIDEO_DIR, "output")

# Ensure these directories exist (moved from main.py for consistency)
if not os.path.exists(VIDEO_DIR):
    os.makedirs(VIDEO_DIR)
if not os.path.exists(RAW_DIR):
    os.makedirs(RAW_DIR)
if not os.path.exists(MERGED_DIR):
    os.makedirs(MERGED_DIR)
if not os.path.exists(OUTPUT_DIR):
    os.makedirs(OUTPUT_DIR)

STATIC_DIR_CONFIG = os.path.join(BASE_DIR, "static") # Renamed to avoid conflict if main.py also defines STATIC_DIR
STATIC_AUDIO_DIR = os.path.join(STATIC_DIR_CONFIG, "audio")
if not os.path.exists(STATIC_DIR_CONFIG):
    os.makedirs(STATIC_DIR_CONFIG)
if not os.path.exists(STATIC_AUDIO_DIR):
    os.makedirs(STATIC_AUDIO_DIR)

# Database file
DB_FILE_PATH = os.path.join(BASE_DIR, "db.json")

# Print statements for verification (optional, can be removed in production)
print(f"CONFIG - Base Directory: {BASE_DIR}")
print(f"CONFIG - Loaded .env from: {os.path.join(BASE_DIR, '.env')}")
print(f"CONFIG - Google Client Secret Filename: {GOOGLE_CLIENT_SECRET_FILENAME}")
print(f"CONFIG - Google Client Secret Path: {GOOGLE_CLIENT_SECRET_PATH}")
print(f"CONFIG - GDrive Folder ID: {GDRIVE_FOLDER_ID}")
print(f"CONFIG - Gemini API Key: {'SET' if GEMINI_API_KEY and GEMINI_API_KEY != '...' else 'NOT SET'}")
print(f"CONFIG - Video Root Directory: {VIDEO_DIR}")
print(f"CONFIG - DB File Path: {DB_FILE_PATH}")

# Verify credential path existence (optional)
if GOOGLE_CLIENT_SECRET_PATH and not os.path.exists(GOOGLE_CLIENT_SECRET_PATH):
    print(f"WARNING: Google Client Secret file not found at: {GOOGLE_CLIENT_SECRET_PATH}")
    print("Please ensure GOOGLE_CLIENT_SECRET_JSON_FILENAME in .env is correct and the file exists in the barged_api directory.")
    # You might want to raise an error here or handle it if the file is critical for startup
    # For now, just a warning, services will fail later if it's missing.
