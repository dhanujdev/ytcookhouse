import os
import json # For loading metadata if passed as filepath, and for db.json interaction (though latter is in utils)
import sys
import pickle # For token storage, an alternative to json
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
# Use GOOGLE_CLIENT_SECRET_PATH as it's the common path for all Google API client secrets
from config import GOOGLE_CLIENT_SECRET_PATH 
# from utils import update_recipe_status # The route handler will update db.json

# --- YouTube Data API v3 Setup ---
SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
# Token file will be saved in the project root (barged_api/)
TOKEN_YOUTUBE_PATH = os.path.join(os.path.dirname(__file__), '..', 'token_youtube.json') # Using .json for consistency
API_SERVICE_NAME = 'youtube'
API_VERSION = 'v3'

class YouTubeUploaderError(Exception):
    """Custom exception for YouTube Uploader service errors."""
    pass

def get_youtube_service():
    """Authenticates (OAuth2) and returns a YouTube Data API service client."""
    creds = None
    if os.path.exists(TOKEN_YOUTUBE_PATH):
        try:
            with open(TOKEN_YOUTUBE_PATH, 'r') as token_file:
                # Uses google.oauth2.credentials.Credentials.from_authorized_user_file if file is simple token
                # or from_authorized_user_info if it's a richer dict. For JSON, this should work.
                creds = Credentials.from_authorized_user_info(json.load(token_file), SCOPES)
        except json.JSONDecodeError:
            creds = None
        except Exception as e:
            print(f"Error loading YouTube token from {TOKEN_YOUTUBE_PATH}: {e}. Attempting re-authentication.")
            creds = None

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                print("YouTube token refreshed successfully.")
            except Exception as e:
                print(f"Failed to refresh YouTube token: {e}. Re-authentication needed.")
                creds = None # Force re-authentication
        else:
            if not GOOGLE_CLIENT_SECRET_PATH or not os.path.exists(GOOGLE_CLIENT_SECRET_PATH):
                msg = f"YouTube/Google client secrets file not found at {GOOGLE_CLIENT_SECRET_PATH}. Check GOOGLE_CLIENT_SECRET_JSON_FILENAME in .env."
                print(f"ERROR: {msg}")
                raise YouTubeUploaderError(msg)
            try:
                flow = InstalledAppFlow.from_client_secrets_file(GOOGLE_CLIENT_SECRET_PATH, SCOPES)
                print("YouTube API authentication required. Please follow the browser prompts.")
                print("If running on a headless server, this step will fail. Authorize locally and ensure token_youtube.json is present.")
                creds = flow.run_local_server(port=0) # Opens browser for auth
            except Exception as e:
                msg = f"Failed to run YouTube authentication flow: {e}"
                print(f"ERROR: {msg}")
                raise YouTubeUploaderError(msg)
        
        # Save the credentials for the next run
        try:
            with open(TOKEN_YOUTUBE_PATH, 'w') as token_file:
                # Save in a format that Credentials.from_authorized_user_info can read
                token_file.write(creds.to_json())
            print(f"YouTube token saved to {TOKEN_YOUTUBE_PATH}")
        except Exception as e:
            print(f"Error saving YouTube token to {TOKEN_YOUTUBE_PATH}: {e}")
            # Not raising an error here, as the service might still work for the current session

    if not creds:
        msg = "Failed to obtain YouTube credentials after all attempts."
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

def upload_video_to_youtube(video_file_path: str, metadata: dict, privacy_status: str = "private") -> str | None:
    """
    Uploads a video to YouTube with the given metadata.

    Args:
        video_file_path: Absolute path to the video file to upload.
        metadata: Dictionary containing title, description, tags.
                  Example: {"title": "My Video", "description": "Cool video", "tags": ["test", "video"]}
        privacy_status: "public", "private", or "unlisted". Defaults to "private".

    Returns:
        The YouTube video URL if successful, None otherwise.
    
    Raises:
        YouTubeUploaderError: If there are issues with credentials or the upload.
    """
    print(f"Attempting to upload video to YouTube: {video_file_path}")
    print(f"Metadata: Title='{metadata.get('title')}', Tags='{metadata.get('tags')}', Privacy='{privacy_status}'")

    if not os.path.exists(video_file_path):
        raise YouTubeUploaderError(f"Video file not found at: {video_file_path}")
    if not metadata.get('title'): # Title is mandatory
        raise YouTubeUploaderError("Video title is missing in metadata.")

    try:
        youtube_service = get_youtube_service()
        
        request_body = {
            'snippet': {
                'title': metadata.get('title'),
                'description': metadata.get('description', ''),
                'tags': metadata.get('tags', []),
                'categoryId': '22' # Default: People & Blogs. 
                                  # See https://developers.google.com/youtube/v3/docs/videoCategories/list
                                  # Common for cooking: '28' (Science & Technology, if educational) or '22'
            },
            'status': {
                'privacyStatus': privacy_status,
                'selfDeclaredMadeForKids': False, # Adjust if necessary
                # 'publishAt': 'YYYY-MM-DDThh:mm:ss.sZ' # For scheduled uploads
            }
        }

        print("Initiating YouTube video upload...")
        media_file = MediaFileUpload(video_file_path, chunksize=-1, resumable=True)

        response_upload = youtube_service.videos().insert(
            part='snippet,status',
            body=request_body,
            media_body=media_file
        ).execute()

        video_id = response_upload.get('id')
        youtube_url = f"https://www.youtube.com/watch?v={video_id}"
        print(f"YouTube upload successful! Video ID: {video_id}, URL: {youtube_url}")
        return youtube_url

    except HttpError as e:
        error_content = e.content.decode('utf-8') if e.content else 'No further error details.'
        msg = f"An HTTP error {e.resp.status} occurred during YouTube upload: {error_content}"
        print(f"ERROR: {msg}")
        # Check for quotaExceeded error
        if "quotaExceeded" in error_content:
            msg = "YouTube API quota exceeded. Please try again later or request a higher quota."
            print(f"ERROR DETAIL: {msg}")
            raise YouTubeUploaderError(msg)
        raise YouTubeUploaderError(f"HTTP error {e.resp.status} during upload: {error_content[:500]}") # Truncate long errors
    except YouTubeUploaderError: # Re-raise specific errors from get_youtube_service
        raise
    except Exception as e:
        msg = f"An unexpected error occurred during YouTube upload: {e}"
        print(f"ERROR: {msg}")
        raise YouTubeUploaderError(msg)

# Example usage (for testing this module directly):
if __name__ == '__main__':
    print("Testing YouTube Uploader Service Module...")
    # Ensure .env is populated with GOOGLE_CLIENT_SECRET_JSON_FILENAME 
    # and the JSON file is present in barged_api/ root.
    # token_youtube.json will be created/used in barged_api/ if auth is needed.

    print(f"Using Google Client Secrets Path: {GOOGLE_CLIENT_SECRET_PATH}")

    # --- Create a dummy video file and metadata for testing ---
    # This assumes MERGED_DIR is accessible and correctly configured in config.py
    # We need to import MERGED_DIR for this test block
    try:
        from config import MERGED_DIR as TEST_MERGED_DIR
        if not os.path.exists(TEST_MERGED_DIR):
            os.makedirs(TEST_MERGED_DIR)
            print(f"Created test merged directory: {TEST_MERGED_DIR}")

        test_video_filename = "youtube_upload_test_video.mp4"
        test_video_filepath = os.path.join(TEST_MERGED_DIR, test_video_filename)
        
        # Create a small, valid MP4 file for testing using FFmpeg if possible
        # This avoids needing a large pre-existing file for the test.
        try:
            ffmpeg_cmd_path = get_ffmpeg_tool_path("ffmpeg") # Check if ffmpeg is available
            # lavfi to create a 5-second test pattern video with silent audio
            ffmpeg_create_test_video_cmd = [
                ffmpeg_cmd_path, '-y',
                '-f', 'lavfi', '-i', 'testsrc=duration=5:size=1280x720:rate=30', # Video
                '-f', 'lavfi', '-i', 'anullsrc=channel_layout=stereo:sample_rate=44100', # Audio (silent)
                '-t', '5', # Duration
                '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p',
                '-c:a', 'aac', '-strict', '-2',
                test_video_filepath
            ]
            print(f"Creating dummy test video using FFmpeg: {' '.join(ffmpeg_create_test_video_cmd)}")
            creationflags_ffmpeg_test = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            subprocess.run(ffmpeg_create_test_video_cmd, check=True, capture_output=True, text=True, creationflags=creationflags_ffmpeg_test)
            print(f"Dummy test video created: {test_video_filepath}")
        except Exception as e_ffmpeg_test:
            print(f"Could not create dummy FFmpeg test video (falling back to simple text file): {e_ffmpeg_test}")
            print("Please ensure FFmpeg is in PATH to create a valid test video for upload.")
            # Fallback: create a simple text file if ffmpeg failed, upload will likely fail but tests API auth.
            with open(test_video_filepath, 'w') as f:
                f.write("This is a dummy test video file for YouTube uploader test.")
            print(f"Created placeholder (non-video) file for testing: {test_video_filepath}")

        test_metadata = {
            "title": "Barged API Test Upload - My Awesome Video Test",
            "description": "This is a test video uploaded by the Barged API. \nTesting YouTube Data API v3 integration. #BargedAPI #TestUpload #Python",
            "tags": ["barged api", "python", "youtube api", "test upload", "development", "awesome video"],
        }
        test_privacy = "unlisted" # Use "private" or "unlisted" for tests

        print(f"\nAttempting to upload video: '{test_video_filepath}' with title: '{test_metadata['title']}'")
        youtube_link = upload_video_to_youtube(test_video_filepath, test_metadata, privacy_status=test_privacy)
        
        if youtube_link:
            print(f"\nSUCCESS: YouTube Upload test completed!")
            print(f"Video Link ({test_privacy}): {youtube_link}")
            # You can now go to this link to verify the upload and metadata.
            # Remember to delete the test video from your YouTube account afterwards.
        else:
            # This case should ideally be covered by exceptions
            print("\nFAILURE: YouTube Upload test failed (no link returned). Check logs.")

    except YouTubeUploaderError as e:
        print(f"YOUTUBE UPLOADER ERROR during test: {e}")
    except ImportError:
        print("Could not import TEST_MERGED_DIR from config. Ensure config.py is correct.")
    except Exception as e:
        print(f"UNEXPECTED ERROR during YouTube Uploader test: {e}")
    finally:
        # Clean up the dummy test video file if it was created by this script
        # if 'test_video_filepath' in locals() and os.path.exists(test_video_filepath) and test_video_filename == "youtube_upload_test_video.mp4":
        #     try:
        #         os.remove(test_video_filepath)
        #         print(f"Cleaned up dummy test video: {test_video_filepath}")
        #     except Exception as e_clean_test_vid:
        #         print(f"Warning: Could not clean up dummy test video {test_video_filepath}: {e_clean_test_vid}")
        print("\nYouTube Uploader Service Module testing finished.")
