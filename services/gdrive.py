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
import sys # Import sys for sys.exit()

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from utils import load_db, update_recipe_status
from config import (
    GDRIVE_TARGET_FOLDER_ID,
    GOOGLE_AUTH_METHOD,
    GOOGLE_SERVICE_ACCOUNT_INFO
    # GOOGLE_SERVICE_ACCOUNT_FILE_PATH, # No longer used
    # GOOGLE_CLIENT_SECRET_PATH_CONFIG # No longer used
)

# --- Google Drive API Setup ---
SCOPES = ['https://www.googleapis.com/auth/drive.readonly']
# OAUTH_TOKEN_GDRIVE_PATH is no longer used as OAuth is removed from this service
# OAUTH_TOKEN_GDRIVE_PATH = os.path.join(os.path.dirname(__file__), '..', 'token_gdrive.json')

class GDriveServiceError(Exception):
    """Custom exception for GDrive service errors."""
    pass

def get_gdrive_service():
    """Authenticates and returns a Google Drive API service client.
       Uses Service Account with individual fields from config.py.
    """
    creds = None

    if GOOGLE_AUTH_METHOD == "SERVICE_ACCOUNT_INDIVIDUAL_FIELDS" and GOOGLE_SERVICE_ACCOUNT_INFO:
        print(f"GDrive Auth: Attempting with Service Account (Individual Fields).")
        try:
            creds = ServiceAccountCredentials.from_service_account_info(
                GOOGLE_SERVICE_ACCOUNT_INFO, scopes=SCOPES
            )
            print(f"GDrive Auth: Successfully obtained credentials via Service Account (Individual Fields).")
        except Exception as e:
            print(f"ERROR: GDrive Auth: Failed to load Service Account from GOOGLE_SERVICE_ACCOUNT_INFO: {e}")
            raise GDriveServiceError(f"Service Account (Individual Fields) credential error: {e}")
    else:
        msg = "GDrive Auth: SERVICE_ACCOUNT_INDIVIDUAL_FIELDS method not configured or GOOGLE_SERVICE_ACCOUNT_INFO missing in config.py."
        print(f"ERROR: {msg}")
        raise GDriveServiceError(msg)

    if not creds: # Should be redundant if the above logic is sound, but as a safeguard.
        msg = f"GDrive Auth: Failed to obtain credentials."
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
    print(f"Listing folders from Google Drive parent ID: {GDRIVE_TARGET_FOLDER_ID}")
    if not GDRIVE_TARGET_FOLDER_ID or GDRIVE_TARGET_FOLDER_ID == "...":
        print("ERROR: GDRIVE_TARGET_FOLDER_ID is not configured in .env. Cannot list GDrive folders.")
        return []

    try:
        service = get_gdrive_service()
        query = f"'{GDRIVE_TARGET_FOLDER_ID}' in parents and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
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
    print("Testing GDrive Service Module (Service Account with Individual Fields method)...")
    print(f"Configured Google Auth Method from config.py: {GOOGLE_AUTH_METHOD}")
    if GOOGLE_AUTH_METHOD == "SERVICE_ACCOUNT_INDIVIDUAL_FIELDS":
        print("Attempting to use Service Account credentials constructed from individual .env variables.")
    else:
        print(f"Warning: Expected GOOGLE_AUTH_METHOD to be 'SERVICE_ACCOUNT_INDIVIDUAL_FIELDS', but got '{GOOGLE_AUTH_METHOD}'. Test may fail or use unexpected auth.")
    
    print(f"Target GDrive Folder ID: {GDRIVE_TARGET_FOLDER_ID}")
    APP_DATA_FOLDER_ID_TEST = None # To be set after creating/getting it
    try:
        # Test: Get or create app data folder
        app_data_folder_id_test = get_or_create_app_data_folder_id() # Assuming this might call get_gdrive_service if not passed
        if not app_data_folder_id_test:
            print("CRITICAL TEST ERROR: Could not get or create App Data Folder in GDrive. Aborting further GDrive tests.")
            sys.exit(1) # Use sys.exit(1) to indicate an error exit
        APP_DATA_FOLDER_ID_TEST = app_data_folder_id_test
        print(f"Test: App Data Folder ID: {APP_DATA_FOLDER_ID_TEST}")

        # Test: Initialize or load db.json from GDrive (simplified for test)
        # In a real scenario, utils.py would use these gdrive functions
        db_test_content = {"test_key": "test_value_initial"}
        db_gdrive_filename_test = "test_app_database.json"
        
        # Try to find it first
        existing_db_file_id = find_file_id_by_name(APP_DATA_FOLDER_ID_TEST, db_gdrive_filename_test)
        print(f"Test: Existing DB file ID on GDrive: {existing_db_file_id}")

        # Create a dummy local db.json to upload
        dummy_local_db_path = os.path.join(os.path.dirname(__file__), "..", "videos", "temp_test_db.json") # Use a temp location
        if not os.path.exists(os.path.dirname(dummy_local_db_path)):
             os.makedirs(os.path.dirname(dummy_local_db_path))
        with open(dummy_local_db_path, 'w') as f_db_test:
            json.dump(db_test_content, f_db_test)

        uploaded_db_file_id = upload_file_to_drive(
            local_file_path=dummy_local_db_path, 
            drive_folder_id=APP_DATA_FOLDER_ID_TEST, 
            drive_filename=db_gdrive_filename_test,
            mimetype='application/json',
            existing_file_id=existing_db_file_id
        )
        if uploaded_db_file_id:
            print(f"Test: Successfully uploaded/updated {db_gdrive_filename_test} to GDrive. File ID: {uploaded_db_file_id}")
            # Test download and verify content
            downloaded_db_content_str = get_file_content_from_drive(uploaded_db_file_id)
            if downloaded_db_content_str:
                downloaded_db_json = json.loads(downloaded_db_content_str)
                if downloaded_db_json.get("test_key") == "test_value_initial":
                    print("Test: DB content verification after download SUCCESSFUL.")
                else:
                    print("Test: DB content verification FAILED.")
            else:
                print("Test: Failed to download DB content for verification.")
        else:
            print(f"Test: Failed to upload/update {db_gdrive_filename_test} to GDrive.")
        
        if os.path.exists(dummy_local_db_path): os.remove(dummy_local_db_path)

        folders = list_folders_from_gdrive_and_db_status() # This will now use the GDrive based db.json indirectly via utils
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
            # For GDrive persistence, RAW_DIR is an ephemeral local temp staging area
            # The main app will ensure RAW_DIR exists locally.
            from config import RAW_DIR 
            local_temp_raw_dir_for_test = os.path.join(RAW_DIR, recipe_safe_name_test + "_gdriveSvcTest_tempLocal")
            if not os.path.exists(local_temp_raw_dir_for_test): os.makedirs(local_temp_raw_dir_for_test)

            print(f"Test download path (local ephemeral): {local_temp_raw_dir_for_test}")
            download_success = download_folder_contents(folder_to_download['id'], folder_to_download['name'], local_temp_raw_dir_for_test)
            print(f"Download test for '{folder_to_download['name']}' to local temp dir {'SUCCESSFUL' if download_success else 'FAILED'}.")
            
            # Test creating a recipe subfolder on GDrive for outputs
            if APP_DATA_FOLDER_ID_TEST:
                recipe_gdrive_output_folder_id = get_or_create_recipe_subfolder_id(APP_DATA_FOLDER_ID_TEST, folder_to_download['id'], "test_outputs")
                if recipe_gdrive_output_folder_id:
                    print(f"Test: Successfully created/got GDrive subfolder for recipe outputs: {recipe_gdrive_output_folder_id}")
                else:
                    print("Test: Failed to create/get GDrive subfolder for recipe outputs.")
            
            # Clean up local temp download dir for test
            if os.path.exists(local_temp_raw_dir_for_test): shutil.rmtree(local_temp_raw_dir_for_test)

        else:
            print("Test: No folder found to test download functionality (or no new folders)." )
        # The following 'else' was duplicated and caused a syntax error.
        # It's removed because the case of 'not folders' is handled by the 'else' above.
    except Exception as e:
        print(f"An UNEXPECTED error occurred during GDrive module test: {e}")
    print("\nGDrive Service Module testing finished.")
