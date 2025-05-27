import os
import pickle # For token storage
import sys
import json # <--- ADDED IMPORT
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload
import io

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from utils import load_db, update_recipe_status # update_last_gdrive_scan_time can be added later
from config import GDRIVE_CREDENTIALS_PATH, GDRIVE_FOLDER_ID

# --- Google Drive API Setup ---
SCOPES = ['https://www.googleapis.com/auth/drive.readonly'] # Read-only access is sufficient for listing and downloading
# Token file will be saved in the project root (barged_api/)
TOKEN_PICKLE_PATH = os.path.join(os.path.dirname(__file__), '..', 'token_gdrive.json') # Changed to .json for readability

class GDriveServiceError(Exception):
    """Custom exception for GDrive service errors."""
    pass

def get_gdrive_service():
    """Authenticates and returns a Google Drive API service client."""
    creds = None
    if os.path.exists(TOKEN_PICKLE_PATH):
        try:
            with open(TOKEN_PICKLE_PATH, 'r') as token_file:
                creds = Credentials.from_authorized_user_info(json.load(token_file), SCOPES)
        except json.JSONDecodeError:
            creds = None # Invalid token file
        except Exception as e:
            print(f"Error loading token from {TOKEN_PICKLE_PATH}: {e}")
            creds = None

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as e:
                print(f"Failed to refresh GDrive token: {e}. Re-authentication needed.")
                creds = None # Force re-authentication
        else:
            if not GDRIVE_CREDENTIALS_PATH or not os.path.exists(GDRIVE_CREDENTIALS_PATH):
                msg = f"GDrive credentials file not found at {GDRIVE_CREDENTIALS_PATH}. Check GOOGLE_CLIENT_SECRET_JSON_FILENAME in .env."
                print(f"ERROR: {msg}")
                raise GDriveServiceError(msg)
            try:
                flow = InstalledAppFlow.from_client_secrets_file(GDRIVE_CREDENTIALS_PATH, SCOPES)
                # For a server environment where user interaction for auth is not possible during request,
                # this flow needs to be handled differently (e.g., pre-authorize, or use service account if applicable).
                # For Render.com, you'd typically authorize locally once, and upload the token_gdrive.json as an environment file OR
                # handle the OAuth redirect flow if Render supports it for your setup.
                # For now, this will work if run in an environment where a browser can be opened for auth.
                print("GDrive authentication required. Please follow the browser prompts.")
                print("If running on a headless server, this step will fail. Authorize locally and ensure token_gdrive.json is present.")
                creds = flow.run_local_server(port=0)
            except Exception as e:
                msg = f"Failed to run GDrive authentication flow: {e}"
                print(f"ERROR: {msg}")
                raise GDriveServiceError(msg)
        
        # Save the credentials for the next run
        try:
            with open(TOKEN_PICKLE_PATH, 'w') as token_file:
                token_file.write(creds.to_json())
            print(f"GDrive token saved to {TOKEN_PICKLE_PATH}")
        except Exception as e:
            print(f"Error saving GDrive token to {TOKEN_PICKLE_PATH}: {e}")
            # Not raising an error here, as the service might still work for the current session

    if not creds:
        msg = "Failed to obtain GDrive credentials after all attempts."
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

def list_folders_from_gdrive_and_db_status():
    """
    Fetches recipe folders (subfolders) from the configured Google Drive parent folder 
    and enriches them with status from db.json.
    """
    print(f"Listing folders from Google Drive parent ID: {GDRIVE_FOLDER_ID}")
    if not GDRIVE_FOLDER_ID or GDRIVE_FOLDER_ID == "...":
        print("ERROR: GDRIVE_TARGET_FOLDER_ID is not configured in .env. Cannot list GDrive folders.")
        return [] # Or raise an error

    try:
        service = get_gdrive_service()
        # Query for subfolders within the parent GDRIVE_FOLDER_ID
        query = f"'{GDRIVE_FOLDER_ID}' in parents and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
        results = service.files().list(
            q=query,
            pageSize=100, # Adjust as needed
            fields="nextPageToken, files(id, name)").execute()
        gdrive_folders = results.get('files', [])
        # update_last_gdrive_scan_time() # Consider calling this after successful fetch
    except HttpError as error:
        print(f"An HTTP error occurred while listing GDrive folders: {error}")
        # Potentially update db with scan failure status if desired
        return [] # Return empty list on error, or raise
    except GDriveServiceError as e:
        print(f"GDrive service error: {e}")
        return []
    except Exception as e:
        print(f"An unexpected error occurred while listing GDrive folders: {e}")
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
            elif status_from_db == "failed":
                 display_name_with_status = f"{folder_name_from_gdrive} (❌ Failed: {db_entry.get('error_message','N/A')[:30]}...)"

        enriched_folders.append({
            "id": folder_id,
            "name": folder_name_from_gdrive, # Original name from GDrive for processing
            "display_name": display_name_with_status, # Name with status for UI
            "status_from_db": status_display,
            "youtube_url": youtube_url_from_db
        })
    
    print(f"Enriched folders with DB status: {json.dumps(enriched_folders, indent=2)}")
    return enriched_folders

def download_folder_contents(folder_id: str, recipe_name: str, download_base_path: str) -> bool:
    """
    Downloads all video files from a given Google Drive folder_id to download_base_path.
    Updates db.json with download status.
    """
    print(f"Attempting to download video clips for folder ID {folder_id} ({recipe_name}) to {download_base_path}")

    if not os.path.exists(download_base_path):
        try:
            os.makedirs(download_base_path)
            print(f"Created download directory: {download_base_path}")
        except Exception as e:
            msg = f"Failed to create download directory {download_base_path}: {e}"
            update_recipe_status(recipe_id=folder_id, name=recipe_name, status="download_failed", error_message=msg)
            return False

    try:
        service = get_gdrive_service()
        # Query for video files within the specific recipe folder_id
        # Common video mimeTypes. Add more if needed: 'video/quicktime' for .mov, 'video/x-msvideo' for .avi etc.
        video_mime_types = "("
        video_mime_types += "mimeType='video/mp4' or "
        video_mime_types += "mimeType='video/mpeg' or "
        video_mime_types += "mimeType='video/quicktime' or "
        video_mime_types += "mimeType='video/x-msvideo' or "
        video_mime_types += "mimeType='video/x-matroska')" #.mkv
        
        query = f"'{folder_id}' in parents and {video_mime_types} and trashed = false"
        
        results = service.files().list(
            q=query,
            pageSize=50, # Max clips per recipe folder, adjust if necessary
            fields="files(id, name)").execute()
        items = results.get('files', [])

        if not items:
            msg = f"No video files found in GDrive folder ID {folder_id} ({recipe_name})."
            print(msg)
            update_recipe_status(recipe_id=folder_id, name=recipe_name, status="download_failed", error_message=msg)
            return False # Or True if an empty folder is not an error for your workflow

        print(f"Found {len(items)} video files in GDrive folder {folder_id} ({recipe_name}). Starting download...")
        downloaded_files_count = 0
        for item in items:
            file_id = item['id']
            file_name = item['name']
            file_path = os.path.join(download_base_path, file_name)
            
            print(f"Downloading GDrive file: {file_name} (ID: {file_id}) to {file_path}...")
            request = service.files().get_media(fileId=file_id)
            fh = io.BytesIO()
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while done is False:
                status, done = downloader.next_chunk()
                print(f"Download {file_name}: {int(status.progress() * 100)}%.")
            
            with open(file_path, 'wb') as f:
                fh.seek(0)
                f.write(fh.read())
            print(f"Successfully downloaded {file_name} to {file_path}")
            downloaded_files_count += 1

        if downloaded_files_count == len(items):
            print(f"All {downloaded_files_count} video files for {recipe_name} downloaded successfully.")
            update_recipe_status(recipe_id=folder_id, name=recipe_name, status="downloaded", raw_clips_path=download_base_path)
            return True
        else:
            msg = f"Partial download for {recipe_name}. Expected {len(items)}, got {downloaded_files_count}."
            print(f"WARNING: {msg}")
            update_recipe_status(recipe_id=folder_id, name=recipe_name, status="download_failed", error_message=msg, raw_clips_path=download_base_path)
            return False # Treat partial as failure for now

    except HttpError as error:
        msg = f"GDrive API HTTP error during download for {recipe_name}: {error}"
        print(f"ERROR: {msg}")
        update_recipe_status(recipe_id=folder_id, name=recipe_name, status="download_failed", error_message=str(error))
        return False
    except GDriveServiceError as e:
        # This will catch auth errors or client build errors from get_gdrive_service
        print(f"ERROR: GDriveServiceError during download for {recipe_name}: {e}")
        update_recipe_status(recipe_id=folder_id, name=recipe_name, status="download_failed", error_message=str(e))
        return False
    except Exception as e:
        msg = f"Unexpected error during GDrive download for {recipe_name}: {e}"
        print(f"ERROR: {msg}")
        update_recipe_status(recipe_id=folder_id, name=recipe_name, status="download_failed", error_message=msg)
        return False

# For direct testing of this module
if __name__ == '__main__':
    print("Testing GDrive Service Module...")
    # Ensure .env is populated and client_secret.json (or your named file) is present
    # and token_gdrive.json will be created/used in barged_api/
    
    print(f"Using GDrive credentials path: {GDRIVE_CREDENTIALS_PATH}")
    print(f"Target GDrive Folder ID: {GDRIVE_FOLDER_ID}")

    # Test 1: List Folders (and their DB status)
    print("\n--- Test: Listing Folders ---")
    try:
        folders = list_folders_from_gdrive_and_db_status()
        if folders:
            print(f"Successfully listed {len(folders)} folders:")
            for f_idx, f_item in enumerate(folders):
                print(f"  {f_idx+1}. ID: {f_item['id']}, Name: {f_item['name']}, Display: {f_item['display_name']}, Status: {f_item['status_from_db']}")
                if f_item['youtube_url']:
                    print(f"     YouTube URL: {f_item['youtube_url']}")
            
            # Test 2: Download contents of the first non-processed/non-failed folder found
            print("\n--- Test: Downloading Folder Contents ---")
            folder_to_download = None
            for f_test_download in folders:
                # Try to find a folder that is 'New' or in a state that implies it needs downloading
                # Avoid re-downloading 'downloaded', 'merged', etc. for a clean test run
                if f_test_download['status_from_db'] == 'New': 
                    folder_to_download = f_test_download
                    break
            if not folder_to_download and folders: # If no 'New' found, pick first for test if any exist
                 print("No 'New' folders found to test download. You may need to clear db.json or add new GDrive folders.")
                 print("Picking the first available folder for a download attempt if one exists...")
                 folder_to_download = folders[0]

            if folder_to_download:
                print(f"Attempting to download recipe: '{folder_to_download['name']}' (ID: {folder_to_download['id']})")
                # Create a temporary download path for the test
                test_download_dir_base = os.path.join(os.path.dirname(__file__), '..', 'videos', 'raw') # Matches config.RAW_DIR structure
                recipe_safe_name = "".join(c if c.isalnum() else "_" for c in folder_to_download['name'])
                specific_test_download_path = os.path.join(test_download_dir_base, recipe_safe_name + "_gdriveTest")
                
                if not os.path.exists(specific_test_download_path):
                     os.makedirs(specific_test_download_path)
                print(f"Test download path: {specific_test_download_path}")

                download_success = download_folder_contents(folder_to_download['id'], folder_to_download['name'], specific_test_download_path)
                if download_success:
                    print(f"SUCCESS: Content download test for '{folder_to_download['name']}' seems successful.")
                    print(f"Files should be in: {specific_test_download_path}")
                    # You might want to manually check the directory or clean it up
                else:
                    print(f"FAILURE: Content download test for '{folder_to_download['name']}' failed. Check logs and db.json.")
            else:
                print("No folder found to test download functionality.")
        else:
            print("No folders listed. Check GDrive Folder ID, permissions, or API errors above.")

    except GDriveServiceError as e:
        print(f"GDrive Service Error during test: {e}")
    except Exception as e:
        print(f"An unexpected error occurred during GDrive module test: {e}")

    print("\nGDrive Service Module testing finished.")
