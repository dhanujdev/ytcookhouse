import os
import json 
import sys
import tempfile # For temporary local video file
from google.oauth2.service_account import Credentials as ServiceAccountCredentials
from google.oauth2.credentials import Credentials as UserCredentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import buildrom googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload
import subprocess # Only for __main__ test dummy video

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from config import (
    GOOGLE_AUTH_METHOD,
    GOOGLE_SERVICE_ACCOUNT_INFO,
    GOOGLE_SERVICE_ACCOUNT_FILE_PATH,
    GOOGLE_CLIENT_SECRET_PATH_CONFIG,
    # MERGED_DIR as LOCAL_TEMP_MERGED_DIR # For temp downloaded video storage
)
from utils import update_recipe_status, get_recipe_status # get_recipe_status for GDrive ID
from services import gdrive # Import gdrive service

SCOPES_YOUTUBE = ["https://www.googleapis.com/auth/youtube.upload"]
OAUTH_TOKEN_YOUTUBE_PATH = os.path.join(os.path.dirname(__file__), '..', 'token_youtube.json')
API_SERVICE_NAME = 'youtube'
API_VERSION = 'v3'

class YouTubeUploaderError(Exception):
    pass

def get_youtube_service(): # This function remains largely the same
    creds = None
    auth_method_to_log = "Unknown"
    if GOOGLE_AUTH_METHOD == "SERVICE_ACCOUNT_JSON_STRING" and GOOGLE_SERVICE_ACCOUNT_INFO:
        auth_method_to_log = "Service Account (JSON string)"
        try: creds = ServiceAccountCredentials.from_service_account_info(GOOGLE_SERVICE_ACCOUNT_INFO, scopes=SCOPES_YOUTUBE)
        except Exception as e: raise YouTubeUploaderError(f"SA JSON cred error: {e}")
    elif GOOGLE_AUTH_METHOD == "SERVICE_ACCOUNT_FILE_PATH" and GOOGLE_SERVICE_ACCOUNT_FILE_PATH:
        auth_method_to_log = f"Service Account (file path: {GOOGLE_SERVICE_ACCOUNT_FILE_PATH})"
        try: creds = ServiceAccountCredentials.from_service_account_file(GOOGLE_SERVICE_ACCOUNT_FILE_PATH, scopes=SCOPES_YOUTUBE)
        except Exception as e: raise YouTubeUploaderError(f"SA file cred error: {e}")
    elif GOOGLE_AUTH_METHOD == "OAUTH_CLIENT_SECRET" and GOOGLE_CLIENT_SECRET_PATH_CONFIG:
        auth_method_to_log = f"OAuth 2.0 Client Secret (file: {GOOGLE_CLIENT_SECRET_PATH_CONFIG})"
        if os.path.exists(OAUTH_TOKEN_YOUTUBE_PATH):
            try: creds = UserCredentials.from_authorized_user_info(json.load(open(OAUTH_TOKEN_YOUTUBE_PATH, 'r')), SCOPES_YOUTUBE)
            except Exception: creds = None
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token: 
                try: creds.refresh(Request())
                except Exception: creds = None
            else:
                try:
                    flow = InstalledAppFlow.from_client_secrets_file(GOOGLE_CLIENT_SECRET_PATH_CONFIG, SCOPES_YOUTUBE)
                    creds = flow.run_local_server(port=0)
                except Exception as e: raise YouTubeUploaderError(f"OAuth flow error: {e}")
            if creds: 
                with open(OAUTH_TOKEN_YOUTUBE_PATH, 'w') as token_file: token_file.write(creds.to_json())
    else: raise YouTubeUploaderError("No valid Google API credential method configured.")
    if not creds: raise YouTubeUploaderError(f"Failed to get credentials via {auth_method_to_log}.")
    try: 
        service = build(API_SERVICE_NAME, API_VERSION, credentials=creds)
        print(f"BACKGROUND TASK: YouTube Auth: Using {auth_method_to_log}. Client created.")
        return service
    except Exception as e: raise YouTubeUploaderError(f"Failed to build YouTube service: {e}")

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

        if local_temp_video__path and os.path.exists(local_temp_video_path):
            try:
                os.remove(local_temp_video_path)
                print(f"BACKGROUND TASK: YouTube: Cleaned local temp video: {local_temp_video_path}")
            except Exception as e_clean:
                print(f"BACKGROUND TASK: YouTube: WARN Failed to clean local temp video {local_temp_video_path}: {e_clean}")
        
    # No explicit return needed by background task manager in routes if it only checks DB status

# __main__ block needs rework to align with GDrive based file handling for tests
