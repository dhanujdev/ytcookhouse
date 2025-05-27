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
from config import RAW_DIR, OUTPUT_DIR # Assuming OUTPUT_DIR for metadata
from utils import update_recipe_status, get_recipe_status, get_all_recipes_from_db

router = APIRouter()

TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), '..', 'templates')
templates = Jinja2Templates(directory=TEMPLATES_DIR)

# --- Helper function to trigger next step in the background ---
def trigger_next_background_task(background_tasks: BackgroundTasks, recipe_id: str):
    recipe_data = get_recipe_status(recipe_id)
    if not recipe_data:
        print(f"BACKGROUND_TRIGGER: Recipe {recipe_id} not found in DB. Cannot trigger next task.")
        return

    current_status = recipe_data.get("status")
    recipe_name_orig = recipe_data.get("name", "Unknown Recipe")
    print(f"BACKGROUND_TRIGGER: Current status for {recipe_id} ({recipe_name_orig}) is {current_status}")

    if current_status == "DOWNLOADED":
        clips_path = recipe_data.get("raw_clips_path")
        if clips_path and os.path.exists(clips_path):
            print(f"BACKGROUND_TRIGGER: Triggering MERGING for {recipe_id} from {clips_path}")
            update_recipe_status(recipe_id=recipe_id, name=recipe_name_orig, status="MERGING")
            background_tasks.add_task(video_editor.merge_videos_and_replace_audio, clips_path, recipe_id, recipe_name_orig)
        else:
            err_msg = f"Clips path {clips_path} not found for MERGING."
            print(f"BACKGROUND_TRIGGER: ERROR for {recipe_id} - {err_msg}")
            update_recipe_status(recipe_id=recipe_id, name=recipe_name_orig, status="MERGE_FAILED", error_message=err_msg)
    
    elif current_status == "MERGED":
        merged_video_path = recipe_data.get("merged_video_path")
        if merged_video_path and os.path.exists(merged_video_path):
            print(f"BACKGROUND_TRIGGER: Triggering METADATA_GENERATION for {recipe_id} from {merged_video_path}")
            update_recipe_status(recipe_id=recipe_id, name=recipe_name_orig, status="GENERATING_METADATA")
            background_tasks.add_task(gemini.generate_youtube_metadata_from_video_info, merged_video_path, recipe_id, recipe_name_orig)
        else:
            err_msg = f"Merged video path {merged_video_path} not found for METADATA_GENERATION."
            print(f"BACKGROUND_TRIGGER: ERROR for {recipe_id} - {err_msg}")
            update_recipe_status(recipe_id=recipe_id, name=recipe_name_orig, status="METADATA_FAILED", error_message=err_msg)

    elif current_status == "METADATA_GENERATED":
        # This status means it's ready for preview. No automatic background task from here.
        # The user will initiate YouTube upload from the preview page.
        print(f"BACKGROUND_TRIGGER: Recipe {recipe_id} is METADATA_GENERATED. Ready for preview and manual YouTube upload trigger.")
        update_recipe_status(recipe_id=recipe_id, name=recipe_name_orig, status="READY_FOR_PREVIEW")

    # Add more conditions if there are other auto-triggered steps

# --- Routes ---

@router.get("/select_folder", response_class=HTMLResponse)
async def select_folder_page(request: Request, message: str = None, error: str = None):
    folders_with_status = gdrive.list_folders_from_gdrive_and_db_status()
    return templates.TemplateResponse("select_folder.html", {
        "request": request, 
        "folders": folders_with_status,
        "message": message,
        "error": error
    })

@router.post("/fetch_clips")
async def fetch_clips_route(background_tasks: BackgroundTasks, folder_id: str = Form(...), folder_name: str = Form(...)):
    print(f"ROUTE /fetch_clips: Request for folder ID: {folder_id}, Name: {folder_name}")
    safe_folder_name = "".join(c if c.isalnum() else "_" for c in folder_name)
    download_path = os.path.join(RAW_DIR, safe_folder_name)

    if not os.path.exists(RAW_DIR): os.makedirs(RAW_DIR)
    if not os.path.exists(download_path): os.makedirs(download_path)

    update_recipe_status(recipe_id=folder_id, name=folder_name, status="DOWNLOADING", raw_clips_path=download_path) # Set status
    
    success = gdrive.download_folder_contents(folder_id, folder_name, download_path)
    
    if success:
        # gdrive.download_folder_contents already updates to DOWNLOADED or DOWNLOAD_FAILED
        # Now trigger the merge task if download was successful
        current_recipe_info = get_recipe_status(folder_id)
        if current_recipe_info and current_recipe_info.get("status") == "DOWNLOADED":
            update_recipe_status(recipe_id=folder_id, name=folder_name, status="MERGING") # Set before adding task
            background_tasks.add_task(video_editor.merge_videos_and_replace_audio, download_path, folder_id, folder_name)
            msg = f"Clips for '{folder_name}' downloaded. Merging started in background."
            return RedirectResponse(url=f"/select_folder?message={msg}", status_code=303)
        else:
            # This case should ideally be handled by gdrive service setting DOWNLOAD_FAILED
            error_msg = current_recipe_info.get("error_message", f"Download failed for {folder_name}, merge not started.")
            return RedirectResponse(url=f"/select_folder?error={error_msg}", status_code=303)
    else:
        # gdrive service already sets DOWNLOAD_FAILED and error message
        db_entry = get_recipe_status(folder_id)
        error_message = db_entry.get("error_message", f"Download failed for {folder_name}. Check logs.") if db_entry else f"Download failed for {folder_name}. Check logs."
        return RedirectResponse(url=f"/select_folder?error={error_message}", status_code=303)


@router.get("/preview/{recipe_db_id}", response_class=HTMLResponse)
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
    
    local_temp_video_for_preview = None
    local_temp_metadata_for_preview = None

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
        gdrive_service = gdrive.get_gdrive_service()
        
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
        
        return templates.TemplateResponse("preview.html", {
            "request": request, "recipe_db_id": recipe_db_id, "recipe_name_safe": recipe_name_safe,
            "recipe_name_display": recipe_name_orig, 
            "video_gdrive_id": merged_video_gdrive_id, 
            "video_url": video_url, "metadata": metadata_content,
            "current_status": current_status, 
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

@router.post("/trigger_metadata_generation/{recipe_db_id}")
async def trigger_metadata_route(background_tasks: BackgroundTasks, recipe_db_id: str):
    recipe_data = get_recipe_status(recipe_db_id)
    if not recipe_data:
        raise HTTPException(status_code=404, detail="Recipe not found")

    recipe_name_orig = recipe_data.get("name", "Unknown Recipe")
    merged_video_path = recipe_data.get("merged_video_path")
    current_status = recipe_data.get("status")

    if current_status != "MERGED":
        return RedirectResponse(url=f"/select_folder?error=Recipe '{recipe_name_orig}' is not in MERGED state. Current: {current_status}", status_code=303)

    if not merged_video_path or not os.path.exists(merged_video_path):
        update_recipe_status(recipe_id=recipe_db_id, name=recipe_name_orig, status="METADATA_FAILED", error_message=f"Merged video path {merged_video_path} not found.")
        return RedirectResponse(url=f"/select_folder?error=Merged_video_for_{recipe_name_orig}_not_found_cannot_generate_metadata.", status_code=303)

    update_recipe_status(recipe_id=recipe_db_id, name=recipe_name_orig, status="GENERATING_METADATA")
    background_tasks.add_task(gemini.generate_youtube_metadata_from_video_info, merged_video_path, recipe_db_id, recipe_name_orig)
    msg = f"Metadata generation started in background for '{recipe_name_orig}'."
    return RedirectResponse(url=f"/select_folder?message={msg}", status_code=303)

@router.post("/upload_youtube")
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

