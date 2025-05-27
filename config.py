import os
import json # For parsing service account JSON string
from dotenv import load_dotenv

# Load environment variables from .env file
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
load_dotenv(os.path.join(BASE_DIR, '.env'))

# --- Google API Credentials Configuration ---
# Prioritize Service Account, then OAuth Client Secret as fallback for local dev/testing.

# Service Account Credentials (Option 1: JSON content as string)
GOOGLE_SERVICE_ACCOUNT_CREDENTIALS_JSON_AS_STRING = os.getenv("GOOGLE_SERVICE_ACCOUNT_CREDENTIALS_JSON_CONTENT")

# Service Account Credentials (Option 2: File Path)
GOOGLE_SERVICE_ACCOUNT_KEY_JSON_PATH_ENV = os.getenv("GOOGLE_SERVICE_ACCOUNT_KEY_JSON_PATH")

# OAuth 2.0 Client Secret (Fallback or for specific local testing)
GOOGLE_CLIENT_SECRET_FILENAME_ENV = os.getenv("GOOGLE_CLIENT_SECRET_JSON_FILENAME")
GOOGLE_CLIENT_SECRET_PATH_CONFIG = os.path.join(BASE_DIR, GOOGLE_CLIENT_SECRET_FILENAME_ENV) if GOOGLE_CLIENT_SECRET_FILENAME_ENV else None

# Determine which Google credential method to use
GOOGLE_AUTH_METHOD = None
GOOGLE_SERVICE_ACCOUNT_INFO = None # Will hold parsed service account JSON if used
GOOGLE_SERVICE_ACCOUNT_FILE_PATH = None # Will hold path if that method is used

if GOOGLE_SERVICE_ACCOUNT_CREDENTIALS_JSON_AS_STRING:
    try:
        GOOGLE_SERVICE_ACCOUNT_INFO = json.loads(GOOGLE_SERVICE_ACCOUNT_CREDENTIALS_JSON_AS_STRING)
        GOOGLE_AUTH_METHOD = "SERVICE_ACCOUNT_JSON_STRING"
        print("CONFIG: Using Google Service Account from JSON string in .env")
    except json.JSONDecodeError as e:
        print(f"ERROR CONFIG: Could not parse GOOGLE_SERVICE_ACCOUNT_CREDENTIALS_JSON_CONTENT: {e}. Check .env format.")
        GOOGLE_AUTH_METHOD = None # Fallback or error
elif GOOGLE_SERVICE_ACCOUNT_KEY_JSON_PATH_ENV and os.path.exists(GOOGLE_SERVICE_ACCOUNT_KEY_JSON_PATH_ENV):
    GOOGLE_SERVICE_ACCOUNT_FILE_PATH = GOOGLE_SERVICE_ACCOUNT_KEY_JSON_PATH_ENV
    GOOGLE_AUTH_METHOD = "SERVICE_ACCOUNT_FILE_PATH"
    print(f"CONFIG: Using Google Service Account from file path: {GOOGLE_SERVICE_ACCOUNT_FILE_PATH}")
elif GOOGLE_CLIENT_SECRET_PATH_CONFIG and os.path.exists(GOOGLE_CLIENT_SECRET_PATH_CONFIG):
    # This is the path for OAuth client secrets, used by gdrive/youtube services if no service account
    GOOGLE_AUTH_METHOD = "OAUTH_CLIENT_SECRET"
    print(f"CONFIG: Using OAuth Client Secret from: {GOOGLE_CLIENT_SECRET_PATH_CONFIG}")
else:
    print("ERROR CONFIG: No valid Google API credentials found (Service Account or OAuth Client Secret).")
    print("Please set GOOGLE_SERVICE_ACCOUNT_CREDENTIALS_JSON_CONTENT or GOOGLE_SERVICE_ACCOUNT_KEY_JSON_PATH in .env for server deployments,")
    print("or GOOGLE_CLIENT_SECRET_JSON_FILENAME for local OAuth testing.")

# The service modules (gdrive.py, youtube_uploader.py) will need to be updated to check 
# GOOGLE_AUTH_METHOD and use GOOGLE_SERVICE_ACCOUNT_INFO, GOOGLE_SERVICE_ACCOUNT_FILE_PATH, 
# or GOOGLE_CLIENT_SECRET_PATH_CONFIG accordingly.

# --- Other Configurations ---
# Google Drive specific folder to scan
GDRIVE_FOLDER_ID = os.getenv("GDRIVE_TARGET_FOLDER_ID", "...")

# Gemini API Key
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "...")

# Video storage directories
VIDEO_DIR = os.path.join(BASE_DIR, "videos")
RAW_DIR = os.path.join(VIDEO_DIR, "raw")
MERGED_DIR = os.path.join(VIDEO_DIR, "merged")
OUTPUT_DIR = os.path.join(VIDEO_DIR, "output")

STATIC_DIR_CONFIG = os.path.join(BASE_DIR, "static")
STATIC_AUDIO_DIR = os.path.join(STATIC_DIR_CONFIG, "audio")

for dir_path in [VIDEO_DIR, RAW_DIR, MERGED_DIR, OUTPUT_DIR, STATIC_DIR_CONFIG, STATIC_AUDIO_DIR]:
    if not os.path.exists(dir_path):
        os.makedirs(dir_path)

# Database file
DB_FILE_PATH = os.path.join(BASE_DIR, "db.json")

# --- Verification Prints (can be reduced in production) ---
print(f"CONFIG - Base Directory: {BASE_DIR}")
print(f"CONFIG - Selected Google Auth Method: {GOOGLE_AUTH_METHOD}")
if GOOGLE_AUTH_METHOD == "OAUTH_CLIENT_SECRET":
    print(f"CONFIG - OAuth Client Secret Path: {GOOGLE_CLIENT_SECRET_PATH_CONFIG}")
print(f"CONFIG - GDrive Folder ID: {GDRIVE_FOLDER_ID}")
print(f"CONFIG - Gemini API Key: {'SET' if GEMINI_API_KEY and GEMINI_API_KEY != '...' else 'NOT SET'}")
print(f"CONFIG - DB File Path: {DB_FILE_PATH}")
