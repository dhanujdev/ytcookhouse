from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
import os

# Assuming your services and config are accessible via these paths
# You might need to adjust imports based on your project structure
import sys
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from services import gdrive
from config import RAW_DIR
# Import update_recipe_status from utils
from utils import update_recipe_status, get_recipe_status # Added get_recipe_status

router = APIRouter()

# Configure templates
TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), '..', 'templates')
templates = Jinja2Templates(directory=TEMPLATES_DIR)

@router.get("/select_folder", response_class=HTMLResponse)
async def select_folder(request: Request):
    # Use the new gdrive function that includes DB status
    folders_with_status = gdrive.list_folders_from_gdrive_and_db_status()
    # The name in folders_with_status might already include status, 
    # but template can decide how to display folder.name and folder.display_name
    return templates.TemplateResponse("select_folder.html", {"request": request, "folders": folders_with_status})

@router.post("/fetch_clips")
async def fetch_clips(folder_id: str = Form(...), folder_name: str = Form(...)):
    print(f"Received request to fetch clips for folder ID: {folder_id}, Name: {folder_name}")
    safe_folder_name = "".join(c if c.isalnum() else "_" for c in folder_name)
    download_path = os.path.join(RAW_DIR, safe_folder_name)

    # Ensure RAW_DIR and recipe-specific download path exist (gdrive service also does this for its own path)
    if not os.path.exists(RAW_DIR):
        os.makedirs(RAW_DIR)
    if not os.path.exists(download_path):
        os.makedirs(download_path)

    # Pass folder_name (original name) to the service function for DB update
    success = gdrive.download_folder_contents(folder_id, folder_name, download_path)
    
    if success:
        # The gdrive service now handles updating db.json with "downloaded" status and raw_clips_path
        # Redirect to merge_and_process, passing folder_id as recipe_db_id
        return RedirectResponse(url=f"/merge_and_process?recipe_db_id={folder_id}&recipe_name_orig={folder_name}&recipe_name_safe={safe_folder_name}&clips_path={download_path}", status_code=303)
    else:
        # gdrive service handles "download_failed" status. Redirect with a generic error.
        # The specific error is in db.json.
        return RedirectResponse(url=f"/select_folder?error=Download_failed_for_{safe_folder_name}._Check_logs_or_DB_for_details.", status_code=303)

@router.get("/merge_and_process", response_class=HTMLResponse)
async def merge_and_process_get(request: Request, recipe_db_id: str, recipe_name_orig: str, recipe_name_safe: str, clips_path: str):
    print(f"Request for /merge_and_process for recipe ID: {recipe_db_id} ({recipe_name_orig}) at path: {clips_path}")
    from services import video_editor 

    try:
        # Corrected call to include recipe_db_id as the second argument
        merged_video_path = video_editor.merge_videos_and_replace_audio(raw_clips_path=clips_path, recipe_db_id=recipe_db_id, recipe_name_orig=recipe_name_orig)
        if merged_video_path:
            update_recipe_status(recipe_id=recipe_db_id, name=recipe_name_orig, status="merged", merged_video_path=merged_video_path)
            # Pass recipe_db_id along for further status updates
            return RedirectResponse(url=f"/generate_metadata?recipe_db_id={recipe_db_id}&recipe_name_orig={recipe_name_orig}&recipe_name_safe={recipe_name_safe}&video_path={merged_video_path}", status_code=303)
        else:
            update_recipe_status(recipe_id=recipe_db_id, name=recipe_name_orig, status="merge_failed", error_message="Video merging returned no path.")
            return RedirectResponse(url=f"/select_folder?error=Failed_to_merge_video_for_{recipe_name_safe}.", status_code=303)
    except Exception as e:
        print(f"Error during merge_and_process for {recipe_name_safe}: {e}")
        update_recipe_status(recipe_id=recipe_db_id, name=recipe_name_orig, status="merge_failed", error_message=str(e))
        return RedirectResponse(url=f"/select_folder?error=Error_merging_{recipe_name_safe}_{str(e).replace(' ','_')}.", status_code=303)


@router.get("/generate_metadata", response_class=HTMLResponse)
async def generate_metadata_get(request: Request, recipe_db_id: str, recipe_name_orig: str, recipe_name_safe: str, video_path: str):
    print(f"Request for /generate_metadata for recipe ID: {recipe_db_id} ({recipe_name_orig}), video: {video_path}")
    from services import gemini
    from services.gemini import GeminiServiceError

    try:
        # Updated function call to include recipe_db_id
        metadata_file_path = gemini.generate_youtube_metadata_from_video_info(video_path, recipe_db_id, recipe_name_orig)
        if metadata_file_path:
            update_recipe_status(recipe_id=recipe_db_id, name=recipe_name_orig, status="metadata_generated", metadata_file_path=metadata_file_path)
            # Pass recipe_db_id to preview
            return RedirectResponse(url=f"/preview?recipe_db_id={recipe_db_id}&recipe_name_orig={recipe_name_orig}&recipe_name_safe={recipe_name_safe}&video_path={video_path}&metadata_path={metadata_file_path}", status_code=303)
        else:
            # This case should be covered by GeminiServiceError
            update_recipe_status(recipe_id=recipe_db_id, name=recipe_name_orig, status="metadata_failed", error_message="Gemini service returned no path without error.")
            return RedirectResponse(url=f"/select_folder?error=Unknown_error_generating_metadata_for_{recipe_name_safe}.", status_code=303)
    except GeminiServiceError as e:
        print(f"GeminiServiceError for {recipe_name_safe}: {e}")
        update_recipe_status(recipe_id=recipe_db_id, name=recipe_name_orig, status="metadata_failed", error_message=str(e))
        return RedirectResponse(url=f"/select_folder?error=Metadata_error_for_{recipe_name_safe}_{str(e).replace(' ', '_')}.", status_code=303)
    except Exception as e:
        print(f"Unexpected error during metadata gen for {recipe_name_safe}: {e}")
        update_recipe_status(recipe_id=recipe_db_id, name=recipe_name_orig, status="metadata_failed", error_message=f"Unexpected: {str(e)}")
        return RedirectResponse(url=f"/select_folder?error=Unexpected_metadata_error_for_{recipe_name_safe}.", status_code=303)

import json # Add json import for loading metadata

@router.get("/preview", response_class=HTMLResponse)
async def preview_video(request: Request, recipe_db_id: str, recipe_name_orig: str, recipe_name_safe: str, video_path: str, metadata_path: str):
    # recipe_db_id is now passed, can be used if needed for context, e.g. fetching full DB entry
    print(f"Request for /preview for recipe ID: {recipe_db_id} ({recipe_name_orig}), video: {video_path}, metadata: {metadata_path}")
    try:
        with open(metadata_path, 'r') as f:
            metadata_content = json.load(f)
        
        video_dir_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'videos'))
        relative_video_path = os.path.relpath(video_path, video_dir_root)
        video_url = f"/videos_serve/{relative_video_path.replace(os.sep, '/')}"

        metadata_content['file_path'] = metadata_path
        # Pass recipe_db_id to the template for the form submission to /upload_youtube
        return templates.TemplateResponse("preview.html", {
            "request": request,
            "recipe_db_id": recipe_db_id, 
            "recipe_name_safe": recipe_name_safe,
            "recipe_name_display": recipe_name_orig, 
            "video_path": video_path, 
            "video_url": video_url,   
            "metadata": metadata_content,
        })
    except FileNotFoundError:
        error_msg_template = "Metadata file not found. Please try processing again."
        update_recipe_status(recipe_id=recipe_db_id, name=recipe_name_orig, status="preview_failed", error_message=error_msg_template)
        return templates.TemplateResponse("preview.html", {
            "request": request, "recipe_db_id": recipe_db_id, "recipe_name_safe": recipe_name_safe, "recipe_name_display": recipe_name_orig,
            "error_message": error_msg_template
        })
    except json.JSONDecodeError:
        error_msg_template = "Metadata file is corrupted. Please try processing again."
        update_recipe_status(recipe_id=recipe_db_id, name=recipe_name_orig, status="preview_failed", error_message=error_msg_template)
        return templates.TemplateResponse("preview.html", {
            "request": request, "recipe_db_id": recipe_db_id, "recipe_name_safe": recipe_name_safe, "recipe_name_display": recipe_name_orig,
            "error_message": error_msg_template
        })
    except Exception as e:
        error_msg_template = f"An unexpected error occurred: {str(e)}. Please try processing again."
        update_recipe_status(recipe_id=recipe_db_id, name=recipe_name_orig, status="preview_failed", error_message=str(e))
        return templates.TemplateResponse("preview.html", {
            "request": request, "recipe_db_id": recipe_db_id, "recipe_name_safe": recipe_name_safe, "recipe_name_display": recipe_name_orig,
            "error_message": error_msg_template
        })

from services import youtube_uploader 
from services.youtube_uploader import YouTubeUploaderError 

@router.post("/upload_youtube")
async def upload_to_youtube_endpoint(request: Request, 
                                   recipe_db_id: str = Form(...), # Now using recipe_db_id
                                   recipe_name_safe: str = Form(...),
                                   video_file_path: str = Form(...),
                                   title: str = Form(...),
                                   description: str = Form(...),
                                   tags: str = Form(...)
                                   ):
    print(f"Request to /upload_youtube for recipe ID: {recipe_db_id} ({recipe_name_safe})")
    # Fetch original name from DB if needed for display, though recipe_name_safe is often used for messages
    recipe_info = get_recipe_status(recipe_db_id) 
    recipe_name_orig = recipe_info.get("name", recipe_name_safe) if recipe_info else recipe_name_safe

    tag_list = [tag.strip() for tag in tags.split(',') if tag.strip()]
    upload_metadata = {"title": title, "description": description, "tags": tag_list}

    try:
        # Corrected call to match the service function signature
        privacy = "unlisted" # Default privacy status for uploads from the app
        youtube_url = youtube_uploader.upload_video_to_youtube(
            video_file_path=video_file_path,
            metadata=upload_metadata,
            privacy_status=privacy
        )
        if youtube_url:
            update_recipe_status(recipe_id=recipe_db_id, name=recipe_name_orig, status="uploaded", youtube_url=youtube_url)
            message = f"Video_for_{recipe_name_safe}_successfully_uploaded_to_YouTube_as_{privacy}_at_{youtube_url}."
            return RedirectResponse(url=f"/select_folder?message={message}", status_code=303)
        else:
            update_recipe_status(recipe_id=recipe_db_id, name=recipe_name_orig, status="upload_failed", error_message="YouTube service returned no URL without error.")
            return RedirectResponse(url=f"/select_folder?error=YouTube_upload_failed_for_{recipe_name_safe}_unknown_reason.", status_code=303)
    except YouTubeUploaderError as e:
        update_recipe_status(recipe_id=recipe_db_id, name=recipe_name_orig, status="upload_failed", error_message=str(e))
        return RedirectResponse(url=f"/select_folder?error=YouTube_upload_error_for_{recipe_name_safe}_{str(e).replace(' ', '_')}.", status_code=303)
    except FileNotFoundError as e: 
        update_recipe_status(recipe_id=recipe_db_id, name=recipe_name_orig, status="upload_failed", error_message=f"Video file not found: {str(e)}")
        return RedirectResponse(url=f"/select_folder?error=Video_file_not_found_for_upload_{recipe_name_safe}.", status_code=303)
    except Exception as e:
        update_recipe_status(recipe_id=recipe_db_id, name=recipe_name_orig, status="upload_failed", error_message=f"Unexpected: {str(e)}")
        return RedirectResponse(url=f"/select_folder?error=Unexpected_error_during_YouTube_upload_for_{recipe_name_safe}.", status_code=303)
