from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles # Added StaticFiles
from fastapi.templating import Jinja2Templates
import os

from routes import upload # Import the router from routes/upload.py

app = FastAPI()

# --- Startup Event for Service Initialization and Checks (Added for Refactoring) ---
# Placeholder imports - will be replaced with actual service modules and functions
from config import APP_STARTUP_STATUS, GDRIVE_SERVICE_CLIENT, YOUTUBE_SERVICE_CLIENT, GEMINI_SERVICE_CLIENT # Import shared clients
from services import gdrive, youtube_uploader, gemini # Assuming these modules exist with relevant functions

@app.on_event("startup")
async def startup_event():
    print("MAIN: Application startup event triggered.")
    # Initialize Google Drive Service
    print("MAIN: Initializing Google Drive Service...")
    # First, check critical configurations from config.py
    import config # Import to access GOOGLE_AUTH_METHOD, GDRIVE_TARGET_FOLDER_ID
    if config.GOOGLE_AUTH_METHOD in ["ERROR_JSON_CONTENT_PARSE", "ERROR_INSUFFICIENT_SA_DETAILS"] or not config.GOOGLE_SERVICE_ACCOUNT_INFO:
        APP_STARTUP_STATUS["gdrive_ready"] = False
        error_detail = f"GDrive Service cannot initialize due to Service Account configuration errors: {config.GOOGLE_AUTH_METHOD}. Check .env."
        APP_STARTUP_STATUS["gdrive_error_details"] = error_detail
        print(f"MAIN: CRITICAL - {error_detail}")
    elif not config.GDRIVE_TARGET_FOLDER_ID or config.GDRIVE_TARGET_FOLDER_ID == "...":
        APP_STARTUP_STATUS["gdrive_ready"] = False
        error_detail = "GDrive Service cannot initialize: GDRIVE_TARGET_FOLDER_ID is not set in .env."
        APP_STARTUP_STATUS["gdrive_error_details"] = error_detail
        print(f"MAIN: CRITICAL - {error_detail}")
    else:
        try:
            gdrive_client_instance = gdrive.create_gdrive_service()
            if gdrive.check_gdrive_service(gdrive_client_instance):
                config.GDRIVE_SERVICE_CLIENT = gdrive_client_instance
                APP_STARTUP_STATUS["gdrive_ready"] = True
                print("MAIN: Google Drive Service initialized, checked, and set as shared client.")
            else:
                # Error details should be set by check_gdrive_service itself if it fails
                APP_STARTUP_STATUS["gdrive_ready"] = False # Ensure it's false
                print(f"MAIN: ERROR - Google Drive Service check failed. Details: {APP_STARTUP_STATUS['gdrive_error_details']}")
        except Exception as e:
            APP_STARTUP_STATUS["gdrive_ready"] = False
            APP_STARTUP_STATUS["gdrive_error_details"] = str(e)
            print(f"MAIN: ERROR - Exception during Google Drive Service initialization: {e}")

    # Initialize YouTube Service
    print("MAIN: Initializing YouTube Service...")
    try:
        # For OAuth user consent, actual client creation might be deferred until user authorizes.
        # The create_youtube_service might raise YouTubeNeedsAuthorization if token doesn't exist.
        # We can't complete the flow during app startup without user interaction.
        # So, we check if a token already exists and is valid.
        # The redirect_uri for startup will be None, so it relies on existing token.
        youtube_client_instance = youtube_uploader.create_youtube_service(redirect_uri=None) 
        if youtube_uploader.check_youtube_service(youtube_client_instance):
            import config 
            config.YOUTUBE_SERVICE_CLIENT = youtube_client_instance 
            APP_STARTUP_STATUS["youtube_ready"] = True
            print("MAIN: YouTube Service successfully initialized from existing token and checked.")
        else:
            # check_youtube_service would have set youtube_error_details
            APP_STARTUP_STATUS["youtube_ready"] = False
            print(f"MAIN: ERROR - YouTube Service check failed with existing token. Details: {APP_STARTUP_STATUS['youtube_error_details']}")
    except youtube_uploader.YouTubeNeedsAuthorization as e:
        APP_STARTUP_STATUS["youtube_ready"] = False
        APP_STARTUP_STATUS["youtube_error_details"] = f"User authorization required: {e.authorization_url}"
        print(f"MAIN: YouTube Service needs user authorization. Visit the URL manually or via UI button if app is running.")
    except Exception as e:
        APP_STARTUP_STATUS["youtube_ready"] = False
        APP_STARTUP_STATUS["youtube_error_details"] = str(e)
        print(f"MAIN: ERROR - Exception during YouTube Service initialization: {e}")

    # Initialize Gemini Service
    print("MAIN: Initializing Gemini Service...")
    # First, check critical configurations from config.py (GEMINI_API_KEY)
    # config was already imported for GDrive checks.
    if not config.GEMINI_API_KEY or config.GEMINI_API_KEY == "...":
        APP_STARTUP_STATUS["gemini_ready"] = False
        error_detail = "Gemini Service cannot initialize: GEMINI_API_KEY is not set in .env."
        APP_STARTUP_STATUS["gemini_error_details"] = error_detail
        print(f"MAIN: CRITICAL - {error_detail}")
    elif config.GOOGLE_AUTH_METHOD in ["ERROR_JSON_CONTENT_PARSE", "ERROR_INSUFFICIENT_SA_DETAILS"] and not config.GEMINI_API_KEY.startswith("AIza"):
        # If Gemini API key is NOT a user API key (AIza...) and service account is broken, Gemini with SA will fail.
        # This is a heuristic; some Gemini usage might rely on SA for project context even with user API keys.
        # However, the primary check is the API key itself. This is a secondary consideration for SA-based Gemini features.
        APP_STARTUP_STATUS["gemini_ready"] = False
        error_detail = f"Gemini Service might fail: GEMINI_API_KEY is set, but Service Account has configuration errors ({config.GOOGLE_AUTH_METHOD})."
        APP_STARTUP_STATUS["gemini_error_details"] = error_detail
        print(f"MAIN: WARNING - {error_detail}") # Warning as it might still work with a user API key
        # Proceed to try initializing, check_gemini_service will give the final say.
        try:
            gemini_model_instance = gemini.create_gemini_model()
            if gemini.check_gemini_service():
                config.GEMINI_SERVICE_CLIENT = gemini_model_instance
                APP_STARTUP_STATUS["gemini_ready"] = True # It might become true if API key is user-based and valid
                print("MAIN: Gemini Service initialized and checked (potentially with user API key).")
            else:
                APP_STARTUP_STATUS["gemini_ready"] = False # Ensure it's false
                print(f"MAIN: ERROR - Gemini Service check failed. Details: {APP_STARTUP_STATUS['gemini_error_details']}")
        except Exception as e:
            APP_STARTUP_STATUS["gemini_ready"] = False
            APP_STARTUP_STATUS["gemini_error_details"] = str(e)
            print(f"MAIN: ERROR - Exception during Gemini Service initialization: {e}")
    else: # GEMINI_API_KEY is set, and no obvious immediate SA conflict for it
        try:
            gemini_model_instance = gemini.create_gemini_model()
            if gemini.check_gemini_service():
                config.GEMINI_SERVICE_CLIENT = gemini_model_instance
                APP_STARTUP_STATUS["gemini_ready"] = True
                print("MAIN: Gemini Service initialized, checked, and set as shared client.")
            else:
                APP_STARTUP_STATUS["gemini_ready"] = False # Ensure it's false
                print(f"MAIN: ERROR - Gemini Service initialization or check failed. Details: {APP_STARTUP_STATUS['gemini_error_details']}")
        except Exception as e:
            APP_STARTUP_STATUS["gemini_ready"] = False
            APP_STARTUP_STATUS["gemini_error_details"] = str(e)
            print(f"MAIN: ERROR - Exception during Gemini Service initialization: {e}")

    # Update overall readiness status
    if APP_STARTUP_STATUS["gdrive_ready"] and APP_STARTUP_STATUS["youtube_ready"] and APP_STARTUP_STATUS["gemini_ready"]:
        APP_STARTUP_STATUS["all_services_ready"] = True
        print("MAIN: All services checked. Application ready.")
    else:
        print("MAIN: WARNING - One or more services are not ready. Check error details.")
        print(f"MAIN: Startup Status: {APP_STARTUP_STATUS}")

# --- OAuth2 Callback Route for YouTube ---
# This needs to be added to a router, e.g., a new auth_router or existing upload.router
# For now, let's define it here and assume it will be added to a router.
# This is a simplified callback, real world might need more robust state handling.

# This state variable is a simplified way to pass the flow's state to the callback.
# In a real app, use a session or a more secure temporary store.
# For now, we can't easily share state between create_youtube_service and the callback route if they are in different modules
# without passing it through a global or similar mechanism. The InstalledAppFlow.fetch_token is blocking.
# The InstalledAppFlow is more suited for CLI apps. For web apps, google_auth_oauthlib.flow.Flow is better.
# The create_youtube_service has been adapted to raise YouTubeNeedsAuthorization which contains the auth_url.
# The actual token fetching will now happen in a dedicated auth route.


# Mount the upload router (which should contain the YouTube auth routes now)
app.include_router(upload.router)

from templating import templates # Import templates

# Mount static files directory for CSS, JS
# STATIC_DIR is defined in config as STATIC_DIR_CONFIG and created there.
# We use STATIC_DIR_CONFIG from config for mounting.
from config import STATIC_DIR_CONFIG
# The os.makedirs call for STATIC_DIR_CONFIG (via STATIC_DIR) is already in config.py
app.mount("/static", StaticFiles(directory=STATIC_DIR_CONFIG), name="static")

# The /videos_serve mount and VIDEO_FILES_DIR are removed as APP_VIDEOS_DIR was removed from config.
# Previews are handled by downloading to static/preview_cache and serving from /static.


@app.get("/", response_class=HTMLResponse, name="home")
async def home(request: Request):
    return templates.TemplateResponse("home.html", {"request": request})
