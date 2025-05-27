import os
import json 
import sys
from google.oauth2.service_account import Credentials as ServiceAccountCredentials
from google.oauth2.credentials import Credentials as UserCredentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload
import subprocess # For dummy video creation in test

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from config import (
    GOOGLE_AUTH_METHOD,
    GOOGLE_SERVICE_ACCOUNT_INFO,
    GOOGLE_SERVICE_ACCOUNT_FILE_PATH,
    GOOGLE_CLIENT_SECRET_PATH_CONFIG
)
# from utils import update_recipe_status # Route handler updates db.json

# --- YouTube Data API v3 Setup ---
SCOPES_YOUTUBE = ["https://www.googleapis.com/auth/youtube.upload"]
OAUTH_TOKEN_YOUTUBE_PATH = os.path.join(os.path.dirname(__file__), '..', 'token_youtube.json')
API_SERVICE_NAME = 'youtube'
API_VERSION = 'v3'

class YouTubeUploaderError(Exception):
    """Custom exception for YouTube Uploader service errors."""
    pass

def get_youtube_service():
    """Authenticates and returns a YouTube Data API service client.
       Prioritizes Service Account, then OAuth2 InstalledAppFlow.
    """
    creds = None
    auth_method_to_log = "Unknown"

    if GOOGLE_AUTH_METHOD == "SERVICE_ACCOUNT_JSON_STRING" and GOOGLE_SERVICE_ACCOUNT_INFO:
        auth_method_to_log = "Service Account (JSON string)"
        print(f"YouTube Auth: Attempting with {auth_method_to_log}")
        try:
            # For service accounts to act on behalf of a channel (impersonation for YouTube API),
            # the service account needs to be a manager of the channel OR use a subject (delegated user).
            # If the service account itself owns a channel (less common for this use case) or has direct upload rights somehow.
            # For user channel uploads, OAuth is often more direct unless domain-wide delegation is set up with a subject.
            # Assuming for now the SA has direct rights or you are managing a brand account where SA can be added.
            creds = ServiceAccountCredentials.from_service_account_info(
                GOOGLE_SERVICE_ACCOUNT_INFO, scopes=SCOPES_YOUTUBE
            )
            # If you need to act on behalf of a specific user (channel owner) using SA (domain-wide delegation):
            # creds = creds.with_subject('user_email@example.com')
            print(f"YouTube Auth: Successfully obtained credentials via {auth_method_to_log}.")
        except Exception as e:
            print(f"ERROR: YouTube Auth: Failed to load Service Account from JSON string: {e}")
            raise YouTubeUploaderError(f"Service Account (JSON string) credential error: {e}")

    elif GOOGLE_AUTH_METHOD == "SERVICE_ACCOUNT_FILE_PATH" and GOOGLE_SERVICE_ACCOUNT_FILE_PATH:
        auth_method_to_log = f"Service Account (file path: {GOOGLE_SERVICE_ACCOUNT_FILE_PATH})"
        print(f"YouTube Auth: Attempting with {auth_method_to_log}")
        try:
            creds = ServiceAccountCredentials.from_service_account_file(
                GOOGLE_SERVICE_ACCOUNT_FILE_PATH, scopes=SCOPES_YOUTUBE
            )
            # creds = creds.with_subject('user_email@example.com') # If using subject for delegation
            print(f"YouTube Auth: Successfully obtained credentials via {auth_method_to_log}.")
        except Exception as e:
            print(f"ERROR: YouTube Auth: Failed to load Service Account from file: {e}")
            raise YouTubeUploaderError(f"Service Account (file path) credential error: {e}")

    elif GOOGLE_AUTH_METHOD == "OAUTH_CLIENT_SECRET" and GOOGLE_CLIENT_SECRET_PATH_CONFIG:
        auth_method_to_log = f"OAuth 2.0 Client Secret (file: {GOOGLE_CLIENT_SECRET_PATH_CONFIG})"
        print(f"YouTube Auth: Attempting with {auth_method_to_log} (user consent flow if no token).")
        if os.path.exists(OAUTH_TOKEN_YOUTUBE_PATH):
            try:
                with open(OAUTH_TOKEN_YOUTUBE_PATH, 'r') as token_file:
                    creds = UserCredentials.from_authorized_user_info(json.load(token_file), SCOPES_YOUTUBE)
                print(f"YouTube Auth: Loaded OAuth token from {OAUTH_TOKEN_YOUTUBE_PATH}")
            except Exception as e:
                print(f"Warning: YouTube Auth: Error loading OAuth token: {e}. Will attempt re-auth.")
                creds = None
        
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                try:
                    creds.refresh(Request())
                    print("YouTube Auth: OAuth token refreshed successfully.")
                except Exception as e:
                    print(f"Warning: YouTube Auth: Failed to refresh OAuth token: {e}. Re-auth needed.")
                    creds = None 
            else:
                try:
                    flow = InstalledAppFlow.from_client_secrets_file(GOOGLE_CLIENT_SECRET_PATH_CONFIG, SCOPES_YOUTUBE)
                    print("YouTube Auth: OAuth authentication required. Please follow browser prompts.")
                    creds = flow.run_local_server(port=0)
                except Exception as e:
                    print(f"ERROR: YouTube Auth: Failed OAuth flow: {e}")
                    raise YouTubeUploaderError(f"OAuth flow error: {e}")
            
            if creds:
                try:
                    with open(OAUTH_TOKEN_YOUTUBE_PATH, 'w') as token_file:
                        token_file.write(creds.to_json())
                    print(f"YouTube Auth: OAuth token saved to {OAUTH_TOKEN_YOUTUBE_PATH}")
                except Exception as e:
                    print(f"Warning: YouTube Auth: Error saving OAuth token: {e}")
    else:
        msg = "YouTube Auth: No valid Google API credential method configured."
        print(f"ERROR: {msg}")
        raise YouTubeUploaderError(msg)

    if not creds:
        msg = f"YouTube Auth: Failed to obtain credentials using method: {auth_method_to_log}."
        print(f"ERROR: {msg}")
        raise YouTubeUploaderError(msg)

    try:
        service = build(API_SERVICE_NAME, API_VERSION, credentials=creds)
        print("YouTube Data API service client created successfully.")
        return service
    except Exception as e:
        msg = f"Failed to build YouTube service client: {e}"
        print(f"ERROR: {msg}")
        raise YouTubeUploaderError(msg)

# upload_video_to_youtube function remains the same, as it calls get_youtube_service()
# which now handles the auth method selection.

def upload_video_to_youtube(video_file_path: str, metadata: dict, privacy_status: str = "private") -> str | None:
    print(f"Attempting to upload video to YouTube: {video_file_path}")
    print(f"Metadata: Title='{metadata.get('title')}', Tags='{metadata.get('tags')}', Privacy='{privacy_status}'")
    if not os.path.exists(video_file_path): raise YouTubeUploaderError(f"Video file not found: {video_file_path}")
    if not metadata.get('title'): raise YouTubeUploaderError("Video title missing.")
    try:
        youtube_service = get_youtube_service()
        request_body = {
            'snippet': {
                'title': metadata.get('title'), 'description': metadata.get('description', ''),
                'tags': metadata.get('tags', []), 'categoryId': '22'
            },
            'status': {'privacyStatus': privacy_status, 'selfDeclaredMadeForKids': False}
        }
        print("Initiating YouTube video upload...")
        media_file = MediaFileUpload(video_file_path, chunksize=-1, resumable=True)
        response_upload = youtube_service.videos().insert(part='snippet,status', body=request_body, media_body=media_file).execute()
        video_id = response_upload.get('id')
        youtube_url = f"https://www.youtube.com/watch?v={video_id}"
        print(f"YouTube upload successful! Video ID: {video_id}, URL: {youtube_url}")
        return youtube_url
    except HttpError as e:
        error_content = e.content.decode('utf-8') if e.content else 'No details.'
        msg = f"HTTP error {e.resp.status} during YouTube upload: {error_content[:500]}"
        if "quotaExceeded" in error_content: msg = "YouTube API quota exceeded."
        print(f"ERROR: {msg}")
        raise YouTubeUploaderError(msg)
    except YouTubeUploaderError: raise
    except Exception as e:
        msg = f"Unexpected error during YouTube upload: {e}"
        print(f"ERROR: {msg}")
        raise YouTubeUploaderError(msg)

if __name__ == '__main__':
    print("Testing YouTube Uploader Service Module (Service Account or OAuth)...")
    print(f"Configured Google Auth Method: {GOOGLE_AUTH_METHOD}")
    # ... (rest of the __main__ block for testing can remain similar, 
    # as it primarily tests upload_video_to_youtube which in turn calls the new get_youtube_service)
    # For dummy video creation, it would need get_ffmpeg_tool_path or similar helper.

    try:
        # For testing SA, ensure GOOGLE_SERVICE_ACCOUNT_... is set in .env
        # For testing OAuth, ensure GOOGLE_CLIENT_SECRET_JSON_FILENAME is set and SA vars are not.
        # The test will use the auth method determined by config.py
        
        from config import MERGED_DIR as TEST_MERGED_DIR # For dummy video path
        if not os.path.exists(TEST_MERGED_DIR): os.makedirs(TEST_MERGED_DIR)

        test_video_filename = "youtube_SA_oauth_test.mp4"
        test_video_filepath = os.path.join(TEST_MERGED_DIR, test_video_filename)
        ffmpeg_available_for_test = False
        try:
            # Minimal import for this test block
            from services.video_editor import get_ffmpeg_tool_path as get_ffmpeg_path_for_test
            ffmpeg_cmd_for_test = get_ffmpeg_path_for_test("ffmpeg")
            ffmpeg_available_for_test = True
        except (ImportError, VideoEditingError):
            print("FFmpeg not found or video_editor service not fully available for dummy video creation. Will use text placeholder.")
        
        if ffmpeg_available_for_test:
            creationflags_test = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            subprocess.run([
                ffmpeg_cmd_for_test, '-y', '-f', 'lavfi', '-i', 'testsrc=duration=3:size=640x360:rate=15',
                '-f', 'lavfi', '-i', 'anullsrc', '-t', '3', '-c:v', 'libx264', '-preset', 'ultrafast',
                '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-strict', '-2', test_video_filepath
            ], check=True, capture_output=True, text=True, creationflags=creationflags_test)
            print(f"Created dummy FFmpeg test video: {test_video_filepath}")
        else:
            with open(test_video_filepath, 'w') as f: f.write("Test video placeholder for YouTube SA/OAuth test.")
            print(f"Created text placeholder for test video: {test_video_filepath}")

        test_metadata = {
            "title": f"Barged API Test (via {GOOGLE_AUTH_METHOD}) - SA/OAuth Test",
            "description": "Testing YouTube upload using the configured Google auth method.",
            "tags": ["test", GOOGLE_AUTH_METHOD.lower()]
        }
        youtube_link = upload_video_to_youtube(test_video_filepath, test_metadata, privacy_status="unlisted")
        if youtube_link: print(f"\nSUCCESS: YouTube Upload test completed! Link: {youtube_link}")
        else: print("\nFAILURE: YouTube Upload test failed (no link).")
    except YouTubeUploaderError as e: print(f"YOUTUBE UPLOADER ERROR: {e}")
    except Exception as e: print(f"UNEXPECTED TEST ERROR: {e}")
    finally:
        # if os.path.exists(test_video_filepath): 
        # try: os.remove(test_video_filepath); print(f"Cleaned up: {test_video_filepath}")
        # except Exception as e: print(f"Could not clean: {e}")
        print("\nYouTube Uploader Service Module testing finished.")
