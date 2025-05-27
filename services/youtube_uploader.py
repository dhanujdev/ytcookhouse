import os
import json 
import sys
import tempfile # For temporary local video file
from google.oauth2.service_account import Credentials as ServiceAccountCredentials
from google.oauth2.credentials import Credentials as UserCredentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload
import subprocess # Only for __main__ test dummy video

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from config import (
    GOOGLE_AUTH_METHOD,
    GOOGLE_SERVICE_ACCOUNT_INFO,
    # ---- Added for Refactoring ----
    YOUTUBE_SERVICE_CLIENT, # Shared client instance
    APP_STARTUP_STATUS      # For updating status during checks
    # -----------------------------
)
from utils import update_recipe_status, get_recipe_status # get_recipe_status for GDrive ID
from services import gdrive # Import gdrive service

SCOPES_YOUTUBE = [
    "https://www.googleapis.com/auth/youtube.upload", 
    "https://www.googleapis.com/auth/youtube.readonly" # Added for channel list checks
]
OAUTH_TOKEN_YOUTUBE_PATH = os.path.join(os.path.dirname(__file__), '..', 'token_youtube.json')
API_SERVICE_NAME = 'youtube'
API_VERSION = 'v3'

class YouTubeUploaderError(Exception):
    pass

def get_youtube_service(bypass_shared_client=False):
    """
    Returns a YouTube API service client.
    Uses a shared client instance after initial successful creation unless bypass_shared_client is True.
    """
    if not bypass_shared_client and YOUTUBE_SERVICE_CLIENT:
        # print("YouTube Service: Returning shared client.") # Optional: for debugging
        return YOUTUBE_SERVICE_CLIENT

    print("YouTube Service: Attempting to create new client...")
    creds = None
    if GOOGLE_AUTH_METHOD == "SERVICE_ACCOUNT_INDIVIDUAL_FIELDS" and GOOGLE_SERVICE_ACCOUNT_INFO:
        print(f"YouTube Auth: Attempting with Service Account (Individual Fields).")
        try: 
            creds = ServiceAccountCredentials.from_service_account_info(GOOGLE_SERVICE_ACCOUNT_INFO, scopes=SCOPES_YOUTUBE)
            print(f"YouTube Auth: Successfully obtained credentials via Service Account (Individual Fields).")
        except Exception as e:
            if bypass_shared_client: APP_STARTUP_STATUS["youtube_error_details"] = f"SA Cred error: {e}"
            raise YouTubeUploaderError(f"YouTube Auth: SA Individual Fields cred error: {e}")
    else: 
        msg = "YouTube Auth: SERVICE_ACCOUNT_INDIVIDUAL_FIELDS method not configured or GOOGLE_SERVICE_ACCOUNT_INFO missing in config.py."
        print(f"ERROR: {msg}")
        if bypass_shared_client: APP_STARTUP_STATUS["youtube_error_details"] = msg
        raise YouTubeUploaderError(msg)
    
    if not creds: # Safeguard
        msg = "YouTube Auth: Failed to obtain credentials."
        if bypass_shared_client: APP_STARTUP_STATUS["youtube_error_details"] = msg
        raise YouTubeUploaderError(msg)

    try: 
        service = build(API_SERVICE_NAME, API_VERSION, credentials=creds)
        print(f"YouTube Auth: Service client created successfully.") # Changed from BACKGROUND TASK for clarity
        
        if YOUTUBE_SERVICE_CLIENT is None or bypass_shared_client:
            import config # Import config directly to modify its attributes
            config.YOUTUBE_SERVICE_CLIENT = service
            print("YouTube Service: New client stored as shared client.")
        return service
    except Exception as e: 
        if bypass_shared_client: APP_STARTUP_STATUS["youtube_error_details"] = f"Failed to build service: {e}"
        raise YouTubeUploaderError(f"Failed to build YouTube service: {e}")

def check_youtube_service(service_client) -> bool:
    """
    Performs a basic check of the YouTube service client using channels.list(mine=True).
    Returns True if successful, False otherwise.
    Updates APP_STARTUP_STATUS with error details on failure.
    """
    if not service_client:
        APP_STARTUP_STATUS["youtube_error_details"] = "Service client is None."
        return False
    try:
        print("YouTube Check: Attempting to list own channel details (channels.list mine=True)...")
        test_request = service_client.channels().list(
            part="snippet", # Using minimal part for a simple check
            mine=True
        )
        test_response = test_request.execute()
        print(f"YouTube Check: channels.list successful. Response: {test_response}")
        return True
    except HttpError as he:
        error_content_test = he.content.decode('utf-8') if he.content else 'No details.'
        error_msg = f"YouTube Check: API HttpError: {he.resp.status} - {error_content_test}"
        print(f"ERROR: {error_msg}")
        APP_STARTUP_STATUS["youtube_error_details"] = error_msg
        return False
    except Exception as e:
        error_msg = f"YouTube Check: Unexpected error: {str(e)}"
        print(f"ERROR: {error_msg}")
        APP_STARTUP_STATUS["youtube_error_details"] = error_msg
        return False

def upload_video_to_youtube(metadata: dict, 
                            privacy_status: str = "private", 
                            recipe_db_id_for_status_update: str = None, 
                            recipe_name_for_status_update: str = "Unknown Recipe"):
    # video_file_path is no longer a direct arg; will be downloaded from GDrive
    print(f"BACKGROUND TASK: YouTube: Starting upload for {recipe_db_id_for_status_update} ({recipe_name_for_status_update})")
    current_db_status_on_exit = "UPLOAD_FAILED"
    youtube_url_on_success = None
    error_message_on_exit = "Unknown YouTube upload error"
    local_temp_video_path = None

    try:
        if not recipe_db_id_for_status_update:
            raise YouTubeUploaderError("recipe_db_id_for_status_update is required for fetching video and updating status.")

        recipe_data = get_recipe_status(recipe_db_id_for_status_update)
        if not recipe_data:
            raise YouTubeUploaderError(f"Recipe data for {recipe_db_id_for_status_update} not found in DB.")
        
        merged_video_gdrive_id = recipe_data.get('merged_video_gdrive_id')
        if not merged_video_gdrive_id:
            raise YouTubeUploaderError(f"merged_video_gdrive_id not found in DB for recipe {recipe_db_id_for_status_update}. Cannot upload.")

        gdrive_service = gdrive.get_gdrive_service()
        
        # Create a temporary local file for the downloaded video
        temp_video_file = tempfile.NamedTemporaryFile(delete=False, suffix='.mp4') # Consider a temp dir from config
        local_temp_video_path = temp_video_file.name
        temp_video_file.close() # Close it so gdrive download can write to it
        
        print(f"BACKGROUND TASK: YouTube: Downloading video from GDrive (ID: {merged_video_gdrive_id}) to temp path: {local_temp_video_path}")
        if not gdrive.download_file_from_drive(merged_video_gdrive_id, local_temp_video_path, service=gdrive_service):
            raise YouTubeUploaderError(f"Failed to download merged video ({merged_video_gdrive_id}) from GDrive for YouTube upload.")
        print(f"BACKGROUND TASK: YouTube: Video downloaded successfully to {local_temp_video_path}")

        if not os.path.exists(local_temp_video_path) or os.path.getsize(local_temp_video_path) == 0:
             raise YouTubeUploaderError(f"Local temp video file {local_temp_video_path} is missing or empty after GDrive download attempt.")

        if not metadata.get('title'):
            raise YouTubeUploaderError("Video title missing in metadata.")

        youtube_service = get_youtube_service()

        # Test API call to list channels
        try:
            print("BACKGROUND TASK: YouTube: Attempting a test API call (list channels)...")
            test_request = youtube_service.channels().list(
                part="snippet,contentDetails,statistics",
                mine=True
            )
            test_response = test_request.execute()
            print(f"BACKGROUND TASK: YouTube: Test API call successful: {test_response}")
        except HttpError as he:
            error_content_test = he.content.decode('utf-8') if he.content else 'No details.'
            print(f"BACKGROUND TASK: YouTube: Test API call FAILED: {error_content_test}")
            # Optionally, re-raise or handle differently if this test fails
            # For now, let it proceed to the upload attempt to see if the error is consistent
        
        request_body = {
            'snippet': {'title': metadata.get('title'), 'description': metadata.get('description', ''),
                        'tags': metadata.get('tags', []), 'categoryId': '22'},
            'status': {'privacyStatus': privacy_status, 'selfDeclaredMadeForKids': False}
        }
        media_file = MediaFileUpload(local_temp_video_path, chunksize=-1, resumable=True)
        print(f"BACKGROUND TASK: YouTube: Initiating actual YouTube API upload for {local_temp_video_path}...")
        response_upload = youtube_service.videos().insert(part='snippet,status', body=request_body, media_body=media_file).execute()
        video_id = response_upload.get('id')
        youtube_url_on_success = f"https://www.youtube.com/watch?v={video_id}"
        
        current_db_status_on_exit = "UPLOADED_TO_YOUTUBE"
        error_message_on_exit = None

    except HttpError as e:
        error_content = e.content.decode('utf-8') if e.content else 'No details.'
        error_message_on_exit = f"YouTube HTTP error {e.resp.status}: {error_content[:500]}"
        if "quotaExceeded" in error_content: error_message_on_exit = "YouTube API quota exceeded."
    except YouTubeUploaderError as yue:
        error_message_on_exit = str(yue)
    except Exception as e:
        error_message_on_exit = f"Unexpected error during YouTube upload: {str(e)}"
    
    finally:
        if recipe_db_id_for_status_update:
            kwargs_for_status_update = {}
            if youtube_url_on_success and current_db_status_on_exit == "UPLOADED_TO_YOUTUBE":
                kwargs_for_status_update['youtube_url'] = youtube_url_on_success
            if error_message_on_exit and current_db_status_on_exit == "UPLOAD_FAILED": # Check specific status
                kwargs_for_status_update['error_message'] = error_message_on_exit
            
            update_recipe_status(
                recipe_id=recipe_db_id_for_status_update, 
                name=recipe_name_for_status_update, 
                status=current_db_status_on_exit, 
                **kwargs_for_status_update
            )
            print(f"BACKGROUND TASK: YouTube: Final DB status for {recipe_db_id_for_status_update} to '{current_db_status_on_exit}'. URL: {youtube_url_on_success if youtube_url_on_success else 'N/A'}, Err: {error_message_on_exit if error_message_on_exit else 'None'}")

        # Attempt to clean up the local temporary video file
        if local_temp_video_path:
            if 'media_file' in locals() and media_file is not None:
                # If using a context manager or if media_file has a close() method:
                # try:
                #     if hasattr(media_file, '_stream') and hasattr(media_file._stream, 'close'):
                #         media_file._stream.close()
                #     elif hasattr(media_file, 'close'): # Hypothetical
                #          media_file.close()
                # except Exception as e_close:
                #     print(f"BACKGROUND TASK: YouTube: Minor error trying to close media_file stream: {e_close}")
                del media_file # Remove reference to MediaFileUpload object

            if os.path.exists(local_temp_video_path):
                try:
                    # Add a small delay before attempting to delete
                    import time
                    time.sleep(1) # Wait 1 second
                    os.remove(local_temp_video_path)
                    print(f"BACKGROUND TASK: YouTube: Cleaned local temp video: {local_temp_video_path}")
                except Exception as e_clean:
                    print(f"BACKGROUND TASK: YouTube: WARN Failed to clean local temp video {local_temp_video_path}: {e_clean}")
            else:
                print(f"BACKGROUND TASK: YouTube: Local temp video {local_temp_video_path} already deleted or was not created.")
        
    # No explicit return needed by background task manager in routes if it only checks DB status

# __main__ block needs rework to align with GDrive based file handling for tests
