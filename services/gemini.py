import os
import json
import sys
import tempfile

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from config import OUTPUT_DIR as LOCAL_TEMP_OUTPUT_DIR, GEMINI_API_KEY, GOOGLE_DRIVE_APP_DATA_FOLDER_NAME
from utils import update_recipe_status, get_recipe_status # get_recipe_status for fetching GDrive ID
from services import gdrive # Import gdrive service
import google.generativeai as genai

class GeminiServiceError(Exception):
    pass

def generate_youtube_metadata_from_video_info(recipe_db_id: str, recipe_name_orig: str):
    # merged_video_gdrive_id will be fetched from DB
    print(f"BACKGROUND TASK: Gemini: Starting metadata for {recipe_db_id} ({recipe_name_orig})")
    
    current_db_status_on_exit = "METADATA_FAILED"
    error_message_on_exit = "Unknown Gemini error"
    final_metadata_gdrive_id = None
    local_temp_video_path = None # For downloaded video if needed for context (not used by current prompt)
    local_temp_metadata_path = None

    try:
        gdrive_service = gdrive.get_gdrive_service()
        app_data_folder_id = gdrive.get_or_create_app_data_folder_id(service=gdrive_service)
        if not app_data_folder_id:
            raise GeminiServiceError("Could not get/create GDrive App Data Folder for metadata.")

        recipe_metadata_gdrive_folder_id = gdrive.get_or_create_recipe_subfolder_id(
            app_data_folder_id, recipe_db_id, "metadata_files", service=gdrive_service
        )
        if not recipe_metadata_gdrive_folder_id:
            raise GeminiServiceError(f"Could not get/create GDrive subfolder for metadata for recipe {recipe_db_id}")

        recipe_data = get_recipe_status(recipe_db_id) # Fetch latest recipe data
        if not recipe_data:
            raise GeminiServiceError(f"Recipe data for {recipe_db_id} not found in DB.")
        
        # The prompt currently doesn't use the video content, only its name/path for context.
        # If in the future the prompt needed video content/frames, we'd download the merged video here.
        # merged_video_gdrive_id = recipe_data.get('merged_video_gdrive_id')
        # if not merged_video_gdrive_id:
        #     raise GeminiServiceError(f"merged_video_gdrive_id not found in DB for {recipe_db_id}")
        # temp_video_file = tempfile.NamedTemporaryFile(delete=False, suffix='.mp4')
        # local_temp_video_path = temp_video_file.name
        # temp_video_file.close()
        # if not gdrive.download_file_from_drive(merged_video_gdrive_id, local_temp_video_path, service=gdrive_service):
        #     raise GeminiServiceError(f"Failed to download merged video ({merged_video_gdrive_id}) from GDrive.")
        # print(f"BACKGROUND TASK: Gemini: (Simulated) Using video context from GDrive ID {merged_video_gdrive_id}")
        video_path_context_for_prompt = f"Google Drive File ID: {recipe_data.get('merged_video_gdrive_id', 'N/A')}"

        if not GEMINI_API_KEY or GEMINI_API_KEY == "...":
            raise GeminiServiceError("GEMINI_API_KEY is not configured.")
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel("gemini-1.5-flash")

        prompt = f'''
        You are an expert YouTube content strategist, specializing in cooking channels.
        A video has been created for a recipe titled: "{recipe_name_orig}".
        The video (context: {video_path_context_for_prompt}) shows the full cooking process.
        Generate metadata in JSON format: title (string, include "{recipe_name_orig}", max 100 chars), 
        description (string, 200-400 words, enticing summary, keywords, placeholder chapter timestamps, CTA, 2-3 hashtags), 
        tags (array of 10-15 strings), 
        chapters (array of objects with "time": string, "label": string; at least 3-5 chapters),
        transcript_suggestion (string, 50-100 words for opening).
        Output ONLY raw JSON. Ensure correct escaping.
        '''
        print(f"BACKGROUND TASK: Gemini: Sending prompt for {recipe_name_orig}...")
        response = model.generate_content(prompt, generation_config=genai.types.GenerationConfig(candidate_count=1))

        if not response.candidates or not response.text:
            block_reason = response.prompt_feedback.block_reason if response.prompt_feedback else "Unknown"
            raise GeminiServiceError(f"Gemini API empty/blocked response. Reason: {block_reason}.")
        
        gemini_output_text = response.text
        if gemini_output_text.strip().startswith("```json"):
            gemini_output_text = gemini_output_text.strip()[7:-3].strip()
        elif gemini_output_text.strip().startswith("```"):
             gemini_output_text = gemini_output_text.strip()[3:-3].strip()
        parsed_metadata = json.loads(gemini_output_text)

        if not os.path.exists(LOCAL_TEMP_OUTPUT_DIR): os.makedirs(LOCAL_TEMP_OUTPUT_DIR)
        safe_recipe_name = "".join(c if c.isalnum() else "_" for c in recipe_name_orig)
        
        temp_metadata_file_obj = tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json', dir=LOCAL_TEMP_OUTPUT_DIR, prefix=f"{safe_recipe_name}_")
        local_temp_metadata_path = temp_metadata_file_obj.name
        json.dump(parsed_metadata, temp_metadata_file_obj, indent=4)
        temp_metadata_file_obj.close()
        print(f"BACKGROUND TASK: Gemini: Metadata saved locally to temp: {local_temp_metadata_path}")

        gdrive_metadata_filename = f"{safe_recipe_name}_metadata.json"
        existing_metadata_gdrive_id = gdrive.find_file_id_by_name(recipe_metadata_gdrive_folder_id, gdrive_metadata_filename, service=gdrive_service)

        final_metadata_gdrive_id = gdrive.upload_file_to_drive(
            local_file_path=local_temp_metadata_path,
            drive_folder_id=recipe_metadata_gdrive_folder_id,
            drive_filename=gdrive_metadata_filename,
            mimetype='application/json',
            service=gdrive_service,
            existing_file_id=existing_metadata_gdrive_id
        )
        if not final_metadata_gdrive_id:
            raise GeminiServiceError("Failed to upload metadata JSON to Google Drive.")

        print(f"BACKGROUND TASK: Gemini: Metadata uploaded to GDrive. File ID: {final_metadata_gdrive_id}")
        current_db_status_on_exit = "READY_FOR_PREVIEW" # Changed from METADATA_GENERATED
        error_message_on_exit = None

    except GeminiServiceError as gse:
        error_message_on_exit = str(gse)
    except json.JSONDecodeError as jde:
        error_message_on_exit = f"Failed to parse JSON from Gemini: {jde}. Response text was: {gemini_output_text[:200]}..."
    except Exception as e:
        error_message_on_exit = f"Unexpected error in Gemini service: {str(e)}"
    
    finally:
        kwargs_for_status_update = {}
        if final_metadata_gdrive_id and current_db_status_on_exit == "READY_FOR_PREVIEW":
            kwargs_for_status_update['metadata_gdrive_id'] = final_metadata_gdrive_id
            kwargs_for_status_update['metadata_file_path'] = None # Clear old local path if any
        if error_message_on_exit and current_db_status_on_exit == "METADATA_FAILED":
            kwargs_for_status_update['error_message'] = error_message_on_exit
        
        update_recipe_status(
            recipe_id=recipe_db_id, 
            name=recipe_name_orig, 
            status=current_db_status_on_exit, 
            **kwargs_for_status_update
        )
        print(f"BACKGROUND TASK: Gemini: Final DB status for {recipe_db_id} set to '{current_db_status_on_exit}'. GDrive ID: {final_metadata_gdrive_id if final_metadata_gdrive_id else 'N/A'}, Error: {error_message_on_exit if error_message_on_exit else 'None'}")

        if local_temp_video_path and os.path.exists(local_temp_video_path):
            os.remove(local_temp_video_path)
            print(f"BACKGROUND TASK: Gemini: Cleaned local temp video: {local_temp_video_path}")
        if local_temp_metadata_path and os.path.exists(local_temp_metadata_path):
            os.remove(local_temp_metadata_path)
            print(f"BACKGROUND TASK: Gemini: Cleaned local temp metadata: {local_temp_metadata_path}")
        
        # The calling background task manager in routes/upload.py 
        # will use trigger_next_background_task if this step was successful and has a next auto step.
        # For READY_FOR_PREVIEW, it usually waits for user interaction.

# __main__ block for testing would need rework.
