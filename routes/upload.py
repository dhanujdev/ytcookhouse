from fastapi import APIRouter, Request, Form, BackgroundTasks, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
import os
import json
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from services import gdrive, video_editor, gemini, youtube_uploader
from services.gemini import GeminiServiceError
from services.youtube_uploader import YouTubeUploaderError
# Import METADATA_TEMP_DIR instead of OUTPUT_DIR, and TEMP_PROCESSING_BASE_DIR for relative paths
from config import TEMP_PROCESSING_BASE_DIR, RAW_DIR, METADATA_TEMP_DIR 
from utils import update_recipe_status, get_recipe_status, get_all_recipes_from_db


router = APIRouter()

# Remove local templates definition, will import from main
# TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), '..', 'templates')
# templates = Jinja2Templates(directory=TEMPLATES_DIR)
from templating import templates # Import the templates instance from templating.py

# --- Helper function to trigger next step in the background ---
def trigger_next_background_task(background_tasks: BackgroundTasks, recipe_id: str):
    recipe_data = get_recipe_status(recipe_id)
    if not recipe_data:
        print(f"BACKGROUND_TRIGGER: Recipe {recipe_id} not found in DB. Cannot trigger next task.")
        return

    current_status = recipe_data.get("status")
    recipe_name_orig = recipe_data.get("name", "Unknown Recipe")
    # More precise debug for status check
    print(f"BACKGROUND_TRIGGER: For Recipe ID '{recipe_id}' ('{recipe_name_orig}'), status from DB is '{current_status}'. Type: {type(current_status)}")
    
    normalized_status = str(current_status).strip().upper()

    if normalized_status == "DOWNLOADED" or normalized_status == "MERGE_FAILED": # Retry merge if it previously failed
        if normalized_status == "MERGE_FAILED":
            print(f"BACKGROUND_TRIGGER: Retrying MERGE for '{recipe_id}' which previously failed.")
        else:
            print(f"BACKGROUND_TRIGGER: Condition normalized_status == 'DOWNLOADED' met for '{recipe_id}'.")
        
        relative_clips_path_from_db = recipe_data.get("raw_clips_path")
        print(f"BACKGROUND_TRIGGER: Attempting MERGE. Full Recipe Data from DB: {recipe_data}")
        print(f"BACKGROUND_TRIGGER: Relative raw_clips_path from DB for '{recipe_id}': '{relative_clips_path_from_db}'")
        
        absolute_clips_path = None
        path_exists = False
        if relative_clips_path_from_db and isinstance(relative_clips_path_from_db, str):
            absolute_clips_path = os.path.join(TEMP_PROCESSING_BASE_DIR, relative_clips_path_from_db)
            path_exists = os.path.exists(absolute_clips_path)
            print(f"BACKGROUND_TRIGGER: For '{recipe_id}', absolute_clips_path is '{absolute_clips_path}'. os.path.exists evaluation: {path_exists}")
        else:
            print(f"BACKGROUND_TRIGGER: For '{recipe_id}', relative_clips_path_from_db is None or not a string: '{relative_clips_path_from_db}'")
        
        if relative_clips_path_from_db and path_exists: 
            print(f"BACKGROUND_TRIGGER: Triggering MERGING for {recipe_id} using relative path '{relative_clips_path_from_db}' (resolved to '{absolute_clips_path}')")
            update_recipe_status(recipe_id=recipe_id, name=recipe_name_orig, status="MERGING")
            # Pass background_tasks object as the first argument to the video_editor function
            background_tasks.add_task(video_editor.merge_videos_and_replace_audio, background_tasks, relative_clips_path_from_db, recipe_id, recipe_name_orig)
            print(f"BACKGROUND_TRIGGER: MERGING task for {recipe_id} (which includes auto-trigger for metadata) ADDED to background_tasks.")
        else:
            err_msg = f"Automated MERGE trigger for '{recipe_name_orig}' ({recipe_id}) failed. Relative path '{relative_clips_path_from_db}' (resolved to '{absolute_clips_path}', exists: {path_exists}) not valid."
            print(f"BACKGROUND_TRIGGER: ERROR - {err_msg}")
            update_recipe_status(recipe_id=recipe_id, name=recipe_name_orig, status="MERGE_FAILED", error_message=err_msg)
    
    elif normalized_status == "MERGED" or normalized_status == "METADATA_FAILED": # Retry metadata if it previously failed
        if normalized_status == "METADATA_FAILED":
            print(f"BACKGROUND_TRIGGER: Retrying METADATA_GENERATION for '{recipe_id}' which previously failed.")
        else:
            print(f"BACKGROUND_TRIGGER: Condition normalized_status == 'MERGED' met for '{recipe_id}'.")

        # The gemini service function generate_youtube_metadata_from_video_info does not directly use a local path for video content anymore.
        # It primarily uses recipe_db_id to fetch GDrive IDs and other info from the database.
        # Check if merged_video_gdrive_id exists, as Gemini might benefit from knowing it's available, even if not directly using the file content for this prompt.
        merged_video_gdrive_id = recipe_data.get("merged_video_gdrive_id")
        if not merged_video_gdrive_id:
            err_msg = f"merged_video_gdrive_id not found in DB for recipe '{recipe_name_orig}' ({recipe_id}). Cannot trigger METADATA_GENERATION."
            print(f"BACKGROUND_TRIGGER: ERROR - {err_msg}")
            update_recipe_status(recipe_id=recipe_id, name=recipe_name_orig, status="METADATA_FAILED", error_message=err_msg)
            return # Stop further processing for this path
            
        print(f"BACKGROUND_TRIGGER: Triggering METADATA_GENERATION for {recipe_id}. Full Recipe Data: {recipe_data}")
        update_recipe_status(recipe_id=recipe_id, name=recipe_name_orig, status="GENERATING_METADATA")
        # When auto-triggering, custom_prompt_str is None, so gemini service uses its default prompt.
        background_tasks.add_task(gemini.generate_youtube_metadata_from_video_info, recipe_db_id=recipe_id, recipe_name_orig=recipe_name_orig, custom_prompt_str=None)
        print(f"BACKGROUND_TRIGGER: METADATA_GENERATION task for {recipe_id} ADDED to background_tasks.")
        # The stray 'else' block that caused the SyntaxError has been removed.

    elif normalized_status == "METADATA_GENERATED": # Use normalized_status here
        # This status means it's ready for preview. No automatic background task from here.
        # The user will initiate YouTube upload from the preview page.
        print(f"BACKGROUND_TRIGGER: Recipe {recipe_id} is METADATA_GENERATED. Ready for preview and manual YouTube upload trigger.")
        update_recipe_status(recipe_id=recipe_id, name=recipe_name_orig, status="READY_FOR_PREVIEW")

    # Add more conditions if there are other auto-triggered steps

# --- YouTube OAuth Routes ---

# Store the flow object globally in this module for the callback to access
# This is a simplification for a single-user local application.
# In a multi-user or more robust scenario, use a session or a more secure state management.
_youtube_oauth_flow = None

@router.get("/authorize_youtube", name="authorize_youtube_route")
async def authorize_youtube(request: Request):
    global _youtube_oauth_flow
    from config import CLIENT_SECRET_YOUTUBE_PATH
    from services.youtube_uploader import SCOPES_YOUTUBE # Import from correct location
    from google_auth_oauthlib.flow import Flow # Use Flow for web apps

    if not os.path.exists(CLIENT_SECRET_YOUTUBE_PATH):
        raise HTTPException(status_code=500, detail="YouTube client secret file not found.")

    # The redirect_uri must match one of the "Authorized redirect URIs" in your Google Cloud Console
    redirect_uri = request.url_for('oauth2callback_youtube_route')
    
    _youtube_oauth_flow = Flow.from_client_secrets_file(
        CLIENT_SECRET_YOUTUBE_PATH,
        scopes=SCOPES_YOUTUBE,
        redirect_uri=redirect_uri
    )
    
    authorization_url, state = _youtube_oauth_flow.authorization_url(
        access_type='offline',
        prompt='consent', # Ensures refresh token is granted
        include_granted_scopes='true'
    )
    # Store state in session if available, or handle appropriately
    # For now, we rely on the flow object being in memory (simplification for local app)
    # request.session['youtube_oauth_state'] = state # If using Starlette sessions
    print(f"Redirecting to YouTube for authorization: {authorization_url}")
    return RedirectResponse(authorization_url)

@router.get("/oauth2callback", name="oauth2callback_youtube_route")
async def oauth2callback_youtube(request: Request, code: str = None, state: str = None, error: str = None):
    global _youtube_oauth_flow
    from config import TOKEN_YOUTUBE_OAUTH_PATH, YOUTUBE_SERVICE_CLIENT # to store client after auth
    import config as app_config # to set YOUTUBE_SERVICE_CLIENT

    if error:
        return RedirectResponse(url=f"/select_folder?error=YouTube_OAuth_Error:_{error}")
    if not code:
        return RedirectResponse(url=f"/select_folder?error=YouTube_OAuth_Error:_Missing_authorization_code")
    if not _youtube_oauth_flow:
        return RedirectResponse(url=f"/select_folder?error=YouTube_OAuth_Error:_OAuth_flow_not_initiated_or_lost_state.")

    try:
        # For web applications, you need to ensure the state matches to prevent CSRF.
        # This requires storing the state generated in /authorize_youtube and comparing it here.
        # If using request.session:
        # stored_state = request.session.pop('youtube_oauth_state', None)
        # if not stored_state or stored_state != state:
        #     raise HTTPException(status_code=400, detail="OAuth state mismatch. Possible CSRF attack.")

        _youtube_oauth_flow.fetch_token(code=code)
        creds = _youtube_oauth_flow.credentials
        
        with open(TOKEN_YOUTUBE_OAUTH_PATH, 'w') as token_file:
            token_file.write(creds.to_json())
        print(f"YouTube OAuth: Token fetched and saved to {TOKEN_YOUTUBE_OAUTH_PATH}")

        # Optionally, create and store the service client immediately
        app_config.YOUTUBE_SERVICE_CLIENT = youtube_uploader.create_youtube_service(redirect_uri=None) # Uses the new token
        app_config.APP_STARTUP_STATUS["youtube_ready"] = True
        app_config.APP_STARTUP_STATUS["youtube_error_details"] = None
        print("YouTube OAuth: Service client re-initialized with new token and marked as ready.")
        
        _youtube_oauth_flow = None # Clear the flow object

        return RedirectResponse(url="/select_folder?message=YouTube_authorization_successful.")
    except Exception as e:
        print(f"YouTube OAuth Callback Error: {e}")
        import traceback
        traceback.print_exc()
        return RedirectResponse(url=f"/select_folder?error=YouTube_OAuth_Callback_Error:_{str(e)}")

# --- Original Routes ---

@router.get("/select_folder", response_class=HTMLResponse, name="select_folder_route")
async def select_folder_page(request: Request, message: str = None, error: str = None):
    from config import APP_STARTUP_STATUS # Import the status dict
    folders_with_status = gdrive.list_folders_from_gdrive_and_db_status()
    return templates.TemplateResponse("select_folder.html", {
        "request": request, 
        "folders": folders_with_status,
        "message": message,
        "error": error,
        "config": {"APP_STARTUP_STATUS": APP_STARTUP_STATUS}  # Pass it to the template
    })

from config import TEMP_PROCESSING_BASE_DIR, RAW_DIR # Import new config vars

@router.post("/fetch_clips", name="fetch_clips_route")
async def fetch_clips_route(background_tasks: BackgroundTasks, folder_id: str = Form(...), folder_name: str = Form(...)):
    print(f"ROUTE /fetch_clips: Request for folder ID: {folder_id}, Name: {folder_name}")
    safe_folder_name = "".join(c if c.isalnum() else "_" for c in folder_name)
    
    # RAW_DIR from config is already the absolute, environment-specific path to .../raw_clips_temp/
    # download_path is the absolute path to the specific recipe's raw clips folder for the current environment
    absolute_download_path = os.path.join(RAW_DIR, safe_folder_name)

    # config.py now handles creation of RAW_DIR
    os.makedirs(absolute_download_path, exist_ok=True) # Ensure specific recipe folder exists

    # Path to be stored in DB should be relative to TEMP_PROCESSING_BASE_DIR
    relative_download_path_for_db = os.path.relpath(absolute_download_path, TEMP_PROCESSING_BASE_DIR)

    update_recipe_status(recipe_id=folder_id, name=folder_name, status="DOWNLOADING", raw_clips_path=relative_download_path_for_db) # Store relative path
    
    gdrive.download_folder_contents(folder_id, folder_name, absolute_download_path) # Pass absolute path for actual download
    
    # After download attempt, get the true status from the DB
    current_recipe_info = get_recipe_status(folder_id)
    current_status_from_db = current_recipe_info.get("status") if current_recipe_info else None

    if current_status_from_db == "DOWNLOADED":
        relative_clips_path_from_db = current_recipe_info.get("raw_clips_path") 
        if not relative_clips_path_from_db:
            error_msg = f"Failed to get relative_clips_path_from_db for {folder_name} after download. Cannot start merge."
            print(f"ERROR in fetch_clips_route: {error_msg}")
            return RedirectResponse(url=f"/select_folder?error={error_msg}", status_code=303)

        update_recipe_status(recipe_id=folder_id, name=folder_name, status="MERGING") 
        # Pass background_tasks object as the first argument to the video_editor function
        background_tasks.add_task(video_editor.merge_videos_and_replace_audio, background_tasks, relative_clips_path_from_db, folder_id, folder_name)
        msg = f"Clips for '{folder_name}' downloaded. Full processing (merge & metadata) started."
        return RedirectResponse(url=f"/select_folder?message={msg}", status_code=303)
    else:
        # If status is not DOWNLOADED, it implies a failure during download (e.g., DOWNLOAD_FAILED)
        error_msg = "Unknown error after download attempt."
        if current_recipe_info and current_recipe_info.get("error_message"):
            error_msg = current_recipe_info.get("error_message")
        elif current_status_from_db:
            error_msg = f"Download process resulted in status: {current_status_from_db}. Merge not started."
        else:
            error_msg = f"Could not retrieve status for {folder_name} after download attempt. Merge not started."
        return RedirectResponse(url=f"/select_folder?error={error_msg}", status_code=303)


@router.get("/preview/{recipe_db_id}", response_class=HTMLResponse, name="preview_recipe_route")
async def preview_video_page(request: Request, recipe_db_id: str):
    print(f"ROUTE /preview: Request for recipe ID: {recipe_db_id}")
    recipe_data = get_recipe_status(recipe_db_id)

    if not recipe_data:
        raise HTTPException(status_code=404, detail="Recipe not found in database.")

    recipe_name_orig = recipe_data.get("name", "Unknown Recipe")
    recipe_name_safe = "".join(c if c.isalnum() else "_" for c in recipe_name_orig)
    merged_video_gdrive_id = recipe_data.get("merged_video_gdrive_id")
    metadata_gdrive_id = recipe_data.get("metadata_gdrive_id")
    current_status = recipe_data.get("status")

    # Initialize variables that might be used in the final except block
    local_temp_video_for_preview = None
    local_temp_metadata_for_preview = None
    local_recipe_preview_dir = None # Initialize this one

    # Allow preview also if successfully uploaded, to see final state/link
    if current_status not in ["READY_FOR_PREVIEW", "METADATA_GENERATED", "UPLOAD_FAILED", "UPLOADED_TO_YOUTUBE"]:
        return templates.TemplateResponse("preview.html", {
            "request": request, "recipe_db_id": recipe_db_id, "recipe_name_safe": recipe_name_safe,
            "recipe_name_display": recipe_name_orig,
            "error_message": f"Recipe is currently in status '{current_status}'. Not ready for preview or upload yet."
        })

    if not merged_video_gdrive_id:
        return templates.TemplateResponse("preview.html", {"request": request, "recipe_db_id": recipe_db_id, "recipe_name_display": recipe_name_orig, "error_message": "Merged video Google Drive ID not found in DB."})
    if not metadata_gdrive_id:
        return templates.TemplateResponse("preview.html", {"request": request, "recipe_db_id": recipe_db_id, "recipe_name_display": recipe_name_orig, "error_message": "Metadata Google Drive ID not found in DB."})

    try:
        import config as app_config # Use an alias to avoid conflict if 'config' is used locally
        gdrive_service = app_config.GDRIVE_SERVICE_CLIENT
        if not gdrive_service:
            raise HTTPException(status_code=503, detail="GDrive service not available. Please try again later.")
        
        preview_temp_dir_name = f"preview_temp_{recipe_db_id}_{recipe_name_safe}" # Make more unique
        # Ensure static/preview_cache exists
        static_preview_cache_dir = os.path.join(os.path.dirname(__file__), "..", "static", "preview_cache")
        if not os.path.exists(static_preview_cache_dir): os.makedirs(static_preview_cache_dir)

        local_recipe_preview_dir = os.path.join(static_preview_cache_dir, preview_temp_dir_name)
        if not os.path.exists(local_recipe_preview_dir): os.makedirs(local_recipe_preview_dir)

        # Download metadata from GDrive to a temp file within the servable preview directory
        local_temp_metadata_filename = "metadata.json"
        local_temp_metadata_for_preview = os.path.join(local_recipe_preview_dir, local_temp_metadata_filename)
        if not gdrive.download_file_from_drive(metadata_gdrive_id, local_temp_metadata_for_preview, service=gdrive_service):
            raise FileNotFoundError("Failed to download metadata from GDrive for preview.")
        with open(local_temp_metadata_for_preview, 'r') as f_meta:
            metadata_content = json.load(f_meta)

        # Download video from GDrive to the servable preview directory
        local_temp_video_filename = f"{recipe_name_safe}_preview.mp4"
        local_temp_video_for_preview = os.path.join(local_recipe_preview_dir, local_temp_video_filename)
        if not gdrive.download_file_from_drive(merged_video_gdrive_id, local_temp_video_for_preview, service=gdrive_service):
            raise FileNotFoundError("Failed to download video from GDrive for preview.")

        video_url = f"/static/preview_cache/{preview_temp_dir_name}/{local_temp_video_filename}"

        # Get default prompt for UI
        video_path_context_for_prompt = f"Google Drive File ID: {merged_video_gdrive_id}"
        default_gemini_prompt = gemini.get_default_gemini_prompt(recipe_name_orig, video_path_context_for_prompt)
        
        return templates.TemplateResponse("preview.html", {
            "request": request, "recipe_db_id": recipe_db_id, "recipe_name_safe": recipe_name_safe,
            "recipe_name_display": recipe_name_orig, 
            "video_gdrive_id": merged_video_gdrive_id, 
            "video_url": video_url, "metadata": metadata_content,
            "current_status": current_status, 
            "default_gemini_prompt": default_gemini_prompt, # Pass default prompt
            "error_message": recipe_data.get("error_message"),
            "youtube_url": recipe_data.get("youtube_url") # Pass YouTube URL if it exists
        })
    except FileNotFoundError as fnf_e:
        return templates.TemplateResponse("preview.html", {"request": request, "recipe_db_id": recipe_db_id, "recipe_name_display": recipe_name_orig, "error_message": str(fnf_e)})
    except json.JSONDecodeError:
        return templates.TemplateResponse("preview.html", {"request": request, "recipe_db_id": recipe_db_id, "recipe_name_display": recipe_name_orig, "error_message": "Metadata file from GDrive is corrupted."})
    except Exception as e:
        # Simplistic cleanup for preview files on error. A more robust cleanup might be needed for long-running servers.
        if local_temp_video_for_preview and os.path.exists(local_temp_video_for_preview): os.remove(local_temp_video_for_preview)
        if local_temp_metadata_for_preview and os.path.exists(local_temp_metadata_for_preview): os.remove(local_temp_metadata_for_preview)
        if local_recipe_preview_dir and os.path.exists(local_recipe_preview_dir) and not os.listdir(local_recipe_preview_dir): os.rmdir(local_recipe_preview_dir)
        return templates.TemplateResponse("preview.html", {"request": request, "recipe_db_id": recipe_db_id, "recipe_name_display": recipe_name_orig, "error_message": f"Error loading preview: {str(e)}."})

@router.post("/regenerate_metadata/{recipe_db_id}", name="regenerate_metadata_route")
async def regenerate_metadata_route(request: Request, background_tasks: BackgroundTasks, recipe_db_id: str, custom_gemini_prompt: str = Form(...)):
    recipe_data = get_recipe_status(recipe_db_id)
    if not recipe_data:
        raise HTTPException(status_code=404, detail="Recipe not found")

    recipe_name_orig = recipe_data.get("name", "Unknown Recipe")
    # merged_video_path is not directly used by gemini.py anymore, it uses GDrive ID from db
    # current_status = recipe_data.get("status") # We might allow regeneration from different statuses

    # Validate prompt basic length if necessary, or trust user input for now
    if not custom_gemini_prompt or len(custom_gemini_prompt) < 50: # Arbitrary minimum length
         # Redirect back to preview page with an error
        # This requires passing all necessary data back to preview.html, or just a simple error to select_folder
        error_msg = "Custom prompt was too short or missing."
        # Ideally, redirect back to preview with the error, but that's more complex. For now:
        return RedirectResponse(url=f"/preview/{recipe_db_id}?error={error_msg}", status_code=303)


    update_recipe_status(recipe_id=recipe_db_id, name=recipe_name_orig, status="GENERATING_METADATA")
    background_tasks.add_task(
        gemini.generate_youtube_metadata_from_video_info, 
        recipe_db_id=recipe_db_id, 
        recipe_name_orig=recipe_name_orig,
        custom_prompt_str=custom_gemini_prompt
    )
    msg = f"Custom metadata generation started for '{recipe_name_orig}'. You will be redirected to preview page once done (refresh if needed)."
    # Redirect back to preview page after triggering, so user sees updates there.
    return RedirectResponse(url=f"/preview/{recipe_db_id}?message={msg}", status_code=303)


@router.post("/upload_youtube", name="upload_to_youtube_route")
async def upload_to_youtube_endpoint(background_tasks: BackgroundTasks, 
                                   request: Request, 
                                   recipe_db_id: str = Form(...),
                                   video_gdrive_id: str = Form(...), # Expecting GDrive ID from form
                                   title: str = Form(...),
                                   description: str = Form(...),
                                   tags: str = Form(...)
                                   ):
    recipe_info = get_recipe_status(recipe_db_id)
    recipe_name_orig = recipe_info.get("name", "Recipe") if recipe_info else "Recipe"
    print(f"ROUTE /upload_youtube: Request for recipe ID: {recipe_db_id} ({recipe_name_orig}) using GDrive ID: {video_gdrive_id}")

    # The youtube_uploader service now handles fetching video from GDrive ID.
    # It also requires recipe_db_id to fetch the correct GDrive ID if not passed directly.
    # We are passing video_gdrive_id from the form, but the service expects recipe_db_id to look it up.
    # Let's adjust the call to youtube_uploader.upload_video_to_youtube to primarily use recipe_db_id
    # and it will internally fetch the merged_video_gdrive_id.

    tag_list = [tag.strip() for tag in tags.split(',') if tag.strip()]
    upload_metadata = {"title": title, "description": description, "tags": tag_list}
    privacy = "unlisted"

    update_recipe_status(recipe_id=recipe_db_id, name=recipe_name_orig, status="UPLOADING_YOUTUBE")
    background_tasks.add_task(
        youtube_uploader.upload_video_to_youtube, # This function will use recipe_db_id to get video_gdrive_id
        metadata=upload_metadata,
        privacy_status=privacy,
        recipe_db_id_for_status_update=recipe_db_id, 
        recipe_name_for_status_update=recipe_name_orig
        # video_gdrive_id is no longer passed here, service will fetch it from DB via recipe_db_id
    )
    
    msg = f"YouTube upload for '{recipe_name_orig}' started in background."
    return RedirectResponse(url=f"/select_folder?message={msg}", status_code=303)

# --- API for status updates (for UI polling) ---
@router.get("/api/recipe_status/{recipe_id}")
async def api_get_recipe_status(recipe_id: str):
    status_data = get_recipe_status(recipe_id)
    if not status_data:
        raise HTTPException(status_code=404, detail="Recipe not found")
    return status_data

@router.get("/api/all_recipes_status")
async def api_get_all_recipes_status():
    all_statuses = get_all_recipes_from_db()
    if not all_statuses:
        return {}
    return all_statuses

# New endpoint to manually trigger next step if a background task completed
# but the next one needs to be initiated (e.g., after merge, trigger metadata gen)
@router.post("/trigger_next_step/{recipe_id}")
async def trigger_next_step_route(background_tasks: BackgroundTasks, recipe_id: str):
    trigger_next_background_task(background_tasks, recipe_id)
    recipe_data = get_recipe_status(recipe_id)
    status_now = recipe_data.get("status", "Unknown") if recipe_data else "Unknown"
    return RedirectResponse(url=f"/select_folder?message=Attempted_to_trigger_next_step_for_{recipe_id}._Current_status:_{status_now}", status_code=303)


@router.post("/reset_recipe/{recipe_db_id}", name="reset_recipe_route")
async def reset_recipe_endpoint(request: Request, recipe_db_id: str):
    print(f"ROUTE /reset_recipe: Request to reset recipe ID: {recipe_db_id}")
    # It might be good to get recipe_name here too for the message, but not strictly needed for reset logic itself
    
    # Call the utility function to reset the recipe in the database
    # This function should set status to "New" and clear relevant fields
    from utils import reset_recipe_in_db # Ensure it's imported
    success = reset_recipe_in_db(recipe_db_id)

    if success:
        msg = f"Recipe_ID_{recipe_db_id}_has_been_reset_to_New_status."
        # Optional: Add logic here to delete local temp files associated with this recipe_db_id
        # This would involve constructing paths based on TEMP_PROCESSING_BASE_DIR and recipe_db_id/name
        # e.g., shutil.rmtree(os.path.join(TEMP_PROCESSING_BASE_DIR, "raw_clips_temp", safe_recipe_name)) etc.
    # For now, we primarily reset the DB entry.
    else:
        msg = f"Error:_Could_not_reset_recipe_ID_{recipe_db_id}._It_might_not_exist_in_the_database."
        return RedirectResponse(url=f"/select_folder?error={msg}", status_code=303)

    return RedirectResponse(url=f"/select_folder?message={msg}", status_code=303)


# --- Admin Route for Hard DB Reset ---
@router.post("/admin/hard_reset_db", name="hard_reset_db_route")
async def hard_reset_database_route(request: Request):
    print("ROUTE /admin/hard_reset_db: Request to HARD RESET entire database.")
    from utils import hard_reset_db_content # Ensure it's imported
    
    try:
        hard_reset_db_content()
        msg = "SUCCESS:_Database_has_been_completely_reset_to_its_initial_state."
        return RedirectResponse(url=f"/select_folder?message={msg}", status_code=303)
    except Exception as e:
        print(f"ERROR during hard_reset_db_route: {e}")
        error_msg = f"Error_during_database_hard_reset:_{str(e)}"
        # Potentially redirect to an error page or home page if /select_folder also relies on DB being healthy
        return RedirectResponse(url=f"/?error={error_msg}", status_code=303) # Redirect to home with error