import os
import json 
import sys
from google.oauth2.service_account import Credentials as ServiceAccountCredentials 
from google.oauth2.credentials import Credentials as UserCredentials 
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload, MediaFileUpload
import io
import shutil # Added for __main__ test cleanup, though not used in main functions

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from utils import load_db, update_recipe_status
from config import (
    GDRIVE_TARGET_FOLDER_ID,
    GOOGLE_AUTH_METHOD,
    GOOGLE_SERVICE_ACCOUNT_INFO,
    TEMP_PROCESSING_BASE_DIR, # Added for relative path calculations
    # For testing block in __main__
    GOOGLE_DRIVE_APP_DATA_FOLDER_NAME,
    DB_JSON_FILENAME_ON_DRIVE,
    APP_ROOT_DIR as APP_ROOT_DIR_CONFIG, # Import APP_ROOT_DIR and alias it for the __main__ block
    RAW_DIR as CONFIG_RAW_DIR,
    # ---- Added for Refactoring ----
    GDRIVE_SERVICE_CLIENT,
    APP_STARTUP_STATUS
    # -----------------------------
)

SCOPES = ['https://www.googleapis.com/auth/drive'] 

class GDriveServiceError(Exception):
    """Custom exception for GDrive service errors."""
    pass

def create_gdrive_service(): # Renamed and simplified
    """Creates and returns a new Google Drive API service client."""
    print("GDrive Service Factory: Attempting to create new client...")
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
            # APP_STARTUP_STATUS updates will be handled by the caller (e.g., main.py startup)
            raise GDriveServiceError(f"Service Account (Individual Fields) credential error: {e}")
    else:
        msg = "GDrive Auth: SERVICE_ACCOUNT_INDIVIDUAL_FIELDS method not configured or GOOGLE_SERVICE_ACCOUNT_INFO missing in config.py."
        print(f"ERROR: {msg}")
        raise GDriveServiceError(msg)

    if not creds: 
        msg = f"GDrive Auth: Failed to obtain credentials."
        print(f"ERROR: {msg}")
        raise GDriveServiceError(msg)

    try:
        service = build('drive', 'v3', credentials=creds)
        print("GDrive Service Factory: Google Drive service client created successfully.")
        return service
    except Exception as e:
        msg = f"GDrive Service Factory: Failed to build GDrive service client: {e}"
        print(f"ERROR: {msg}")
        raise GDriveServiceError(msg)

def check_gdrive_service(service_client) -> bool:
    """
    Performs a basic check of the GDrive service client.
    Returns True if successful, False otherwise.
    Updates APP_STARTUP_STATUS with error details on failure.
    """
    if not service_client:
        APP_STARTUP_STATUS["gdrive_error_details"] = "Service client is None."
        return False
    try:
        print("GDrive Check: Attempting to list root folder (pageSize=1) as a health check...")
        # Perform a simple, non-mutating operation, e.g., list files from root with a small page size
        service_client.files().list(pageSize=1, fields="files(id, name)").execute()
        print("GDrive Check: Root folder list successful. Service is operational.")
        return True
    except HttpError as e:
        error_msg = f"GDrive Check: API HttpError: {e.resp.status} - {e.content.decode('utf-8') if e.content else 'No details'}"
        print(f"ERROR: {error_msg}")
        APP_STARTUP_STATUS["gdrive_error_details"] = error_msg
        return False
    except Exception as e:
        error_msg = f"GDrive Check: Unexpected error: {str(e)}"
        print(f"ERROR: {error_msg}")
        APP_STARTUP_STATUS["gdrive_error_details"] = error_msg
        return False

def find_file_id_by_name(parent_folder_id: str, filename: str, service=None):
    if not service:
        from config import GDRIVE_SERVICE_CLIENT
        service = GDRIVE_SERVICE_CLIENT
        if not service:
            # This case should ideally not be hit if startup sequence is robust
            # and this function is called after startup within main app context.
            # For background tasks, they should explicitly pass a service instance.
            print("ERROR: GDRIVE_SERVICE_CLIENT not initialized when called from find_file_id_by_name without service arg.")
            raise GDriveServiceError("Shared GDrive client not initialized.")
    try:
        query = f"name = '{filename}' and '{parent_folder_id}' in parents and trashed = false"
        response = service.files().list(q=query, spaces='drive', fields='files(id, name)').execute()
        for file_item in response.get('files', []):
            return file_item.get('id')
    except HttpError as error:
        print(f"An error occurred while trying to find file '{filename}': {error}")
    return None

def get_or_create_app_data_folder_id(service=None):
    if not service:
        from config import GDRIVE_SERVICE_CLIENT
        service = GDRIVE_SERVICE_CLIENT
        if not service:
            raise GDriveServiceError("Shared GDrive client not initialized. Called from get_or_create_app_data_folder_id.")
    
    if not GOOGLE_DRIVE_APP_DATA_FOLDER_NAME:
        raise GDriveServiceError("GOOGLE_DRIVE_APP_DATA_FOLDER_NAME is not set in config.")

    query = f"name='{GOOGLE_DRIVE_APP_DATA_FOLDER_NAME}' and mimeType='application/vnd.google-apps.folder' and 'root' in parents and trashed=false"
    try:
        response = service.files().list(q=query, spaces='drive', fields='files(id)').execute()
        folders = response.get('files', [])
        if folders:
            folder_id = folders[0].get('id')
            print(f"GDrive: Found existing App Data folder '{GOOGLE_DRIVE_APP_DATA_FOLDER_NAME}' with ID: {folder_id}")
            return folder_id
        else:
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
    if not service:
        from config import GDRIVE_SERVICE_CLIENT
        service = GDRIVE_SERVICE_CLIENT
        if not service:
            raise GDriveServiceError("Shared GDrive client not initialized. Called from upload_file_to_drive.")
    try:
        file_metadata = {'name': drive_filename}
        if not existing_file_id: 
             file_metadata['parents'] = [drive_folder_id]

        media = MediaFileUpload(local_file_path, mimetype=mimetype, resumable=True)
        
        if existing_file_id:
            print(f"GDrive: Updating existing file ID {existing_file_id} with {local_file_path} as {drive_filename}")
            request = service.files().update(fileId=existing_file_id, body=file_metadata, media_body=media, fields='id')
        else:
            print(f"GDrive: Uploading new file {local_file_path} to folder {drive_folder_id} as {drive_filename}")
            request = service.files().create(body=file_metadata, media_body=media, fields='id')
        
        file_item = request.execute()
        uploaded_file_id = file_item.get('id')
        print(f"GDrive: File '{drive_filename}' uploaded successfully. File ID: {uploaded_file_id}")
        return uploaded_file_id
    except HttpError as error:
        print(f"GDrive: An error occurred during file upload: {error}")
        raise GDriveServiceError(f"Failed to upload file '{drive_filename}': {error}")
    except Exception as e:
        print(f"GDrive: An unexpected error occurred during file upload: {e}")
        raise GDriveServiceError(f"Unexpected error uploading file '{drive_filename}': {e}")

def get_file_content_from_drive(file_id: str, service=None) -> str | None:
    if not service:
        from config import GDRIVE_SERVICE_CLIENT
        service = GDRIVE_SERVICE_CLIENT
        if not service:
            raise GDriveServiceError("Shared GDrive client not initialized. Called from get_file_content_from_drive.")
    try:
        request = service.files().get_media(fileId=file_id)
        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            status, done = downloader.next_chunk()
        fh.seek(0)
        return fh.read().decode('utf-8')
    except HttpError as error:
        if error.resp.status == 404:
            print(f"GDrive: File with ID {file_id} not found for download.")
            return None 
        print(f"GDrive: An HttpError occurred downloading file ID {file_id}: {error}")
        raise GDriveServiceError(f"Failed to download file content for ID '{file_id}': {error}")
    except Exception as e:
        print(f"GDrive: An unexpected error occurred downloading file ID {file_id}: {e}")
        raise GDriveServiceError(f"Unexpected error downloading file content for ID '{file_id}': {e}")

def get_or_create_recipe_subfolder_id(app_data_folder_id: str, recipe_id: str, subfolder_name: str, service=None):
    if not service:
        from config import GDRIVE_SERVICE_CLIENT
        service = GDRIVE_SERVICE_CLIENT
        if not service:
            raise GDriveServiceError("Shared GDrive client not initialized. Called from get_or_create_recipe_subfolder_id.")
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
    if not service:
        from config import GDRIVE_SERVICE_CLIENT
        service = GDRIVE_SERVICE_CLIENT
        if not service:
            raise GDriveServiceError("Shared GDrive client not initialized. Called from download_file_from_drive.")
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
        if os.path.exists(local_download_path):
            pass # os.remove(local_download_path)
        raise GDriveServiceError(f"Failed to download file from drive (ID: {file_id}): {error}")
    except Exception as e:
        print(f"GDrive: An unexpected error occurred downloading file ID {file_id} to {local_download_path}: {e}")
        raise GDriveServiceError(f"Unexpected error downloading file from drive (ID: {file_id}): {e}")

def list_folders_from_gdrive_and_db_status():
    print(f"Listing folders from Google Drive parent ID: {GDRIVE_TARGET_FOLDER_ID}")
    if not GDRIVE_TARGET_FOLDER_ID or GDRIVE_TARGET_FOLDER_ID == "...":
        print("ERROR: GDRIVE_TARGET_FOLDER_ID is not configured in .env. Cannot list GDrive folders.")
        return []

    try:
        from config import GDRIVE_SERVICE_CLIENT
        # This function is typically called from routes that should use the shared client.
        service_to_use = GDRIVE_SERVICE_CLIENT
        if not service_to_use:
             # Fallback or error if called too early / in wrong context
            print("WARNING: list_folders_from_gdrive_and_db_status called when shared GDrive client is not ready. Attempting to create one.")
            # This creates a temporary client for this call if the shared one isn't ready.
            # Ideally, this function is called by routes that can rely on the startup-initialized client.
            service_to_use = create_gdrive_service() 
        
        query = f"'{GDRIVE_TARGET_FOLDER_ID}' in parents and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
        results = service_to_use.files().list(q=query, pageSize=100, fields="nextPageToken, files(id, name)").execute()
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
            continue 
        db_entry = db_recipes.get(folder_id)
        status_from_db_value = "New"
        display_name_with_status = folder_name_from_gdrive
        youtube_url_from_db = None

        if db_entry:
            status_from_db_value = db_entry.get("status", "Unknown")
            if not isinstance(status_from_db_value, str):
                status_from_db_value = str(status_from_db_value)

        status_display_text = status_from_db_value.replace("_", " ").title()
        
        if status_from_db_value == "New" or status_from_db_value == "Unknown":
            display_name_with_status = folder_name_from_gdrive
        elif status_from_db_value.upper() == "UPLOADED_TO_YOUTUBE" or status_from_db_value == "uploaded":
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
            "status_from_db": status_from_db_value, 
            "youtube_url": youtube_url_from_db
        })
    print(f"Enriched folders with DB status: {json.dumps(enriched_folders, indent=2)}")
    return enriched_folders

def download_folder_contents(folder_id: str, recipe_name: str, download_base_path: str) -> bool:
    # download_base_path is the ABSOLUTE path where files will be downloaded for the current environment.
    print(f"Attempting to download video clips for folder ID {folder_id} ({recipe_name}) to {download_base_path}")
    os.makedirs(download_base_path, exist_ok=True)

    try:
        from config import GDRIVE_SERVICE_CLIENT
        service_to_use = GDRIVE_SERVICE_CLIENT
        if not service_to_use:
            print("WARNING: download_folder_contents called when shared GDrive client is not ready. Attempting to create one.")
            service_to_use = create_gdrive_service()

        video_mime_types = "(" + " or ".join([f"mimeType='{m}'" for m in ['video/mp4', 'video/mpeg', 'video/quicktime', 'video/x-msvideo', 'video/x-matroska']]) + ")"
        query = f"'{folder_id}' in parents and {video_mime_types} and trashed = false"
        results = service.files().list(q=query, pageSize=50, fields="files(id, name)").execute()
        items = results.get('files', [])

        if not items:
            msg = f"No video files found in GDrive folder ID {folder_id} ({recipe_name})."
            update_recipe_status(recipe_id=folder_id, name=recipe_name, status="DOWNLOAD_FAILED", error_message=msg)
            return False
        
        print(f"Found {len(items)} video files in GDrive folder {folder_id}. Starting download...")
        for item in items:
            file_id, file_name = item['id'], item['name']
            file_path = os.path.join(download_base_path, file_name) 
            print(f"Downloading GDrive file: {file_name} to {file_path}...")
            request_dl = service.files().get_media(fileId=file_id)
            fh = io.BytesIO()
            downloader = MediaIoBaseDownload(fh, request_dl)
            done = False
            while not done:
                status, done = downloader.next_chunk()
                if status: print(f"Download {file_name}: {int(status.progress() * 100)}%.")
            with open(file_path, 'wb') as f:
                fh.seek(0)
                f.write(fh.read())
            print(f"Successfully downloaded {file_name}")

        relative_path_for_db = os.path.relpath(download_base_path, TEMP_PROCESSING_BASE_DIR)
        print(f"GDrive Service: Storing relative path for raw_clips_path in DB: '{relative_path_for_db}'")
        update_recipe_status(recipe_id=folder_id, name=recipe_name, status="DOWNLOADED", raw_clips_path=relative_path_for_db)
        return True
    except Exception as e:
        msg = f"Error during GDrive download for {recipe_name}: {e}"
        print(f"ERROR: {msg}")
        update_recipe_status(recipe_id=folder_id, name=recipe_name, status="DOWNLOAD_FAILED", error_message=msg)
        return False

if __name__ == '__main__':
    print("Testing GDrive Service Module (Service Account with Individual Fields method)...")
    print(f"Configured Google Auth Method from config.py: {GOOGLE_AUTH_METHOD}")
    
    if GOOGLE_AUTH_METHOD == "SERVICE_ACCOUNT_INDIVIDUAL_FIELDS":
        print("Attempting to use Service Account credentials constructed from individual .env variables.")
    else:
        print(f"Warning: Expected GOOGLE_AUTH_METHOD to be 'SERVICE_ACCOUNT_INDIVIDUAL_FIELDS', but got '{GOOGLE_AUTH_METHOD}'. Test may fail or use unexpected auth.")
    
    print(f"Target GDrive Folder ID: {GDRIVE_TARGET_FOLDER_ID}")
    APP_DATA_FOLDER_ID_TEST = None 
    try:
        # For __main__ test block, always create a fresh service instance
        test_service = create_gdrive_service()
        app_data_folder_id_test = get_or_create_app_data_folder_id(service=test_service) # Pass the fresh service
        if not app_data_folder_id_test:
            print("CRITICAL TEST ERROR: Could not get or create App Data Folder in GDrive. Aborting further GDrive tests.")
            sys.exit(1) 
        APP_DATA_FOLDER_ID_TEST = app_data_folder_id_test
        print(f"Test: App Data Folder ID: {APP_DATA_FOLDER_ID_TEST}")

        db_test_content = {"test_key": "test_value_initial"}
        
        existing_db_file_id = find_file_id_by_name(app_data_folder_id_test, DB_JSON_FILENAME_ON_DRIVE, service=test_service)
        print(f"Test: Existing DB file ID on GDrive for '{DB_JSON_FILENAME_ON_DRIVE}': {existing_db_file_id}")

        dummy_local_db_path = os.path.join(APP_ROOT_DIR_CONFIG, "temp_test_db.json") # Use APP_ROOT_DIR_CONFIG
        if not os.path.exists(os.path.dirname(dummy_local_db_path)):
             os.makedirs(os.path.dirname(dummy_local_db_path))
        with open(dummy_local_db_path, 'w') as f_db_test:
            json.dump(db_test_content, f_db_test)

        uploaded_db_file_id = upload_file_to_drive(
            local_file_path=dummy_local_db_path,
            drive_folder_id=app_data_folder_id_test,
            drive_filename=DB_JSON_FILENAME_ON_DRIVE, 
            mimetype='application/json',
            existing_file_id=existing_db_file_id,
            service=test_service
        )
        if uploaded_db_file_id:
            print(f"Test: Successfully uploaded/updated {DB_JSON_FILENAME_ON_DRIVE} to GDrive. File ID: {uploaded_db_file_id}")
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

        folders = list_folders_from_gdrive_and_db_status()
        if folders:
            print(f"Successfully listed {len(folders)} folders.")
            folder_to_download = next((f for f in folders if f['status_from_db'] == 'New'), None)
            if not folder_to_download and folders: folder_to_download = folders[0]

            if folder_to_download:
                print(f"\nAttempting to download: '{folder_to_download['name']}' (ID: {folder_to_download['id']})")
                
                recipe_safe_name_test = "".join(c if c.isalnum() else "_" for c in folder_to_download['name'])
                local_temp_raw_dir_for_test = os.path.join(CONFIG_RAW_DIR, recipe_safe_name_test + "_gdriveSvcTest_tempLocal") # Use CONFIG_RAW_DIR
                if not os.path.exists(local_temp_raw_dir_for_test): os.makedirs(local_temp_raw_dir_for_test)

                print(f"Test download path (local ephemeral): {local_temp_raw_dir_for_test}")
                download_success = download_folder_contents(folder_to_download['id'], folder_to_download['name'], local_temp_raw_dir_for_test)
                print(f"Download test for '{folder_to_download['name']}' to local temp dir {'SUCCESSFUL' if download_success else 'FAILED'}.")
            
                if app_data_folder_id_test:
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
            
                if os.path.exists(local_temp_raw_dir_for_test) and os.path.isdir(local_temp_raw_dir_for_test):
                     shutil.rmtree(local_temp_raw_dir_for_test) # Use shutil.rmtree for directories

        else:
            print("Test: No folder found to test download functionality (or no new folders)." )
    except Exception as e:
        print(f"An UNEXPECTED error occurred during GDrive module test: {e}")
        import traceback
        traceback.print_exc()
    print("\nGDrive Service Module testing finished.")
