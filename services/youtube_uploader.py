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
    # YOUTUBE_SERVICE_CLIENT, # No longer managing shared client within this module for OAuth user flow
    APP_STARTUP_STATUS,      # For updating status during checks
    # CLIENT_SECRET_YOUTUBE_PATH, # No longer using file path for YouTube client secret
    TOKEN_YOUTUBE_OAUTH_PATH,   # Path to store/load user tokens
    YOUTUBE_AUTH_METHOD         # To decide auth flow
)
from utils import update_recipe_status, get_recipe_status 
from services import gdrive 

# For OAuth User Consent Flow
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.exceptions import RefreshError

SCOPES_YOUTUBE = [
    "https://www.googleapis.com/auth/youtube.upload", 
    "https://www.googleapis.com/auth/youtube.readonly", 
    "https://www.googleapis.com/auth/drive.readonly"  # Adding to match Google's response
]
# OAUTH_TOKEN_YOUTUBE_PATH is now TOKEN_YOUTUBE_OAUTH_PATH from config
API_SERVICE_NAME = 'youtube'
API_VERSION = 'v3'

class YouTubeUploaderError(Exception):
    pass

class YouTubeNeedsAuthorization(Exception):
    """Custom exception to indicate user authorization is required."""
    def __init__(self, authorization_url):
        self.authorization_url = authorization_url
        super().__init__(f"User authorization required. Please visit: {authorization_url}")

def create_youtube_service(redirect_uri: str = None): # redirect_uri needed for web flow
    """Creates and returns a new YouTube API service client using OAuth 2.0 User Consent Flow."""
    creds = None
    print(f"YouTube OAuth: Attempting to get credentials. Configured method: {YOUTUBE_AUTH_METHOD}")

    client_config_dict_from_env = None
    client_config_json_str_env = os.getenv("GOOGLE_CLIENT_SECRET_JSON_YOUTUBE")

    if not client_config_json_str_env:
        msg = "YouTube OAuth Error: GOOGLE_CLIENT_SECRET_JSON_YOUTUBE environment variable not set."
        print(f"ERROR: {msg}")
        if APP_STARTUP_STATUS.get("youtube_error_details") is None: 
             APP_STARTUP_STATUS["youtube_error_details"] = msg
        raise YouTubeUploaderError(msg)
    
    print("YouTube OAuth (create_youtube_service): Loading client config from GOOGLE_CLIENT_SECRET_JSON_YOUTUBE env var.")
    try:
        client_config_dict_from_env = json.loads(client_config_json_str_env)
    except json.JSONDecodeError as e:
        msg = f"YouTube OAuth Error: Failed to parse GOOGLE_CLIENT_SECRET_JSON_YOUTUBE: {e}"
        print(f"ERROR: {msg}")
        if APP_STARTUP_STATUS.get("youtube_error_details") is None: 
             APP_STARTUP_STATUS["youtube_error_details"] = msg
        raise YouTubeUploaderError(msg)
    
    if not client_config_dict_from_env: # Should be caught by previous checks, but as a safeguard
        msg = "YouTube OAuth Error: Client config could not be loaded from GOOGLE_CLIENT_SECRET_JSON_YOUTUBE."
        print(f"ERROR: {msg}")
        if APP_STARTUP_STATUS.get("youtube_error_details") is None: 
             APP_STARTUP_STATUS["youtube_error_details"] = msg
        raise YouTubeUploaderError(msg)

    if os.path.exists(TOKEN_YOUTUBE_OAUTH_PATH):
        try:
            creds = UserCredentials.from_authorized_user_file(TOKEN_YOUTUBE_OAUTH_PATH, SCOPES_YOUTUBE)
            print(f"YouTube OAuth: Loaded credentials from {TOKEN_YOUTUBE_OAUTH_PATH}")
        except Exception as e:
            print(f"YouTube OAuth: Error loading token file: {e}. Will attempt re-authorization.")
            creds = None # Ensure creds is None to trigger re-auth

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            print("YouTube OAuth: Credentials expired, attempting to refresh...")
            try:
                creds.refresh(Request())
                print("YouTube OAuth: Credentials refreshed successfully.")
                # Save the refreshed credentials
                with open(TOKEN_YOUTUBE_OAUTH_PATH, 'w') as token_file:
                    token_file.write(creds.to_json())
                print(f"YouTube OAuth: Refreshed token saved to {TOKEN_YOUTUBE_OAUTH_PATH}")
            except RefreshError as e:
                print(f"YouTube OAuth: Error refreshing credentials: {e}. Need new authorization.")
                # Fall through to re-authorization by deleting bad token file
                if os.path.exists(TOKEN_YOUTUBE_OAUTH_PATH):
                    os.remove(TOKEN_YOUTUBE_OAUTH_PATH)
                creds = None # Force re-auth
            except Exception as e_refresh: # Catch other potential errors during refresh
                print(f"YouTube OAuth: Unexpected error during token refresh: {e_refresh}. Need new authorization.")
                if os.path.exists(TOKEN_YOUTUBE_OAUTH_PATH):
                    os.remove(TOKEN_YOUTUBE_OAUTH_PATH)
                creds = None # Force re-auth
        
        if not creds: # Trigger this if creds are still None (no token file, or refresh failed)
            print(f"YouTube OAuth: Credentials not found or invalid. Starting OAuth flow.")
            if not redirect_uri:
                # This happens if create_youtube_service is called by a background task without a redirect_uri
                # Or if startup calls it before redirect_uri is available from a request context.
                # For background tasks, we assume authorization has happened via a web flow earlier.
                # If called at startup without a token, it can't complete the flow without user interaction.
                msg = "YouTube OAuth: Cannot initiate authorization flow without a redirect_uri or pre-existing token. User needs to authorize via web UI first."
                print(f"ERROR: {msg}")
                if APP_STARTUP_STATUS.get("youtube_error_details") is None:
                    APP_STARTUP_STATUS["youtube_error_details"] = msg
                raise YouTubeUploaderError(msg) # Or could raise YouTubeNeedsAuthorization here if preferred for startup

            # Use client_config_dict_from_env (must be available at this point)
            if not client_config_dict_from_env: # This should be an impossible state if logic above is correct
                raise YouTubeUploaderError("Critical: YouTube client secret config from ENV VAR missing for flow initiation.")
            
            flow = InstalledAppFlow.from_client_config(client_config_dict_from_env, SCOPES_YOUTUBE)
            
            flow.redirect_uri = redirect_uri # Set the redirect URI for the web flow
            
            authorization_url, state = flow.authorization_url(
                access_type='offline', 
                prompt='consent', # Force consent screen even if previously approved, good for ensuring refresh_token
                include_granted_scopes='true'
            )
            print(f"YouTube OAuth: Authorization URL generated. State: {state}")
            # Store flow and state for the callback to use. This is tricky without a proper session.
            # For simplicity here, we can store it in a global variable or a temporary file.
            # A better way for web apps is to use a session or a short-lived cache.
            # For now, we will raise an exception that the calling route can catch.
            # This exception will be caught by the /authorize_youtube route in practice.
            # If create_youtube_service is called directly by startup or background task without a redirect_uri,
            # it will hit the "Cannot initiate authorization flow without a redirect_uri" error before this.
            raise YouTubeNeedsAuthorization(authorization_url)

    if not creds or not creds.valid:
        # If after all attempts, creds are still not valid, raise error.
        msg = "YouTube OAuth: Credentials still not valid after attempts (or token file is bad/missing and no interactive flow was triggered)."
        if APP_STARTUP_STATUS.get("youtube_error_details") is None:
            APP_STARTUP_STATUS["youtube_error_details"] = msg
        raise YouTubeUploaderError(msg)

    try: 
        service = build(API_SERVICE_NAME, API_VERSION, credentials=creds)
        print(f"YouTube Service Factory: Service client created successfully using OAuth User Consent.")
        return service
    except Exception as e: 
        raise YouTubeUploaderError(f"Failed to build YouTube service with OAuth User Consent: {e}")

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

        # Background task should create its own gdrive client instance
        print("BACKGROUND TASK: YouTube: Creating task-specific GDrive client.")
        gdrive_service = gdrive.create_gdrive_service()
        
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

        # Background task should use the shared OAuth client if available and valid
        # It cannot initiate a new OAuth flow itself.
        import config as app_config
        youtube_service = app_config.YOUTUBE_SERVICE_CLIENT
        if not youtube_service:
            # Check if it just needs re-init from token after an auth callback updated the token file
            # but before the main app YOUTUBE_SERVICE_CLIENT was updated by that callback.
            # This is a bit of a race condition mitigation.
            print("BACKGROUND TASK: YouTube: Shared YouTube client not found in config. Attempting to create from token.")
            try:
                # Try to create from token; redirect_uri=None means it won't start a new flow.
                youtube_service = create_youtube_service(redirect_uri=None) 
                if not youtube_service:
                    raise YouTubeUploaderError("Failed to create YouTube service from token for background task.")
                # If successful, this instance is local to the task, does not update shared config.YOUTUBE_SERVICE_CLIENT here.
            except Exception as task_auth_e:
                raise YouTubeUploaderError(f"YouTube client not available or not authorized for background task. Please authorize via UI. Error: {task_auth_e}")
        
        print("BACKGROUND TASK: YouTube: Using OAuth YouTube client for upload.")

        # Test API call to list channels (already part of check_youtube_service, but can be a pre-flight here too)
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
