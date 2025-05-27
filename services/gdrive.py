import os
import json 
import sys
# Removed pickle as we'll use JSON for token file for consistency with service account if needed by OAuth
from google.oauth2.service_account import Credentials as ServiceAccountCredentials # For Service Account
from google.oauth2.credentials import Credentials as UserCredentials # For OAuth2 user tokens
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload
import io

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from utils import load_db, update_recipe_status
from config import (
    GDRIVE_FOLDER_ID,
    GOOGLE_AUTH_METHOD, 
    GOOGLE_SERVICE_ACCOUNT_INFO, 
    GOOGLE_SERVICE_ACCOUNT_FILE_PATH,
    GOOGLE_CLIENT_SECRET_PATH_CONFIG # Renamed in config.py for clarity
)

# --- Google Drive API Setup ---
SCOPES = ['https://www.googleapis.com/auth/drive.readonly']
# Token file for OAuth2 flow (user consent)
OAUTH_TOKEN_GDRIVE_PATH = os.path.join(os.path.dirname(__file__), '..', 'token_gdrive.json')

class GDriveServiceError(Exception):
    """Custom exception for GDrive service errors."""
    pass

def get_gdrive_service():
    """Authenticates and returns a Google Drive API service client.
       Prioritizes Service Account, then OAuth2 InstalledAppFlow.
    """
    creds = None
    auth_method_to_log = "Unknown"

    if GOOGLE_AUTH_METHOD == "SERVICE_ACCOUNT_JSON_STRING" and GOOGLE_SERVICE_ACCOUNT_INFO:
        auth_method_to_log = "Service Account (JSON string)"
        print(f"GDrive Auth: Attempting with {auth_method_to_log}")
        try:
            creds = ServiceAccountCredentials.from_service_account_info(
                GOOGLE_SERVICE_ACCOUNT_INFO, scopes=SCOPES
            )
            print(f"GDrive Auth: Successfully obtained credentials via {auth_method_to_log}.")
        except Exception as e:
            print(f"ERROR: GDrive Auth: Failed to load Service Account from JSON string: {e}")
            raise GDriveServiceError(f"Service Account (JSON string) credential error: {e}")

    elif GOOGLE_AUTH_METHOD == "SERVICE_ACCOUNT_FILE_PATH" and GOOGLE_SERVICE_ACCOUNT_FILE_PATH:
        auth_method_to_log = f"Service Account (file path: {GOOGLE_SERVICE_ACCOUNT_FILE_PATH})"
        print(f"GDrive Auth: Attempting with {auth_method_to_log}")
        try:
            creds = ServiceAccountCredentials.from_service_account_file(
                GOOGLE_SERVICE_ACCOUNT_FILE_PATH, scopes=SCOPES
            )
            print(f"GDrive Auth: Successfully obtained credentials via {auth_method_to_log}.")
        except Exception as e:
            print(f"ERROR: GDrive Auth: Failed to load Service Account from file: {e}")
            raise GDriveServiceError(f"Service Account (file path) credential error: {e}")

    elif GOOGLE_AUTH_METHOD == "OAUTH_CLIENT_SECRET" and GOOGLE_CLIENT_SECRET_PATH_CONFIG:
        auth_method_to_log = f"OAuth 2.0 Client Secret (file: {GOOGLE_CLIENT_SECRET_PATH_CONFIG})"
        print(f"GDrive Auth: Attempting with {auth_method_to_log} (user consent flow if no token).")
        if os.path.exists(OAUTH_TOKEN_GDRIVE_PATH):
            try:
                with open(OAUTH_TOKEN_GDRIVE_PATH, 'r') as token_file:
                    creds = UserCredentials.from_authorized_user_info(json.load(token_file), SCOPES)
                print(f"GDrive Auth: Loaded OAuth token from {OAUTH_TOKEN_GDRIVE_PATH}")
            except Exception as e:
                print(f"Warning: GDrive Auth: Error loading OAuth token from {OAUTH_TOKEN_GDRIVE_PATH}: {e}. Will attempt re-auth.")
                creds = None
        
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                try:
                    creds.refresh(Request())
                    print("GDrive Auth: OAuth token refreshed successfully.")
                except Exception as e:
                    print(f"Warning: GDrive Auth: Failed to refresh OAuth token: {e}. Re-authentication needed.")
                    creds = None 
            else:
                try:
                    flow = InstalledAppFlow.from_client_secrets_file(GOOGLE_CLIENT_SECRET_PATH_CONFIG, SCOPES)
                    print("GDrive Auth: OAuth authentication required. Please follow browser prompts.")
                    # Note: run_local_server might not be suitable for all server environments.
                    # For Render, pre-authorized token (if OAuth is used) or SA is better.
                    creds = flow.run_local_server(port=0)
                except Exception as e:
                    print(f"ERROR: GDrive Auth: Failed OAuth flow: {e}")
                    raise GDriveServiceError(f"OAuth flow error: {e}")
            
            if creds:
                try:
                    with open(OAUTH_TOKEN_GDRIVE_PATH, 'w') as token_file:
                        token_file.write(creds.to_json()) # Save user credentials as JSON
                    print(f"GDrive Auth: OAuth token saved to {OAUTH_TOKEN_GDRIVE_PATH}")
                except Exception as e:
                    print(f"Warning: GDrive Auth: Error saving OAuth token to {OAUTH_TOKEN_GDRIVE_PATH}: {e}")
    else:
        msg = "GDrive Auth: No valid Google API credential method configured in config.py or .env."
        print(f"ERROR: {msg}")
        raise GDriveServiceError(msg)

    if not creds:
        msg = f"GDrive Auth: Failed to obtain credentials using method: {auth_method_to_log}."
        print(f"ERROR: {msg}")
        raise GDriveServiceError(msg)

    try:
        service = build('drive', 'v3', credentials=creds)
        print("Google Drive service client created successfully.")
        return service
    except Exception as e:
        msg = f"Failed to build GDrive service client: {e}"
        print(f"ERROR: {msg}")
        raise GDriveServiceError(msg)

# The rest of the file (list_folders_from_gdrive_and_db_status, download_folder_contents, __main__ test block)
# remains largely the same as it calls get_gdrive_service() and doesn't need to change its own logic.
# Ensure the __main__ block doesn't try to re-define config paths if it uses them directly.


def list_folders_from_gdrive_and_db_status():
    """
    Fetches recipe folders (subfolders) from the configured Google Drive parent folder 
    and enriches them with status from db.json.
    """
    print(f"Listing folders from Google Drive parent ID: {GDRIVE_FOLDER_ID}")
    if not GDRIVE_FOLDER_ID or GDRIVE_FOLDER_ID == "...":
        print("ERROR: GDRIVE_TARGET_FOLDER_ID is not configured in .env. Cannot list GDrive folders.")
        return []

    try:
        service = get_gdrive_service()
        query = f"'{GDRIVE_FOLDER_ID}' in parents and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
        results = service.files().list(q=query, pageSize=100, fields="nextPageToken, files(id, name)").execute()
        gdrive_folders = results.get('files', [])
    except Exception as e:
        print(f"An error occurred while listing GDrive folders: {e}")
        return []

    if not gdrive_folders:
        print("No recipe subfolders found in the specified GDrive folder or access issue.")
        return []
    print(f"Found {len(gdrive_folders)} potential recipe folders in GDrive.")

    db_recipes = load_db().get("recipes", {})
    enriched_folders = []
    for folder in gdrive_folders:
        folder_id = folder["id"]
        folder_name_from_gdrive = folder["name"]
        db_entry = db_recipes.get(folder_id)
        status_display = "New"
        display_name_with_status = folder_name_from_gdrive
        youtube_url_from_db = None
        if db_entry:
            status_from_db = db_entry.get("status", "Unknown")
            status_display = status_from_db.replace("_", " ").title()
            display_name_with_status = f"{folder_name_from_gdrive} (Status: {status_display})"
            if status_from_db == "uploaded":
                youtube_url_from_db = db_entry.get("youtube_url")
                display_name_with_status = f"{folder_name_from_gdrive} (✅ Uploaded)"
            elif status_from_db == "failed" or "failed" in status_from_db.lower(): # more robust check for failed status
                 display_name_with_status = f"{folder_name_from_gdrive} (❌ Failed: {db_entry.get('error_message','N/A')[:30]}...)"
        enriched_folders.append({
            "id": folder_id, "name": folder_name_from_gdrive,
            "display_name": display_name_with_status, "status_from_db": status_display,
            "youtube_url": youtube_url_from_db
        })
    print(f"Enriched folders with DB status: {json.dumps(enriched_folders, indent=2)}")
    return enriched_folders

def download_folder_contents(folder_id: str, recipe_name: str, download_base_path: str) -> bool:
    print(f"Attempting to download video clips for folder ID {folder_id} ({recipe_name}) to {download_base_path}")
    if not os.path.exists(download_base_path): os.makedirs(download_base_path)
    try:
        service = get_gdrive_service()
        video_mime_types = "(" + " or ".join([f"mimeType='{m}'" for m in ['video/mp4', 'video/mpeg', 'video/quicktime', 'video/x-msvideo', 'video/x-matroska']]) + ")"
        query = f"'{folder_id}' in parents and {video_mime_types} and trashed = false"
        results = service.files().list(q=query, pageSize=50, fields="files(id, name)").execute()
        items = results.get('files', [])
        if not items:
            msg = f"No video files found in GDrive folder ID {folder_id} ({recipe_name})."
            update_recipe_status(recipe_id=folder_id, name=recipe_name, status="download_failed", error_message=msg); return False
        print(f"Found {len(items)} video files in GDrive folder {folder_id}. Starting download...")
        for item in items:
            file_id, file_name = item['id'], item['name']
            file_path = os.path.join(download_base_path, file_name)
            print(f"Downloading GDrive file: {file_name} to {file_path}...")
            request = service.files().get_media(fileId=file_id)
            fh = io.BytesIO()
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while not done:
                status, done = downloader.next_chunk()
                if status: print(f"Download {file_name}: {int(status.progress() * 100)}%.")
            with open(file_path, 'wb') as f: fh.seek(0); f.write(fh.read())
            print(f"Successfully downloaded {file_name}")
        update_recipe_status(recipe_id=folder_id, name=recipe_name, status="downloaded", raw_clips_path=download_base_path); return True
    except Exception as e:
        msg = f"Error during GDrive download for {recipe_name}: {e}"
        update_recipe_status(recipe_id=folder_id, name=recipe_name, status="download_failed", error_message=msg); return False

if __name__ == '__main__':
    print("Testing GDrive Service Module (with potential Service Account or OAuth)...")
    print(f"Configured Google Auth Method: {GOOGLE_AUTH_METHOD}")
    if GOOGLE_AUTH_METHOD == "SERVICE_ACCOUNT_JSON_STRING":
        print("Service Account Key (content from .env): Parsed in config.")
    elif GOOGLE_AUTH_METHOD == "SERVICE_ACCOUNT_FILE_PATH":
        print(f"Service Account Key File Path: {GOOGLE_SERVICE_ACCOUNT_FILE_PATH}")
    elif GOOGLE_AUTH_METHOD == "OAUTH_CLIENT_SECRET":
        print(f"OAuth Client Secret Path: {GOOGLE_CLIENT_SECRET_PATH_CONFIG}")
        print(f"OAuth User Token Path: {OAUTH_TOKEN_GDRIVE_PATH}")
    else:
        print("No Google Auth method properly configured in .env / config.py. Test may fail.")
    
    print(f"Target GDrive Folder ID: {GDRIVE_FOLDER_ID}")
    try:
        folders = list_folders_from_gdrive_and_db_status()
        if folders:
            print(f"Successfully listed {len(folders)} folders.")
            # Simplified download test: try to download the first 'New' folder if any
            folder_to_download = next((f for f in folders if f['status_from_db'] == 'New'), None)
            if not folder_to_download and folders: folder_to_download = folders[0] # Fallback to first if no 'New'

            if folder_to_download:
                print(f"\nAttempting to download: '{folder_to_download['name']}' (ID: {folder_to_download['id']})")
                from config import RAW_DIR # For test download path consistency
                # Ensure RAW_DIR exists (config.py should do this, but good for standalone test)
                if not os.path.exists(RAW_DIR): os.makedirs(RAW_DIR)
                
                recipe_safe_name_test = "".join(c if c.isalnum() else "_" for c in folder_to_download['name'])
                # Create a unique subfolder for this test run to avoid conflicts if RAW_DIR is used by main app
                # However, for simplicity and to align with how main app might work, use the direct safe name path
                # This might overwrite if test is run multiple times on same folder without clearing db.json & videos/raw
                specific_test_download_path = os.path.join(RAW_DIR, recipe_safe_name_test + "_gdriveSvcTest")
                if not os.path.exists(specific_test_download_path): os.makedirs(specific_test_download_path)

                print(f"Test download path: {specific_test_download_path}")
                download_success = download_folder_contents(folder_to_download['id'], folder_to_download['name'], specific_test_download_path)
                print(f"Download test for '{folder_to_download['name']}' {'SUCCESSFUL' if download_success else 'FAILED'}.")
            else:
                print("No folder found to test download functionality (or no new folders)." )
        else:
            print("No folders listed. Check GDrive Folder ID, permissions, or API errors.")
    except Exception as e:
        print(f"An UNEXPECTED error occurred during GDrive module test: {e}")
    print("\nGDrive Service Module testing finished.")
