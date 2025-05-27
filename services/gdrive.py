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
from googleapiclient.http import MediaIoBaseDownload, MediaFileUpload # Added MediaFileUpload
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
# Changed to read/write scope for creating folders and uploading files
SCOPES = ['https://www.googleapis.com/auth/drive'] 
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

# --- GDrive Helper Functions for App Data Management ---

def find_file_id_by_name(parent_folder_id: str, filename: str, service=None):
    """Finds a file by name within a specific parent folder."""
    if not service:
        service = get_gdrive_service()
    try:
        query = f"name = '{filename}' and '{parent_folder_id}' in parents and trashed = false"
        response = service.files().list(q=query, spaces='drive', fields='files(id, name)').execute()
        for file in response.get('files', []):
            return file.get('id')
    except HttpError as error:
        print(f"An error occurred while trying to find file '{filename}': {error}")
    return None

def get_or_create_app_data_folder_id(service=None):
    """Gets or creates the main application data folder in GDrive.
       Uses GOOGLE_DRIVE_APP_DATA_FOLDER_NAME from config.
    """
    if not service:
        service = get_gdrive_service()
    
    from config import GOOGLE_DRIVE_APP_DATA_FOLDER_NAME # Ensure it's imported here for direct use
    if not GOOGLE_DRIVE_APP_DATA_FOLDER_NAME:
        raise GDriveServiceError("GOOGLE_DRIVE_APP_DATA_FOLDER_NAME is not set in config.")

    # Check if folder already exists (look in root of My Drive)
    query = f"name='{GOOGLE_DRIVE_APP_DATA_FOLDER_NAME}' and mimeType='application/vnd.google-apps.folder' and 'root' in parents and trashed=false"
    try:
        response = service.files().list(q=query, spaces='drive', fields='files(id)').execute()
        folders = response.get('files', [])
        if folders:
            folder_id = folders[0].get('id')
            print(f"GDrive: Found existing App Data folder '{GOOGLE_DRIVE_APP_DATA_FOLDER_NAME}' with ID: {folder_id}")
            return folder_id
        else:
            # Create the folder in the root of My Drive
            print(f"GDrive: App Data folder '{GOOGLE_DRIVE_APP_DATA_FOLDER_NAME}' not found. Creating...")
            file_metadata = {
                'name': GOOGLE_DRIVE_APP_DATA_FOLDER_NAME,
                'mimeType': 'application/vnd.google-apps.folder'
            }
            folder = service.files().create(body=file_metadata, fields='id').execute()
            folder_id = folder.get('id')
            print(f"GDrive: Created App Data folder '{GOOGLE_DRIVE_APP_DATA_FOLDER_NAME}' with ID: {folder_id}")
            return folder_id
    except HttpError as error:
        print(f"GDrive: An error occurred during get/create app data folder: {error}")
        raise GDriveServiceError(f"Failed to get/create App Data folder '{GOOGLE_DRIVE_APP_DATA_FOLDER_NAME}': {error}")
    except Exception as e:
        print(f"GDrive: An unexpected error in get_or_create_app_data_folder_id: {e}")
        raise GDriveServiceError(f"Unexpected error in get_or_create_app_data_folder_id for '{GOOGLE_DRIVE_APP_DATA_FOLDER_NAME}': {e}")


def upload_file_to_drive(local_file_path: str, drive_folder_id: str, drive_filename: str, mimetype: str = 'application/octet-stream', existing_file_id: str = None, service=None):
    """Uploads a local file to a specified Google Drive folder.
       Updates the file if existing_file_id is provided.
    """
    if not service:
        service = get_gdrive_service()
    try:
        file_metadata = {'name': drive_filename}
        if not existing_file_id: # Only add parents if creating new file
             file_metadata['parents'] = [drive_folder_id]

        media = MediaFileUpload(local_file_path, mimetype=mimetype, resumable=True)
        
        if existing_file_id:
            print(f"GDrive: Updating existing file ID {existing_file_id} with {local_file_path} as {drive_filename}")
            request = service.files().update(fileId=existing_file_id, body=file_metadata, media_body=media, fields='id')
        else:
            print(f"GDrive: Uploading new file {local_file_path} to folder {drive_folder_id} as {drive_filename}")
            request = service.files().create(body=file_metadata, media_body=media, fields='id')
        
        file = request.execute()
        uploaded_file_id = file.get('id')
        print(f"GDrive: File '{drive_filename}' uploaded successfully. File ID: {uploaded_file_id}")
        return uploaded_file_id
    except HttpError as error:
        print(f"GDrive: An error occurred during file upload: {error}")
        # Consider specific error handling, e.g., for 404 if folder_id is wrong
        raise GDriveServiceError(f"Failed to upload file '{drive_filename}': {error}")
    except Exception as e:
        print(f"GDrive: An unexpected error occurred during file upload: {e}")
        raise GDriveServiceError(f"Unexpected error uploading file '{drive_filename}': {e}")

def get_file_content_from_drive(file_id: str, service=None) -> str | None:
    """Downloads a file's content from Google Drive as a string."""
    if not service:
        service = get_gdrive_service()
    try:
        request = service.files().get_media(fileId=file_id)
        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            status, done = downloader.next_chunk()
            # print(f"GDrive Download: {int(status.progress() * 100)}%.") # Optional progress
        fh.seek(0)
        return fh.read().decode('utf-8') # Assuming text content like JSON
    except HttpError as error:
        if error.resp.status == 404:
            print(f"GDrive: File with ID {file_id} not found for download.")
            return None # File not found is a valid case for db loading
        print(f"GDrive: An HttpError occurred downloading file ID {file_id}: {error}")
        raise GDriveServiceError(f"Failed to download file content for ID '{file_id}': {error}")
    except Exception as e:
        print(f"GDrive: An unexpected error occurred downloading file ID {file_id}: {e}")
        raise GDriveServiceError(f"Unexpected error downloading file content for ID '{file_id}': {e}")

def get_or_create_recipe_subfolder_id(app_data_folder_id: str, recipe_id: str, subfolder_name: str, service=None):
    """Gets or creates a specific subfolder (e.g., for merged videos, metadata) for a recipe
       within the main app_data_folder_id, using recipe_id as part of the folder name
       to ensure uniqueness if multiple recipes share a name.
       Example subfolder_name could be 'merged_videos', 'metadata_files'.
       The actual folder created will be like 'recipe_id_subfolder_name'.
    """
    if not service:
        service = get_gdrive_service()

    # Sanitize recipe_id and subfolder_name to be safe for folder names if necessary,
    # though GDrive IDs are usually safe. Using a combination for clarity.
    gd_subfolder_name = f"{recipe_id}_{subfolder_name}"

    query = f"name='{gd_subfolder_name}' and mimeType='application/vnd.google-apps.folder' and '{app_data_folder_id}' in parents and trashed=false"
    try:
        response = service.files().list(q=query, spaces='drive', fields='files(id)').execute()
        folders = response.get('files', [])
        if folders:
            return folders[0].get('id')
        else:
            file_metadata = {
                'name': gd_subfolder_name,
                'mimeType': 'application/vnd.google-apps.folder',
                'parents': [app_data_folder_id]
            }
            folder = service.files().create(body=file_metadata, fields='id').execute()
            return folder.get('id')
    except HttpError as error:
        print(f"GDrive: An error occurred during get/create recipe subfolder '{gd_subfolder_name}': {error}")
        raise GDriveServiceError(f"Failed to get/create recipe subfolder '{gd_subfolder_name}': {error}")


def download_file_from_drive(file_id: str, local_download_path: str, service=None) -> bool:
    """Downloads a file from GDrive to the specified local_download_path."""
    if not service:
        service = get_gdrive_service()
    try:
        request = service.files().get_media(fileId=file_id)
        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        print(f"GDrive: Starting download of file ID {file_id} to {local_download_path}...")
        while not done:
            status, done = downloader.next_chunk()
            if status:
                print(f"GDrive Download progress: {int(status.progress() * 100)}%.")
        
        # Ensure directory for local_download_path exists
        local_dir = os.path.dirname(local_download_path)
        if not os.path.exists(local_dir):
            os.makedirs(local_dir)
            
        with open(local_download_path, 'wb') as f:
            fh.seek(0)
            f.write(fh.read())
        print(f"GDrive: Successfully downloaded file ID {file_id} to {local_download_path}")
        return True
    except HttpError as error:
        print(f"GDrive: An HttpError occurred downloading file ID {file_id} to {local_download_path}: {error}")
        # Optionally, clean up partially downloaded file if any
        if os.path.exists(local_download_path):
            # os.remove(local_download_path) # Or handle differently
            pass
        raise GDriveServiceError(f"Failed to download file from drive (ID: {file_id}): {error}")
    except Exception as e:
        print(f"GDrive: An unexpected error occurred downloading file ID {file_id} to {local_download_path}: {e}")
        raise GDriveServiceError(f"Unexpected error downloading file from drive (ID: {file_id}): {e}")

# --- Main GDrive Service Functions ---

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
        if folder_name_from_gdrive == ".ipynb_checkpoints":
            print(f"Skipping folder: {folder_name_from_gdrive}")
            continue # Skip this iteration
        db_entry = db_recipes.get(folder_id)
        status_from_db_value = "New" # Default for truly new folders / if no db_entry
        display_name_with_status = folder_name_from_gdrive
        youtube_url_from_db = None

        if db_entry:
            status_from_db_value = db_entry.get("status", "Unknown")
            # Ensure status_from_db_value is a string before calling .upper() or other string methods
            if not isinstance(status_from_db_value, str):
                status_from_db_value = str(status_from_db_value) # Convert if not string, e.g. if None or other type

        status_display_text = status_from_db_value.replace("_", " ").title()
        
        # Update display_name_with_status based on the (potentially hacked) status_from_db_value
        if status_from_db_value == "New" or status_from_db_value == "Unknown":
            display_name_with_status = folder_name_from_gdrive
        elif status_from_db_value.upper() == "UPLOADED_TO_YOUTUBE" or status_from_db_value == "uploaded": # Accommodate both case from potential manual edits or old data
            youtube_url_from_db = db_entry.get("youtube_url") if db_entry else None
            display_name_with_status = f"{folder_name_from_gdrive} (✅ Uploaded)"
        elif "FAILED" in status_from_db_value.upper():
            error_msg_snippet = db_entry.get('error_message','N/A')[:30] if db_entry else 'N/A'
            display_name_with_status = f"{folder_name_from_gdrive} (❌ Failed: {error_msg_snippet}...)"
        else:
            display_name_with_status = f"{folder_name_from_gdrive} (Status: {status_display_text})"
            
        enriched_folders.append({
            "id": folder_id, "name": folder_name_from_gdrive,
            "display_name": display_name_with_status, 
            "status_from_db": status_from_db_value, # Use the potentially modified status for logic
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
    print("Testing GDrive Service Module (Service Account with Individual Fields method)...") # __main__ block
    print(f"Configured Google Auth Method from config.py: {GOOGLE_AUTH_METHOD}")
    # The following test code is illustrative and may need GOOGLE_DRIVE_APP_DATA_FOLDER_NAME
    # and DB_JSON_FILENAME_ON_DRIVE from config to be imported if used directly here.
    # It's better if such test code uses the functions it's testing.
    from config import GOOGLE_DRIVE_APP_DATA_FOLDER_NAME, DB_JSON_FILENAME_ON_DRIVE # For test
    
    if GOOGLE_AUTH_METHOD == "SERVICE_ACCOUNT_INDIVIDUAL_FIELDS":
        print("Attempting to use Service Account credentials constructed from individual .env variables.")
    else:
        print(f"Warning: Expected GOOGLE_AUTH_METHOD to be 'SERVICE_ACCOUNT_INDIVIDUAL_FIELDS', but got '{GOOGLE_AUTH_METHOD}'. Test may fail or use unexpected auth.")
    
    print(f"Target GDrive Folder ID: {GDRIVE_TARGET_FOLDER_ID}")
    APP_DATA_FOLDER_ID_TEST = None # To be set after creating/getting it
    try:
        # Test: Get or create app data folder
        # These test functions (get_or_create_app_data_folder_id etc.) are now defined globally in the module
        test_service = get_gdrive_service() # Get service once for tests
        app_data_folder_id_test = get_or_create_app_data_folder_id(service=test_service)
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
        existing_db_file_id = find_file_id_by_name(app_data_folder_id_test, DB_JSON_FILENAME_ON_DRIVE, service=test_service)
        print(f"Test: Existing DB file ID on GDrive for '{DB_JSON_FILENAME_ON_DRIVE}': {existing_db_file_id}")

        # Create a dummy local db.json to upload
        # Ensure BASE_DIR is accessible or use relative paths carefully for tests
        from config import BASE_DIR # For test path construction
        dummy_local_db_path = os.path.join(BASE_DIR, "temp_test_db.json")
        if not os.path.exists(os.path.dirname(dummy_local_db_path)):
             os.makedirs(os.path.dirname(dummy_local_db_path))
        with open(dummy_local_db_path, 'w') as f_db_test:
            json.dump(db_test_content, f_db_test)

        uploaded_db_file_id = upload_file_to_drive(
            local_file_path=dummy_local_db_path,
            drive_folder_id=app_data_folder_id_test,
            drive_filename=DB_JSON_FILENAME_ON_DRIVE, # Use constant from config
            mimetype='application/json',
            existing_file_id=existing_db_file_id,
            service=test_service
        )
        if uploaded_db_file_id:
            print(f"Test: Successfully uploaded/updated {DB_JSON_FILENAME_ON_DRIVE} to GDrive. File ID: {uploaded_db_file_id}")
            # Test download and verify content
            downloaded_db_content_str = get_file_content_from_drive(uploaded_db_file_id, service=test_service)
            if downloaded_db_content_str:
                downloaded_db_json = json.loads(downloaded_db_content_str)
                if downloaded_db_json.get("test_key") == "test_value_initial":
                    print("Test: DB content verification after download SUCCESSFUL.")
                else:
                    print("Test: DB content verification FAILED.")
            else:
                print("Test: Failed to download DB content for verification.")
        else:
            print(f"Test: Failed to upload/update {DB_JSON_FILENAME_ON_DRIVE} to GDrive.")
        
        if os.path.exists(dummy_local_db_path): os.remove(dummy_local_db_path)

        folders = list_folders_from_gdrive_and_db_status() # This uses the main get_gdrive_service()
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
            if app_data_folder_id_test: # Use the id obtained in this test
                recipe_gdrive_output_folder_id = get_or_create_recipe_subfolder_id(
                    app_data_folder_id_test, 
                    folder_to_download['id'], 
                    "test_outputs", 
                    service=test_service
                )
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
