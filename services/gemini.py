import os
import json
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from config import OUTPUT_DIR, GEMINI_API_KEY, MERGED_DIR # Added MERGED_DIR
from utils import update_recipe_status # To update DB on success/failure if needed from here

# Import the google-generativeai library
import google.generativeai as genai

class GeminiServiceError(Exception):
    """Custom exception for Gemini service errors."""
    pass

def generate_youtube_metadata_from_video_info(video_path: str, recipe_db_id: str, recipe_name_orig: str) -> str | None:
    """
    Generates YouTube metadata for a given video using the Gemini API.
    It sends information about the video (like title, context) to a text-based Gemini model.
    Does NOT upload the video file itself to Gemini in this version.

    Args:
        video_path: Path to the merged video file (used for context in prompt).
        recipe_db_id: The database ID for the recipe.
        recipe_name_orig: The original recipe name for prompting.

    Returns:
        The path to the saved metadata JSON file if successful, None otherwise.
    """
    print(f"Attempting to generate YouTube metadata via Gemini for recipe: {recipe_name_orig} (ID: {recipe_db_id})")

    if not GEMINI_API_KEY or GEMINI_API_KEY == "...":
        msg = "GEMINI_API_KEY is not configured in .env or config.py. Cannot proceed with metadata generation."
        print(f"ERROR: {msg}")
        # No db update here as this check is upfront; the route handler would update status if it calls this and fails.
        raise GeminiServiceError(msg)

    try:
        genai.configure(api_key=GEMINI_API_KEY)
        print("Gemini API configured successfully.")
    except Exception as e:
        msg = f"Failed to configure Gemini client with API key: {e}"
        print(f"ERROR: {msg}")
        raise GeminiServiceError(msg)

    # Choose a Gemini model suitable for text generation and structured JSON output.
    # Example: 'gemini-1.5-flash', 'gemini-1.0-pro', etc.
    # For more advanced multimodal features (if sending video frames/content directly), 
    # you might use a model like 'gemini-1.5-pro' (latest) or 'gemini-pro-vision'.
    # For now, we'll stick to text-based input about the video.
    # model_name = "gemini-1.0-pro" # This caused a 404 with v1beta API
    # model_name = "gemini-1.5-flash-latest" # Caused GenerationConfig error with response_mime_type
    model_name = "gemini-2.5-flash-preview-05-20" # User selected preview model
    print(f"Using Gemini model: {model_name}")
    model = genai.GenerativeModel(model_name)

    # Craft the prompt for Gemini.
    # This is a critical part and may need significant tuning for best results.
    prompt = f"""
    You are an expert YouTube content strategist, specializing in cooking channels.
    A video has been created for a recipe titled: "{recipe_name_orig}".
    The video shows the full cooking process for this recipe.
    The final video file is located at (server path, for context only, you cannot access it): {video_path}

    Please generate the following metadata for this YouTube video, strictly in JSON format:
    1.  "title": A compelling, SEO-friendly YouTube title. It must include "{recipe_name_orig}". Max 100 characters.
    2.  "description": A detailed and engaging YouTube description (around 200-400 words).
        *   Start with an enticing summary of the "{recipe_name_orig}" recipe.
        *   Include relevant keywords naturally.
        *   Suggest a few logical chapters with timestamps (e.g., "0:00 Intro", "1:15 Preparing Ingredients", "3:00 Cooking {recipe_name_orig}", etc.). Make these timestamps placeholders as you don't know the exact video timings.
        *   Add a call to action (e.g., like, subscribe, comment).
        *   Include 2-3 relevant hashtags at the end of the description (e.g., #{recipe_name_orig.replace(' ','')} #easyrecipe #homecooking).
        *   Maximum 5000 characters for the entire description.
    3.  "tags": An array of 10-15 relevant string keywords for YouTube tags. Include variations of "{recipe_name_orig}", cooking style, main ingredients if inferable, etc.
    4.  "chapters": An array of chapter objects, each object having "time" (string, e.g., "0:00") and "label" (string, e.g., "Introduction"). These should match the chapters suggested in the description. Create at least 3-5 chapters.
    5.  "transcript_suggestion": A brief, engaging suggested opening for the video transcript (approx. 50-100 words for the first minute or so of the video), introducing the "{recipe_name_orig}" recipe.

    Output ONLY the raw JSON object. Do not include any explanatory text before or after the JSON.
    Example for "chapters": [ {{"time": "0:00", "label": "Introduction"}}, {{"time": "1:15", "label": "Preparing Ingredients"}} ]
    Ensure all string values in the JSON are properly escaped.
    """

    print(f"Sending prompt to Gemini for {recipe_name_orig}...")
    # print(f"--- PROMPT START ---\n{prompt}\n--- PROMPT END ---") # Uncomment to debug the exact prompt

    try:
        # The new API uses generate_content with specific configurations for JSON
        generation_config = genai.types.GenerationConfig(
            candidate_count=1
            # response_mime_type="application/json", # Removed as it caused error with current library/model combo
        )
        # For older models like gemini-1.0-pro, response_mime_type is not directly in GenerationConfig for the model object directly.
        # Instead, strict prompting for JSON is key. (This comment is less relevant now with 1.5 flash)

        response = model.generate_content(prompt, generation_config=generation_config)
        
        # print(f"Full Gemini Response: {response}") # For debugging response structure

        if not response.candidates or not response.text:
            # Handle cases where the response might be empty or blocked due to safety settings
            block_reason = response.prompt_feedback.block_reason if response.prompt_feedback else "Unknown"
            safety_ratings = response.prompt_feedback.safety_ratings if response.prompt_feedback else "N/A"
            msg = f"Gemini API returned an empty or blocked response. Block Reason: {block_reason}. Safety Ratings: {safety_ratings}"
            print(f"ERROR: {msg}")
            raise GeminiServiceError(msg)
        
        gemini_output_text = response.text
        print(f"Successfully received response from Gemini for {recipe_name_orig}.")
        # print(f"--- GEMINI RAW TEXT OUTPUT ---\n{gemini_output_text}\n--- END GEMINI RAW TEXT --- ")

    except Exception as e:
        msg = f"Gemini API call failed for {recipe_name_orig}: {e}"
        print(f"ERROR: {msg}")
        raise GeminiServiceError(msg)

    # Parse the Gemini JSON output
    try:
        # Gemini response might be wrapped in ```json ... ```, try to strip it.
        if gemini_output_text.strip().startswith("```json"):
            gemini_output_text = gemini_output_text.strip()[7:-3].strip()
        elif gemini_output_text.strip().startswith("```"):
             gemini_output_text = gemini_output_text.strip()[3:-3].strip()

        parsed_metadata = json.loads(gemini_output_text)
        print(f"Successfully parsed JSON metadata from Gemini for {recipe_name_orig}.")
    except json.JSONDecodeError as e:
        error_detail = f"Failed to parse JSON from Gemini response: {e}. Response text was: \n{gemini_output_text[:1000]}..."
        print(f"ERROR: {error_detail}")
        raise GeminiServiceError(error_detail)

    # Ensure OUTPUT_DIR exists (config.py should also ensure this, but check again)
    if not os.path.exists(OUTPUT_DIR):
        os.makedirs(OUTPUT_DIR)

    safe_recipe_name = "".join(c if c.isalnum() else "_" for c in recipe_name_orig)
    metadata_filename = f"{safe_recipe_name}_metadata.json"
    metadata_filepath = os.path.join(OUTPUT_DIR, metadata_filename)

    try:
        with open(metadata_filepath, 'w') as f:
            json.dump(parsed_metadata, f, indent=4)
        print(f"Metadata from Gemini successfully saved to: {metadata_filepath}")
        # update_recipe_status(recipe_id=recipe_db_id, name=recipe_name_orig, status="metadata_generated", metadata_file_path=metadata_filepath)
        # The route handler will call update_recipe_status.
        return metadata_filepath
    except IOError as e:
        msg = f"Failed to save metadata for {recipe_name_orig} to {metadata_filepath}: {e}"
        print(f"ERROR: {msg}")
        raise GeminiServiceError(msg)

# Renamed from generate_youtube_metadata_from_video to reflect it takes info, not the video file itself for processing
# Also, added recipe_db_id for consistency if we need to update DB status from here in future.

# Example usage (for testing this module directly):
if __name__ == '__main__':
    print("Testing Gemini Service Module...")
    # Ensure .env is populated with a valid GEMINI_API_KEY
    print(f"Using GEMINI_API_KEY: {'SET' if GEMINI_API_KEY and GEMINI_API_KEY != '...' else 'NOT SET or placeholder ...'}")

    if GEMINI_API_KEY and GEMINI_API_KEY != "...":
        try:
            genai.configure(api_key=GEMINI_API_KEY)
            print("\nAvailable models that support 'generateContent':")
            for m in genai.list_models():
                if 'generateContent' in m.supported_generation_methods:
                    print(f"- {m.name} (Display Name: {m.display_name})")
        except Exception as e:
            print(f"Could not list models: {e}")
    print("-"*30)

    # Example data (replace with actual data from a previous step for a real test)
    test_recipe_name = "Spicy Mango Tango Salad"
    test_recipe_id = "gemini_test_mango_salad_001"
    # This path is just for context in the prompt, file doesn't need to exist for this test of the Gemini API call.
    test_video_path = os.path.join(MERGED_DIR, f"{test_recipe_name.replace(' ', '_')}_final.mp4") 

    if not GEMINI_API_KEY or GEMINI_API_KEY == "...":
        print("ERROR: GEMINI_API_KEY is not set in your .env file. Aborting test.")
    else:
        try:
            print(f"\nAttempting to generate metadata for recipe: {test_recipe_name}")
            metadata_file = generate_youtube_metadata_from_video_info(test_video_path, test_recipe_id, test_recipe_name)
            if metadata_file:
                print(f"\nSUCCESS: Gemini metadata generation test completed.")
                print(f"Metadata file saved at: {metadata_file}")
                # Print contents for review
                with open(metadata_file, 'r') as f_read:
                    print("--- Metadata Content ---")
                    print(f_read.read())
                    print("--- End Metadata Content ---")
            else:
                print("\nFAILURE: Gemini metadata generation test failed (no file path returned). Check logs.")
        except GeminiServiceError as e:
            print(f"GEMINI SERVICE ERROR during test: {e}")
        except Exception as e:
            print(f"UNEXPECTED ERROR during Gemini module test: {e}")
    
    print("\nGemini Service Module testing finished.")
