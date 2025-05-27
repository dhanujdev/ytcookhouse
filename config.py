import os
import json 
from dotenv import load_dotenv

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
load_dotenv(os.path.join(BASE_DIR, '.env'))

# --- Google API Credentials Configuration ---
GOOGLE_SERVICE_ACCOUNT_CREDENTIALS_JSON_AS_STRING = os.getenv("GOOGLE_SERVICE_ACCOUNT_CREDENTIALS_JSON_CONTENT")
GOOGLE_SERVICE_ACCOUNT_KEY_JSON_PATH_ENV = os.getenv("GOOGLE_SERVICE_ACCOUNT_KEY_JSON_PATH")
GOOGLE_CLIENT_SECRET_FILENAME_ENV = os.getenv("GOOGLE_CLIENT_SECRET_JSON_FILENAME")
GOOGLE_CLIENT_SECRET_PATH_CONFIG = os.path.join(BASE_DIR, GOOGLE_CLIENT_SECRET_FILENAME_ENV) if GOOGLE_CLIENT_SECRET_FILENAME_ENV else None

GOOGLE_AUTH_METHOD = None
GOOGLE_SERVICE_ACCOUNT_INFO = None
GOOGLE_SERVICE_ACCOUNT_FILE_PATH = None

if GOOGLE_SERVICE_ACCOUNT_CREDENTIALS_JSON_AS_STRING:
    try:
        GOOGLE_SERVICE_ACCOUNT_INFO = json.loads(GOOGLE_SERVICE_ACCOUNT_CREDENTIALS_JSON_AS_STRING)
        GOOGLE_AUTH_METHOD = "SERVICE_ACCOUNT_JSON_STRING"
    except json.JSONDecodeError as e:
        print(f"ERROR CONFIG: Could not parse GOOGLE_SERVICE_ACCOUNT_CREDENTIALS_JSON_CONTENT: {e}.")
elif GOOGLE_SERVICE_ACCOUNT_KEY_JSON_PATH_ENV and os.path.exists(GOOGLE_SERVICE_ACCOUNT_KEY_JSON_PATH_ENV):
    GOOGLE_SERVICE_ACCOUNT_FILE_PATH = GOOGLE_SERVICE_ACCOUNT_KEY_JSON_PATH_ENV
    GOOGLE_AUTH_METHOD = "SERVICE_ACCOUNT_FILE_PATH"
elif GOOGLE_CLIENT_SECRET_PATH_CONFIG and os.path.exists(GOOGLE_CLIENT_SECRET_PATH_CONFIG):
    GOOGLE_AUTH_METHOD = "OAUTH_CLIENT_SECRET"
else:
    print("ERROR CONFIG: No valid Google API credentials found.")

# --- Google Drive Specific Configuration ---
GDRIVE_TARGET_FOLDER_ID = os.getenv("GDRIVE_TARGET_FOLDER_ID", "...") # Source folder for raw recipe clips
GOOGLE_DRIVE_APP_DATA_FOLDER_NAME = os.getenv("GOOGLE_DRIVE_APP_DATA_FOLDER_NAME", "YTCookhouseAppData") # App's root data folder on GDrive
DB_JSON_FILENAME_ON_DRIVE = "app_database.json" # Name of the DB file on GDrive

# --- Gemini API Key ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "...")

# --- Local Ephemeral Directories for Temporary Processing on Render ---
# These are temporary working directories on Render's filesystem.
# Data here will NOT persist across deploys/restarts if not backed by a persistent disk (which we are avoiding for cost).
# Google Drive will be the source of truth for persistent data.

TEMP_PROCESSING_DIR = os.path.join(BASE_DIR, "temp_processing_space")
RAW_DIR = os.path.join(TEMP_PROCESSING_DIR, "raw_clips_temp")       # For raw clips downloaded from GDrive
MERGED_DIR = os.path.join(TEMP_PROCESSING_DIR, "merged_videos_temp") # For merged videos before GDrive upload
OUTPUT_DIR = os.path.join(TEMP_PROCESSING_DIR, "metadata_temp")      # For metadata JSONs before GDrive upload

STATIC_DIR_CONFIG = os.path.join(BASE_DIR, "static")
STATIC_AUDIO_DIR = os.path.join(STATIC_DIR_CONFIG, "audio")

# Ensure these local temporary directories exist
for dir_path in [TEMP_PROCESSING_DIR, RAW_DIR, MERGED_DIR, OUTPUT_DIR, STATIC_DIR_CONFIG, STATIC_AUDIO_DIR]:
    if not os.path.exists(dir_path):
        try:
            os.makedirs(dir_path)
            print(f"CONFIG: Created local temporary directory: {dir_path}")
        except OSError as e:
            print(f"CONFIG: ERROR creating local temporary directory {dir_path}: {e}")

# DB_FILE_PATH is no longer defined here as db.json is managed via Google Drive by utils.py

# --- Verification Prints ---
print(f"CONFIG - Base Directory (app root): {BASE_DIR}")
print(f"CONFIG - Local Temp Processing Dir: {TEMP_PROCESSING_DIR}")
print(f"CONFIG - Selected Google Auth Method: {GOOGLE_AUTH_METHOD}")
print(f"CONFIG - GDrive Target Folder ID (Raw Clips Source): {GDRIVE_TARGET_FOLDER_ID}")
print(f"CONFIG - GDrive App Data Folder Name (for DB, Merged, Metadata): {GOOGLE_DRIVE_APP_DATA_FOLDER_NAME}")
print(f"CONFIG - Gemini API Key: {'SET' if GEMINI_API_KEY and GEMINI_API_KEY != '...' else 'NOT SET'}")

