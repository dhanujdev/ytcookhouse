import os
import json 
from dotenv import load_dotenv

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
load_dotenv(os.path.join(BASE_DIR, '.env'))

# --- Google API Credentials Configuration ---
# This configuration ONLY uses individual Service Account fields from environment variables.

SA_TYPE = os.getenv("SA_TYPE", "service_account")
SA_PROJECT_ID = os.getenv("SA_PROJECT_ID")
SA_PRIVATE_KEY_ID = os.getenv("SA_PRIVATE_KEY_ID")
SA_PRIVATE_KEY = os.getenv("SA_PRIVATE_KEY") # Must contain literal \n for newlines in .env
SA_CLIENT_EMAIL = os.getenv("SA_CLIENT_EMAIL")
SA_CLIENT_ID = os.getenv("SA_CLIENT_ID")
SA_AUTH_URI = os.getenv("SA_AUTH_URI", "https://accounts.google.com/o/oauth2/auth")
SA_TOKEN_URI = os.getenv("SA_TOKEN_URI", "https://oauth2.googleapis.com/token")
SA_AUTH_PROVIDER_X509_CERT_URL = os.getenv("SA_AUTH_PROVIDER_X509_CERT_URL", "https://www.googleapis.com/oauth2/v1/certs")
SA_CLIENT_X509_CERT_URL = os.getenv("SA_CLIENT_X509_CERT_URL")

GOOGLE_AUTH_METHOD = None
GOOGLE_SERVICE_ACCOUNT_INFO = None
# GOOGLE_SERVICE_ACCOUNT_FILE_PATH and GOOGLE_CLIENT_SECRET_PATH_CONFIG are no longer used.

if SA_PROJECT_ID and SA_PRIVATE_KEY_ID and SA_PRIVATE_KEY and SA_CLIENT_EMAIL and SA_CLIENT_ID and SA_CLIENT_X509_CERT_URL:
    GOOGLE_SERVICE_ACCOUNT_INFO = {
        "type": SA_TYPE,
        "project_id": SA_PROJECT_ID,
        "private_key_id": SA_PRIVATE_KEY_ID,
        "private_key": SA_PRIVATE_KEY.replace('\\n', '\n') if SA_PRIVATE_KEY else None,
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
    # You might want to raise an exception here or handle this more gracefully depending on your app's needs.
    # For example: raise ValueError("Missing one or more SA_... environment variables for Google authentication.")

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

# --- Persistent Video Storage Directories (referenced by main.py for StaticFiles) ---
# These are gitignored locally but need to exist on the server if app expects them.
APP_VIDEOS_DIR = os.path.join(BASE_DIR, "videos")
APP_VIDEOS_RAW_DIR = os.path.join(APP_VIDEOS_DIR, "raw")
APP_VIDEOS_MERGED_DIR = os.path.join(APP_VIDEOS_DIR, "merged")
APP_VIDEOS_OUTPUT_DIR = os.path.join(APP_VIDEOS_DIR, "output")

# Ensure all necessary directories exist (both temp and main video structure)
# Note: main.py also creates APP_VIDEOS_DIR, this is just to be sure and for subdirs.
# Order matters if creating nested dirs, ensure parent is created first.
DIRECTORIES_TO_CREATE = [
    TEMP_PROCESSING_DIR, RAW_DIR, MERGED_DIR, OUTPUT_DIR, # Temporary processing space
    STATIC_DIR_CONFIG, STATIC_AUDIO_DIR, # Static assets
    APP_VIDEOS_DIR, APP_VIDEOS_RAW_DIR, APP_VIDEOS_MERGED_DIR, APP_VIDEOS_OUTPUT_DIR # Main video storage structure
]

for dir_path in DIRECTORIES_TO_CREATE:
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

