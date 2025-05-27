import json
import os
from datetime import datetime

# Assuming config.py is in the parent directory and DB_FILE_PATH is defined there
from config import DB_FILE_PATH

# --- DB Structure --- #
# db.json will store a dictionary like:
# {
#     "recipes": {
#         "recipe_folder_id_or_unique_name": {
#             "id": "folder_id_from_gdrive",
#             "name": "Original Recipe Name",
#             "status": "downloaded" | "merged" | "metadata_generated" | "uploaded" | "failed",
#             "last_updated": "timestamp",
#             "raw_clips_path": "/path/to/raw/clips",
#             "merged_video_path": "/path/to/merged/video.mp4",
#             "metadata_file_path": "/path/to/metadata.json",
#             "youtube_url": "http://youtube.com/watch?v=...",
#             "error_message": "details if status is failed"
#         },
#         ...
#     },
#     "last_gdrive_scan": "timestamp"
# }
# --- End DB Structure --- #

def load_db() -> dict:
    """Loads the database from DB_FILE_PATH. Initializes if not found or empty."""
    if not os.path.exists(DB_FILE_PATH):
        print(f"Database file not found at {DB_FILE_PATH}. Initializing new database.")
        db_content = initialize_db()
        return db_content
    try:
        with open(DB_FILE_PATH, 'r') as f:
            content = f.read()
            if not content.strip(): # File is empty or whitespace
                print(f"Database file {DB_FILE_PATH} is empty. Initializing.")
                return initialize_db()
            db_content = json.loads(content)
            # Ensure essential keys are present
            if "recipes" not in db_content:
                db_content["recipes"] = {}
            return db_content
    except json.JSONDecodeError:
        print(f"Error decoding JSON from {DB_FILE_PATH}. Re-initializing database.")
        return initialize_db()
    except Exception as e:
        print(f"Could not read database file {DB_FILE_PATH}: {e}. Initializing.")
        return initialize_db()

def save_db(db_content: dict):
    """Saves the given dictionary to the database file."""
    try:
        with open(DB_FILE_PATH, 'w') as f:
            json.dump(db_content, f, indent=4)
        # print(f"Database saved successfully to {DB_FILE_PATH}") # Can be noisy
    except Exception as e:
        print(f"Error saving database to {DB_FILE_PATH}: {e}")

def initialize_db() -> dict:
    """Returns the structure for an empty database and saves it."""
    db_content = {
        "recipes": {},
        "last_gdrive_scan": None
    }
    save_db(db_content)
    return db_content

def get_recipe_status(recipe_id: str) -> dict | None:
    """Fetches the status and data for a specific recipe_id."""
    db = load_db()
    return db["recipes"].get(recipe_id)

def update_recipe_status(recipe_id: str, name: str, status: str, **kwargs):
    """
    Updates the status and other details for a recipe. 
    'recipe_id' is typically the Google Drive folder ID.
    'name' is the human-readable recipe name.
    Additional details can be passed via kwargs.
    """
    db = load_db()
    if recipe_id not in db["recipes"]:
        db["recipes"][recipe_id] = {"id": recipe_id} # Initialize if new
    
    db["recipes"][recipe_id]["name"] = name
    db["recipes"][recipe_id]["status"] = status
    db["recipes"][recipe_id]["last_updated"] = datetime.utcnow().isoformat()
    
    for key, value in kwargs.items():
        db["recipes"][recipe_id][key] = value
        
    save_db(db)
    print(f"Updated status for recipe ID '{recipe_id}' ({name}) to '{status}'. Details: {kwargs}")

def get_all_recipes_from_db() -> dict:
    """Returns the entire 'recipes' dictionary from the database."""
    db = load_db()
    return db.get("recipes", {})

def update_last_gdrive_scan_time():
    """Updates the last_gdrive_scan timestamp in the database."""
    db = load_db()
    db["last_gdrive_scan"] = datetime.utcnow().isoformat()
    save_db(db)

# --- Example Usage (can be run directly for testing utils.py) ---
if __name__ == '__main__':
    print(f"Using DB file at: {DB_FILE_PATH}")

    # Initialize or Load DB
    current_db = load_db()
    print(f"\nInitial DB Content:\n{json.dumps(current_db, indent=2)}")

    # Test adding/updating a recipe
    TEST_RECIPE_ID = "folder123_test_id"
    TEST_RECIPE_NAME = "Test Recipe Alpha"
    print(f"\nUpdating status for {TEST_RECIPE_NAME}...")
    update_recipe_status(TEST_RECIPE_ID, TEST_RECIPE_NAME, "downloaded", raw_clips_path="/videos/raw/Test_Recipe_Alpha")
    
    retrieved_status = get_recipe_status(TEST_RECIPE_ID)
    print(f"\nRetrieved status for {TEST_RECIPE_ID}:\n{json.dumps(retrieved_status, indent=2)}")

    TEST_RECIPE_ID_2 = "folder456_test_id"
    TEST_RECIPE_NAME_2 = "Test Recipe Beta - Merged"
    update_recipe_status(TEST_RECIPE_ID_2, TEST_RECIPE_NAME_2, "merged", 
                         raw_clips_path="/videos/raw/Test_Recipe_Beta", 
                         merged_video_path="/videos/merged/Test_Recipe_Beta.mp4")

    all_recipes = get_all_recipes_from_db()
    print(f"\nAll Recipes in DB:\n{json.dumps(all_recipes, indent=2)}")

    update_last_gdrive_scan_time()
    updated_db = load_db()
    print(f"\nDB Content after scan time update:\n{json.dumps(updated_db, indent=2)}")

    # Test failing a recipe
    update_recipe_status(TEST_RECIPE_ID, TEST_RECIPE_NAME, "failed", error_message="Simulated download failure.")
    retrieved_status_failed = get_recipe_status(TEST_RECIPE_ID)
    print(f"\nRetrieved status for failed {TEST_RECIPE_ID}:\n{json.dumps(retrieved_status_failed, indent=2)}")

    print("\nUtils testing complete.")
