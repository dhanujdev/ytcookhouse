import json
import os
from datetime import datetime
import tempfile # For temporary local db file

import time # Added for cache timestamp

# Assuming config.py is in the parent directory
from config import (
    GOOGLE_DRIVE_APP_DATA_FOLDER_NAME, 
    DB_JSON_FILENAME_ON_DRIVE,
    # ---- Added for DB Caching ----
    CACHED_DB_CONTENT,
    DB_CACHE_TIMESTAMP,
    DB_CACHE_DURATION_SECONDS,
    # ---- Import the shared GDrive client ----
    GDRIVE_SERVICE_CLIENT
    # -----------------------------------------
)
# from services import gdrive # No longer import the whole module for get_gdrive_service here

# DB_FILE_PATH is no longer a static local path. db.json lives on Google Drive.

def load_db() -> dict:
    """Loads the database. Tries from cache first, then Google Drive. Initializes if not found or empty."""
    global CACHED_DB_CONTENT, DB_CACHE_TIMESTAMP # Allow modification of global cache variables

    # Check cache first
    if CACHED_DB_CONTENT and DB_CACHE_TIMESTAMP:
        cache_age = time.time() - DB_CACHE_TIMESTAMP
        if cache_age < DB_CACHE_DURATION_SECONDS:
            # print(f"UTILS: Returning DB from cache (age: {cache_age:.2f}s).") # Optional: for debugging
            return CACHED_DB_CONTENT
        else:
            print(f"UTILS: DB cache expired (age: {cache_age:.2f}s). Fetching from GDrive.")
    else:
        print("UTILS: No valid DB cache. Fetching from GDrive.")

    # If cache miss or expired, load from Google Drive
    print("UTILS: Attempting to load DB from Google Drive...")
    try:
        # Use the shared GDrive client from config
        if not GDRIVE_SERVICE_CLIENT:
            print("UTILS: ERROR - Shared GDrive service client not available. Cannot load DB.")
            # Fallback to a temporary in-memory DB if GDrive client is not initialized
            return {"recipes": {}, "last_gdrive_scan": None, "gdrive_error": "Shared GDrive client not initialized"}
        service = GDRIVE_SERVICE_CLIENT 
        
        # Since get_or_create_app_data_folder_id is part of gdrive.py, we still need gdrive module for it.
        # Let's ensure gdrive services is imported for its helper functions.
        from services import gdrive 
        app_data_folder_id = gdrive.get_or_create_app_data_folder_id(service=service)
        if not app_data_folder_id:
            print("UTILS: ERROR - Could not get/create app data folder on GDrive. Initializing local default DB.")
            return initialize_db() # This will attempt to save to GDrive, might fail if folder creation failed

        db_file_id = gdrive.find_file_id_by_name(app_data_folder_id, DB_JSON_FILENAME_ON_DRIVE, service=service)

        if db_file_id:
            print(f"UTILS: Found DB file on GDrive with ID: {db_file_id}. Fetching content...")
            db_content_str = gdrive.get_file_content_from_drive(db_file_id, service=service)
            if db_content_str:
                try:
                    db_data = json.loads(db_content_str)
                    if "recipes" not in db_data: # Basic validation
                        db_data["recipes"] = {}
                    print("UTILS: DB loaded successfully from GDrive.")
                    # Update cache
                    CACHED_DB_CONTENT = db_data
                    DB_CACHE_TIMESTAMP = time.time()
                    return db_data
                except json.JSONDecodeError as e:
                    print(f"UTILS: ERROR - Failed to decode JSON from GDrive DB file content: {e}. Initializing new DB.")
                    return initialize_db()
            else:
                print("UTILS: WARNING - DB file on GDrive is empty or unreadable. Initializing new DB.")
                return initialize_db()
        else:
            print(f"UTILS: DB file '{DB_JSON_FILENAME_ON_DRIVE}' not found in GDrive app folder. Initializing new DB.")
            return initialize_db()
    except gdrive.GDriveServiceError as e:
        print(f"UTILS: ERROR - GDriveServiceError while loading DB: {e}. Returning a temporary in-memory DB.")
        # Fallback to a temporary in-memory DB if GDrive is totally inaccessible
        return {"recipes": {}, "last_gdrive_scan": None, "gdrive_error": str(e)}
    except Exception as e:
        print(f"UTILS: ERROR - Unexpected error loading DB from GDrive: {e}. Returning temporary in-memory DB.")
        return {"recipes": {}, "last_gdrive_scan": None, "unexpected_error": str(e)}

def save_db(db_content: dict):
    """Saves the given dictionary to the database file on Google Drive and updates the cache."""
    global CACHED_DB_CONTENT, DB_CACHE_TIMESTAMP # Allow modification of global cache variables

    print("UTILS: Attempting to save DB to Google Drive...")
    try:
        if not GDRIVE_SERVICE_CLIENT:
            print("UTILS: ERROR - Shared GDrive service client not available. Cannot save DB.")
            return # Or raise an error
        service = GDRIVE_SERVICE_CLIENT

        from services import gdrive # For gdrive.get_or_create_app_data_folder_id etc.
        app_data_folder_id = gdrive.get_or_create_app_data_folder_id(service=service)
        if not app_data_folder_id:
            print("UTILS: ERROR - Could not get/create app data folder on GDrive. DB save failed.")
            return

        existing_db_file_id = gdrive.find_file_id_by_name(app_data_folder_id, DB_JSON_FILENAME_ON_DRIVE, service=service)
        
        # Create a temporary local file to upload
        temp_db_file = tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json')
        json.dump(db_content, temp_db_file, indent=4)
        local_temp_path = temp_db_file.name
        temp_db_file.close()

        print(f"UTILS: Saving DB to GDrive. Existing file ID: {existing_db_file_id}. Local temp: {local_temp_path}")
        uploaded_file_id = gdrive.upload_file_to_drive(
            local_file_path=local_temp_path,
            drive_folder_id=app_data_folder_id,
            drive_filename=DB_JSON_FILENAME_ON_DRIVE,
            mimetype='application/json',
            service=service,
            existing_file_id=existing_db_file_id
        )
        os.remove(local_temp_path) # Clean up temp file

        if uploaded_file_id:
            print(f"UTILS: DB saved successfully to GDrive. File ID: {uploaded_file_id}")
            # Update cache immediately after successful save
            CACHED_DB_CONTENT = db_content
            DB_CACHE_TIMESTAMP = time.time()
            print("UTILS: DB cache updated after save.")
        else:
            print("UTILS: ERROR - Failed to upload DB to GDrive.")

    except gdrive.GDriveServiceError as e:
        print(f"UTILS: ERROR - GDriveServiceError while saving DB: {e}")
    except Exception as e:
        print(f"UTILS: ERROR - Unexpected error saving DB to GDrive: {e}")

def initialize_db() -> dict:
    """Returns the structure for an empty database and attempts to save it to GDrive."""
    print("UTILS: Initializing new DB structure.")
    db_content = {
        "recipes": {},
        "last_gdrive_scan": None
    }
    save_db(db_content) # This will attempt to save the newly initialized DB to GDrive
    return db_content

def get_recipe_status(recipe_id: str) -> dict | None:
    db = load_db()
    return db.get("recipes", {}).get(recipe_id)

def update_recipe_status(recipe_id: str, name: str, status: str, **kwargs):
    db = load_db()
    if "recipes" not in db: # Should be handled by load_db, but as a safeguard
        db["recipes"] = {}
        
    if recipe_id not in db["recipes"]:
        db["recipes"][recipe_id] = {"id": recipe_id}
    
    db["recipes"][recipe_id]["name"] = name
    db["recipes"][recipe_id]["status"] = status
    db["recipes"][recipe_id]["last_updated"] = datetime.utcnow().isoformat()
    
    for key, value in kwargs.items():
        db["recipes"][recipe_id][key] = value
        
    save_db(db)
    print(f"UTILS: Updated status for recipe ID '{recipe_id}' ({name}) to '{status}'. Details: {kwargs}")

def get_all_recipes_from_db() -> dict:
    db = load_db()
    return db.get("recipes", {})

def update_last_gdrive_scan_time():
    db = load_db()
    db["last_gdrive_scan"] = datetime.utcnow().isoformat()
    save_db(db)

def reset_recipe_in_db(recipe_id: str):
    """Resets a recipe's status and associated processing fields in the database to a 'New' state."""
    db = load_db()
    if "recipes" not in db or recipe_id not in db["recipes"]:
        print(f"UTILS: Cannot reset recipe. ID '{recipe_id}' not found in DB.")
        return False

    original_name = db["recipes"][recipe_id].get("name", "Unknown Recipe") 
    print(f"UTILS: Resetting recipe ID '{recipe_id}' ('{original_name}') to 'New' state.")

    # Preserve original ID and name, clear everything else relevant to processing state
    db["recipes"][recipe_id] = {
        "id": recipe_id,
        "name": original_name,
        "status": "New",
        "last_updated": datetime.utcnow().isoformat(),
        "raw_clips_path": None,
        "merged_video_gdrive_id": None,
        "metadata_gdrive_id": None,
        "youtube_url": None,
        "error_message": None,
        # Add any other fields that should be cleared upon reset
        # e.g., 'merged_video_path': None, 'metadata_file_path': None, if you ever store them
    }
    save_db(db)
    print(f"UTILS: Recipe ID '{recipe_id}' successfully reset.")
    return True

if __name__ == '__main__':
    print("Testing GDrive-backed utils.py...")
    # Test load (will try to init on GDrive if not exists)
    test_db = load_db()
    print(f"Initial loaded DB: {json.dumps(test_db, indent=2)}")

    # Test update
    TEST_RECIPE_ID = "gdrive_utils_test_recipe_001"
    TEST_RECIPE_NAME = "GDrive Utils Test Recipe"
    update_recipe_status(TEST_RECIPE_ID, TEST_RECIPE_NAME, "test_status", test_arg="hello_gdrive")

    retrieved = get_recipe_status(TEST_RECIPE_ID)
    print(f"Retrieved after update: {json.dumps(retrieved, indent=2)}")
    if retrieved and retrieved.get("test_arg") == "hello_gdrive":
        print("SUCCESS: Test update and retrieval seems to work with GDrive DB.")
    else:
        print("FAILURE: Test update/retrieval issue with GDrive DB.")
    print("GDrive-backed utils.py testing finished.")

def hard_reset_db_content() -> bool:
    """Resets the entire DB to an initial empty state on Google Drive and clears the cache."""
    # Ensure global config variables for cache are accessible if not already imported as such
    # For direct modification, it might be cleaner to import config and use config.VAR
    # However, if CACHED_DB_CONTENT and DB_CACHE_TIMESTAMP are already treated as module-level globals
    # that `load_db` and `save_db` modify via `global` keyword, this approach is consistent.
    # Let's assume they are module globals that can be modified by other functions here.
    
    # To be absolutely safe and explicit with global config vars:
    import config 
    
    print("UTILS: Performing HARD RESET of the database.")
    initial_db = {
        "recipes": {},
        "last_gdrive_scan": None
    }
    save_db(initial_db) # This will save to GDrive and should update the cache via its own logic
    
    # Explicitly set cache to the reset state immediately after save_db call returns.
    # save_db already updates these, but doing it here ensures it, even if save_db changes.
    config.CACHED_DB_CONTENT = initial_db 
    config.DB_CACHE_TIMESTAMP = time.time()
    print("UTILS: Database hard reset complete. Cache also reset.")
    return True

