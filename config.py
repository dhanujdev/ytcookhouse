
import os
import json 
from dotenv import load_dotenv
import platform # Import platform module

# Determine the true base directory of the application (barged_api folder)
# __file__ is the path to config.py, so its dirname is barged_api
APP_ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(APP_ROOT_DIR, '.env')) # Load .env from barged_api directory

# --- Google API Credentials Configuration ---
SA_TYPE = os.getenv("SA_TYPE", "service_account")
SA_PROJECT_ID = os.getenv("SA_PROJECT_ID")
SA_PRIVATE_KEY_ID = os.getenv("SA_PRIVATE_KEY_ID")
SA_PRIVATE_KEY = os.getenv("SA_PRIVATE_KEY") # Must contain literal \\n for newlines in .env
SA_CLIENT_EMAIL = os.getenv("SA_CLIENT_EMAIL")
SA_CLIENT_ID = os.getenv("SA_CLIENT_ID")
SA_AUTH_URI = os.getenv("SA_AUTH_URI", "https://accounts.google.com/o/oauth2/auth")
SA_TOKEN_URI = os.getenv("SA_TOKEN_URI", "https://oauth2.googleapis.com/token")
SA_AUTH_PROVIDER_X509_CERT_URL = os.getenv("SA_AUTH_PROVIDER_X509_CERT_URL", "https://www.googleapis.com/oauth2/v1/certs")
SA_CLIENT_X509_CERT_URL = os.getenv("SA_CLIENT_X509_CERT_URL")

GOOGLE_AUTH_METHOD = None
GOOGLE_SERVICE_ACCOUNT_INFO = None

if SA_PROJECT_ID and SA_PRIVATE_KEY_ID and SA_PRIVATE_KEY and SA_CLIENT_EMAIL and SA_CLIENT_ID and SA_CLIENT_X509_CERT_URL:
    GOOGLE_SERVICE_ACCOUNT_INFO = {
        "type": SA_TYPE,
        "project_id": SA_PROJECT_ID,
        "private_key_id": SA_PRIVATE_KEY_ID,
        "private_key": SA_PRIVATE_KEY.replace('\\\\n', '\n') if SA_PRIVATE_KEY else None, # Adjusted escaping for \n
        "client_email": SA_CLIENT_EMAIL,
        "client_id": SA_CLIENT_ID,
        "auth_uri": SA_AUTH_URI,
        "token_uri": SA_TOKEN_URI,
        "auth_provider_x509_cert_url": SA_AUTH_PROVIDER_X509_CERT_URL,
        "client_x509_cert_url": SA_CLIENT_X509_CERT_URL
    }
    GOOGLE_AUTH_METHOD = "SERVICE_ACCOUNT_INDIVIDUAL_FIELDS"
else:
    print("ERROR CONFIG: Insufficient Google Service Account details in .env. Please set all SA_... variables.")

# --- Google Drive Specific Configuration ---
GDRIVE_TARGET_FOLDER_ID = os.getenv("GDRIVE_TARGET_FOLDER_ID", "...") 
GOOGLE_DRIVE_APP_DATA_FOLDER_NAME = os.getenv("GOOGLE_DRIVE_APP_DATA_FOLDER_NAME", "YTCookhouseAppData")
DB_JSON_FILENAME_ON_DRIVE = "app_database.json" 

# --- Gemini API Key ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "...")

# --- Environment-Aware Base Directory for Temporary Processing ---
# This is the root for raw_clips_temp, merged_videos_temp, metadata_temp
# These files are considered ephemeral locally and should be uploaded to GDrive for persistence.
if 'RENDER' in os.environ and os.environ.get('RENDER_INSTANCE_ID'):
    # Running on Render. Check if a persistent disk is mounted at /mnt/data
    render_disk_path = "/mnt/data/ytauto_processing_space"
    if os.path.exists("/mnt/data"):
        TEMP_PROCESSING_BASE_DIR = render_disk_path
        print(f"CONFIG - Running on Render with persistent disk. Temp Processing Base: {TEMP_PROCESSING_BASE_DIR}")
    else:
        # Fallback to ephemeral storage on Render if no persistent disk detected at /mnt/data
        TEMP_PROCESSING_BASE_DIR = os.path.join(APP_ROOT_DIR, "temp_processing_space")
        print(f"CONFIG - Running on Render (ephemeral storage). Temp Processing Base: {TEMP_PROCESSING_BASE_DIR}")
elif platform.system() == "Windows":
    # Local Windows environment
    TEMP_PROCESSING_BASE_DIR = os.path.join(APP_ROOT_DIR, "temp_processing_space")
    print(f"CONFIG - Running on Windows. Temp Processing Base: {TEMP_PROCESSING_BASE_DIR}")
else:
    # Other local environments (Linux, macOS)
    TEMP_PROCESSING_BASE_DIR = os.path.join(APP_ROOT_DIR, "temp_processing_space")
    print(f"CONFIG - Running on local {platform.system()}. Temp Processing Base: {TEMP_PROCESSING_BASE_DIR}")

# Define specific temporary directories relative to the dynamic base
RAW_DIR = os.path.join(TEMP_PROCESSING_BASE_DIR, "raw_clips_temp")
MERGED_DIR = os.path.join(TEMP_PROCESSING_BASE_DIR, "merged_videos_temp")
METADATA_TEMP_DIR = os.path.join(TEMP_PROCESSING_BASE_DIR, "metadata_temp")

# --- Static and Persistent Video Storage Directories (within the app structure) ---
# These are assumed to be part of your project repo or managed by where the app is run.
STATIC_DIR_CONFIG = os.path.join(APP_ROOT_DIR, "static")
STATIC_AUDIO_DIR = os.path.join(STATIC_DIR_CONFIG, "audio")

# Base directory for 'videos' folder which might be served or linked directly
# If these are also meant to be temporary before GDrive upload, they could use TEMP_PROCESSING_BASE_DIR.
# If they are for a different purpose (e.g., local final output not necessarily uploaded or if GDrive is just a backup),
# keeping them separate based on APP_ROOT_DIR is fine.
APP_VIDEOS_DIR = os.path.join(APP_ROOT_DIR, "videos")
APP_VIDEOS_RAW_DIR = os.path.join(APP_VIDEOS_DIR, "raw")             # Potentially GDrive download target if not using TEMP_PROCESSING_BASE_DIR for it
APP_VIDEOS_MERGED_DIR = os.path.join(APP_VIDEOS_DIR, "merged")       # Potentially local merged video storage
APP_VIDEOS_OUTPUT_DIR = os.path.join(APP_VIDEOS_DIR, "output")       # Potentially local metadata storage

DIRECTORIES_TO_CREATE = [
    TEMP_PROCESSING_BASE_DIR, RAW_DIR, MERGED_DIR, METADATA_TEMP_DIR,
    STATIC_DIR_CONFIG, STATIC_AUDIO_DIR,
    APP_VIDEOS_DIR, APP_VIDEOS_RAW_DIR, APP_VIDEOS_MERGED_DIR, APP_VIDEOS_OUTPUT_DIR
]

for dir_path in DIRECTORIES_TO_CREATE:
    os.makedirs(dir_path, exist_ok=True) # exist_ok=True makes it safe to call multiple times

# --- Verification Prints ---
print(f"CONFIG - APP_ROOT_DIR (location of this config file): {APP_ROOT_DIR}")
print(f"CONFIG - TEMP_PROCESSING_BASE_DIR (dynamic for temp files): {TEMP_PROCESSING_BASE_DIR}")
print(f"CONFIG - RAW_DIR (for downloaded raw clips): {RAW_DIR}")
print(f"CONFIG - MERGED_DIR (for temporary merged videos): {MERGED_DIR}")
print(f"CONFIG - METADATA_TEMP_DIR (for temporary metadata JSONs): {METADATA_TEMP_DIR}")
print(f"CONFIG - Selected Google Auth Method: {GOOGLE_AUTH_METHOD}")
print(f"CONFIG - GDrive Target Folder ID (Raw Clips Source): {GDRIVE_TARGET_FOLDER_ID}")
print(f"CONFIG - GDrive App Data Folder Name (for DB, Merged, Metadata): {GOOGLE_DRIVE_APP_DATA_FOLDER_NAME}")
print(f"CONFIG - Gemini API Key: {'SET' if GEMINI_API_KEY and GEMINI_API_KEY != '...' else 'NOT SET'}")

# --- Shared Service Clients & Startup Status (Added for Refactoring) ---
GDRIVE_SERVICE_CLIENT = None
YOUTUBE_SERVICE_CLIENT = None
GEMINI_SERVICE_CLIENT = None # Assuming you'll have one for Gemini too

APP_STARTUP_STATUS = {
    "gdrive_ready": False,
    "gdrive_error_details": None,
    "youtube_ready": False,
    "youtube_error_details": None,
    "gemini_ready": False, # Assuming Gemini check
    "gemini_error_details": None,
    "all_services_ready": False
}

# --- In-Memory DB Cache Configuration (Added for Refactoring) ---
CACHED_DB_CONTENT = None
DB_CACHE_TIMESTAMP = None 
DB_CACHE_DURATION_SECONDS = 1800 # Cache DB content for 30 minutes by default

# --- YouTube OAuth User Consent Configuration ---
# CLIENT_SECRET_YOUTUBE_PATH is no longer used directly for YouTube client secret.
# Configuration is expected via GOOGLE_CLIENT_SECRET_JSON_YOUTUBE environment variable.

# --- Path to store the user's OAuth token after they grant consent (Made environment-aware) ---
TOKEN_YOUTUBE_OAUTH_FILENAME = "token_youtube_oauth.json"
if 'RENDER' in os.environ and os.environ.get('RENDER_INSTANCE_ID'):
    # Ensure this path matches where your Render persistent disk is mounted and you have write permissions.
    persistent_storage_base = "/mnt/data" 
    APP_TOKENS_PERSIST_DIR = os.path.join(persistent_storage_base, "ytcookhouse_tokens") 
    os.makedirs(APP_TOKENS_PERSIST_DIR, exist_ok=True)
    TOKEN_YOUTUBE_OAUTH_PATH = os.path.join(APP_TOKENS_PERSIST_DIR, TOKEN_YOUTUBE_OAUTH_FILENAME)
    print(f"CONFIG - Running on Render. OAuth Token Path (Persistent): {TOKEN_YOUTUBE_OAUTH_PATH}")
else:
    # Local development path
    TOKEN_YOUTUBE_OAUTH_PATH = os.path.join(APP_ROOT_DIR, TOKEN_YOUTUBE_OAUTH_FILENAME)
    print(f"CONFIG - Running Locally. OAuth Token Path: {TOKEN_YOUTUBE_OAUTH_PATH}")
# Note: The old OAUTH_TOKEN_YOUTUBE_PATH in youtube_uploader.py might be for a different flow or can be removed/renamed.

# --- General Auth Method Selection ---
# GOOGLE_AUTH_METHOD still applies to GDrive and Gemini (Service Account)
# We can add a specific one for YouTube if needed, or let youtube_uploader.py decide based on file presence.
YOUTUBE_AUTH_METHOD = "OAUTH_USER_CONSENT" # Explicitly set this for clarity