import os
import json
import sys
import tempfile

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
# Use METADATA_TEMP_DIR which is the absolute path from config
from config import (
    METADATA_TEMP_DIR, 
    GEMINI_API_KEY, 
    GOOGLE_DRIVE_APP_DATA_FOLDER_NAME,
    # ---- Added for Refactoring ----
    GEMINI_SERVICE_CLIENT, # Shared client/model instance
    APP_STARTUP_STATUS     # For updating status during checks
    # -----------------------------
)
from utils import update_recipe_status, get_recipe_status # get_recipe_status for fetching GDrive ID
from services import gdrive # Import gdrive service
import google.generativeai as genai

class GeminiServiceError(Exception):
    pass

# Store the model name globally within the module or fetch from config if it can vary
DEFAULT_GEMINI_MODEL_NAME = "gemini-1.5-flash"

def create_gemini_model(model_name: str = DEFAULT_GEMINI_MODEL_NAME): # Renamed and simplified
    """
    Configures genai and returns an instance of GenerativeModel.
    """
    print(f"Gemini Model Factory: Attempting to configure API and get model: {model_name}...")
    if not GEMINI_API_KEY or GEMINI_API_KEY == "...":
        msg = "Gemini Model Factory Error: GEMINI_API_KEY is not configured in config.py."
        print(f"ERROR: {msg}")
        raise GeminiServiceError(msg)
    
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        print("Gemini Model Factory: genai.configure called successfully.")
        model = genai.GenerativeModel(model_name)
        print(f"Gemini Model Factory: GenerativeModel instance for '{model_name}' created.")
        return model
    except Exception as e:
        msg = f"Gemini Model Factory Error: Failed to configure or get model '{model_name}': {str(e)}"
        print(f"ERROR: {msg}")
        raise GeminiServiceError(msg)

def check_gemini_service() -> bool:
    """
    Performs a basic check of the Gemini service by trying to list available models
    and ensuring the default model is supported.
    Returns True if successful, False otherwise.
    Updates APP_STARTUP_STATUS with error details on failure.
    """
    print("Gemini Check: Attempting to list models and verify default model support...")
    if not GEMINI_API_KEY or GEMINI_API_KEY == "...":
        error_msg = "Gemini Check: GEMINI_API_KEY is not configured."
        print(f"ERROR: {error_msg}")
        APP_STARTUP_STATUS["gemini_error_details"] = error_msg
        return False
    try:
        genai.configure(api_key=GEMINI_API_KEY) # Ensure API key is configured for list_models
        
        model_found = False
        # Check if the specific model we use is listed as supported by this API key
        # The model name for listing might be different from the one used for GenerativeModel instance
        # e.g. list_models might show 'gemini-1.5-flash-latest' or similar.
        # We are checking if ANY model containing parts of our DEFAULT_GEMINI_MODEL_NAME exists.
        # A more robust check would be to use the exact model name if known for list_models response.
        models_listed_count = 0
        supported_model_name_to_check = DEFAULT_GEMINI_MODEL_NAME # or specific name like 'gemini-1.5-flash-001'
        
        for m in genai.list_models():
            models_listed_count += 1
            # print(f"DEBUG Gemini Check: Found model {m.name}") # For debugging
            if supported_model_name_to_check in m.name: # Check if our default model is in the supported list
                # More precise check if `m.supported_generation_methods` includes 'generateContent' for this model
                if 'generateContent' in m.supported_generation_methods:
                    model_found = True
                    # print(f"Gemini Check: Model '{m.name}' supports 'generateContent'.") # Debug
                    # break # Found a suitable model
            # Check for the exact model name we use to instantiate GenerativeModel
            if DEFAULT_GEMINI_MODEL_NAME == m.name and 'generateContent' in m.supported_generation_methods:
                model_found = True
                print(f"Gemini Check: Exact model '{DEFAULT_GEMINI_MODEL_NAME}' found and supports 'generateContent'.")
                break

        if models_listed_count == 0:
            error_msg = "Gemini Check: genai.list_models() returned no models. Check API key and permissions."
            print(f"ERROR: {error_msg}")
            APP_STARTUP_STATUS["gemini_error_details"] = error_msg
            return False
            
        if model_found:
            print(f"Gemini Check: Successfully listed {models_listed_count} models and confirmed support for a model like '{DEFAULT_GEMINI_MODEL_NAME}'. Service is operational.")
            return True
        else:
            error_msg = f"Gemini Check: Default model '{DEFAULT_GEMINI_MODEL_NAME}' or a variant supporting generateContent not found in the {models_listed_count} listed models."
            print(f"ERROR: {error_msg}")
            APP_STARTUP_STATUS["gemini_error_details"] = error_msg
            return False

    except Exception as e:
        error_msg = f"Gemini Check: Error during model listing or check: {str(e)}"
        print(f"ERROR: {error_msg}")
        APP_STARTUP_STATUS["gemini_error_details"] = error_msg
        return False

def get_default_gemini_prompt(recipe_name_orig: str, video_path_context_for_prompt: str) -> str:
    """Generates the default prompt for Gemini metadata generation, with guidance for UI customization."""
    return f'''
    You are YTGenie, an expert YouTube content strategist and wordsmith, specializing in creating viral-worthy content for cooking channels. 
    Your tone should be: [Specify Tone - e.g., "friendly and engaging", "humorous and informative", "professional for a Telugu-speaking audience"]. Default is friendly and engaging.
    The video is for a recipe titled: "{recipe_name_orig}".
    Video context (e.g., GDrive ID, not directly accessible by AI): {video_path_context_for_prompt}
    
    Key details to incorporate (user-provided if available, otherwise infer or be general):
    - Main Ingredients: [User: List key ingredients, e.g., "Chicken, Basmati Rice, Whole Spices"]
    - Brief Process Overview: [User: Briefly describe main steps, e.g., "Marination, Layering, Dum Cooking"]
    - Target Audience Notes: [User: e.g., "Explain steps simply for beginners", "Highlight health benefits", "Mention cultural significance for Telugu viewers"]

    Please generate the following metadata in strict JSON format. Output ONLY the raw JSON object:
    {{
        "title": "string (max 100 chars, SEO-friendly, include '{recipe_name_orig}'. Make it catchy and click-worthy! Example: The Perfect {recipe_name_orig} Recipe!)",
        "description": "string (300-400 words). Structure:
            1. Catchy opening (1-2 sentences) - what is the video about and why watch it?
            2. Brief overview of the dish: taste, texture, uniqueness. [Incorporate Main Ingredients and Process Overview from above if provided by user]
            3. Key cooking stages/highlights (without specific timestamps yet).
            4. Call to action (e.g., subscribe, comment, like, visit website).
            5. Relevant hashtags (2-3, e.g., #{recipe_name_orig.replace(" ", "")}, #EasyCooking, #[User: Add a custom hashtag]).",
        "tags": "array of 12-15 strings (include '{recipe_name_orig}', variations, main ingredients [if provided by user], cooking style, cuisine type, occasion, e.g., 'dinner party', 'quick meal')",
        "chapters": "array of 5-7 objects, each with '{{\"time\": \"[HH:MM:SS]\", \"label\": \"Descriptive chapter title\"}}'. Chapters should cover logical steps like: 
            - Introduction / Ingredients Overview
            - Preparation of [Main Component]
            - Cooking Process Part 1 (e.g., Sautéing Aromatics)
            - Cooking Process Part 2 (e.g., Adding Main Ingredients & Simmering)
            - Final Steps / Garnishing
            - Plating & Serving Suggestions
            - Taste Test / Outro
            Adjust based on the actual recipe flow.",
        "transcript_suggestion": "string (50-100 words for a compelling video opening or a short summary for social media. Make it exciting! [Incorporate Target Audience Notes if provided by user])"
    }}
    Ensure all JSON strings are properly escaped. Focus on quality, engagement, and SEO.
    '''

def generate_youtube_metadata_from_video_info(recipe_db_id: str, recipe_name_orig: str, custom_prompt_str: str = None):
    # merged_video_gdrive_id will be fetched from DB
    print(f"BACKGROUND TASK: Gemini: Starting metadata for {recipe_db_id} ({recipe_name_orig}). Custom prompt provided: {bool(custom_prompt_str)}")
    
    current_db_status_on_exit = "METADATA_FAILED"
    error_message_on_exit = "Unknown Gemini error"
    final_metadata_gdrive_id = None
    local_temp_video_path = None # For downloaded video if needed for context (not used by current prompt)
    local_temp_metadata_path = None

    try:
        # Background task should create its own gdrive client instance
        print("BACKGROUND TASK: Gemini: Creating task-specific GDrive client.")
        gdrive_service = gdrive.create_gdrive_service()

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

        # Background task should create its own Gemini model instance
        print("BACKGROUND TASK: Gemini: Creating task-specific Gemini model.")
        model = create_gemini_model() # Using the factory function
        if not model:
            raise GeminiServiceError("Failed to create Gemini model instance for background task.")

        prompt_to_use = custom_prompt_str
        if not prompt_to_use:
            prompt_to_use = get_default_gemini_prompt(recipe_name_orig, video_path_context_for_prompt)
        
        print(f"BACKGROUND TASK: Gemini: Sending prompt for {recipe_name_orig}...")
        # print(f"Using Prompt:\n{prompt_to_use[:500]}...") # For debugging long prompts
        response = model.generate_content(prompt_to_use, generation_config=genai.types.GenerationConfig(candidate_count=1))

        if not response.candidates or not response.text:
            block_reason = response.prompt_feedback.block_reason if response.prompt_feedback else "Unknown"
            raise GeminiServiceError(f"Gemini API empty/blocked response. Reason: {block_reason}.")
        
        gemini_output_text = response.text
        if gemini_output_text.strip().startswith("```json"):
            gemini_output_text = gemini_output_text.strip()[7:-3].strip()
        elif gemini_output_text.strip().startswith("```"):
            gemini_output_text = gemini_output_text.strip()[3:-3].strip()
        parsed_metadata = json.loads(gemini_output_text)

        # METADATA_TEMP_DIR from config is already the absolute, environment-specific path
        # config.py ensures it exists
        safe_recipe_name = "".join(c if c.isalnum() else "_" for c in recipe_name_orig)
        
        temp_metadata_file_obj = tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json', dir=METADATA_TEMP_DIR, prefix=f"{safe_recipe_name}_")
        local_temp_metadata_path = temp_metadata_file_obj.name # This is an absolute path
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
