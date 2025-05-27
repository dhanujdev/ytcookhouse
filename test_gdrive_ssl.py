from google.oauth2.service_account import Credentials as ServiceAccountCredentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
import os
import json # Not strictly used in this version, but good for consistency
from dotenv import load_dotenv
import traceback

print('Python GDrive SSL Test Script Initializing...')

# Assuming this script is in barged_api, and .env is also there.
APP_ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
dotenv_path = os.path.join(APP_ROOT_DIR, '.env')
load_dotenv(dotenv_path)
print(f'.env loaded from: {dotenv_path}')

SA_TYPE = os.getenv("SA_TYPE", "service_account")
SA_PROJECT_ID = os.getenv("SA_PROJECT_ID")
SA_PRIVATE_KEY_ID = os.getenv("SA_PRIVATE_KEY_ID")
SA_PRIVATE_KEY_ENV = os.getenv("SA_PRIVATE_KEY")
SA_CLIENT_EMAIL = os.getenv("SA_CLIENT_EMAIL")
SA_CLIENT_ID = os.getenv("SA_CLIENT_ID")
SA_AUTH_URI = os.getenv("SA_AUTH_URI", "https://accounts.google.com/o/oauth2/auth")
SA_TOKEN_URI = os.getenv("SA_TOKEN_URI", "https://oauth2.googleapis.com/token")
SA_AUTH_PROVIDER_X509_CERT_URL = os.getenv("SA_AUTH_PROVIDER_X509_CERT_URL", "https://www.googleapis.com/oauth2/v1/certs")
SA_CLIENT_X509_CERT_URL = os.getenv("SA_CLIENT_X509_CERT_URL")
GOOGLE_DRIVE_APP_DATA_FOLDER_NAME = os.getenv("GOOGLE_DRIVE_APP_DATA_FOLDER_NAME", "YTCookhouseAppData")

print(f'SA_PROJECT_ID: {SA_PROJECT_ID}, SA_CLIENT_EMAIL: {SA_CLIENT_EMAIL}, GOOGLE_DRIVE_APP_DATA_FOLDER_NAME: {GOOGLE_DRIVE_APP_DATA_FOLDER_NAME}')

# Check for missing essential SA variables
missing_vars_check = {
    "SA_PROJECT_ID": SA_PROJECT_ID,
    "SA_PRIVATE_KEY_ID": SA_PRIVATE_KEY_ID,
    "SA_PRIVATE_KEY_ENV": SA_PRIVATE_KEY_ENV,
    "SA_CLIENT_EMAIL": SA_CLIENT_EMAIL,
    "SA_CLIENT_ID": SA_CLIENT_ID,
    "SA_CLIENT_X509_CERT_URL": SA_CLIENT_X509_CERT_URL
}
actual_missing = [name for name, val in missing_vars_check.items() if not val]
if actual_missing:
    print(f"ERROR: Critical Service Account environment variables are missing: {actual_missing}")
    exit(1)

GOOGLE_SERVICE_ACCOUNT_INFO = {
    "type": SA_TYPE,
    "project_id": SA_PROJECT_ID,
    "private_key_id": SA_PRIVATE_KEY_ID,
    "private_key": SA_PRIVATE_KEY_ENV.replace('\\n', '\n') if SA_PRIVATE_KEY_ENV else None, # Corrected for direct use
    "client_email": SA_CLIENT_EMAIL,
    "client_id": SA_CLIENT_ID,
    "auth_uri": SA_AUTH_URI,
    "token_uri": SA_TOKEN_URI,
    "auth_provider_x509_cert_url": SA_AUTH_PROVIDER_X509_CERT_URL,
    "client_x509_cert_url": SA_CLIENT_X509_CERT_URL
}
SCOPES = ['https://www.googleapis.com/auth/drive']

def get_gdrive_service():
    creds = ServiceAccountCredentials.from_service_account_info(GOOGLE_SERVICE_ACCOUNT_INFO, scopes=SCOPES)
    return build('drive', 'v3', credentials=creds)

def test_get_or_create_app_data_folder(service, folder_name):
    query = f"name='{folder_name}' and mimeType='application/vnd.google-apps.folder' and 'root' in parents and trashed=false"
    try:
        response = service.files().list(q=query, spaces='drive', fields='files(id)').execute()
        folders = response.get('files', [])
        if folders:
            folder_id = folders[0].get('id')
            print(f"GDrive: Found existing App Data folder '{folder_name}' with ID: {folder_id}")
            return folder_id
        else:
            print(f"GDrive: App Data folder '{folder_name}' not found. Creating...")
            file_metadata = {'name': folder_name, 'mimeType': 'application/vnd.google-apps.folder'}
            folder = service.files().create(body=file_metadata, fields='id').execute()
            folder_id = folder.get('id')
            print(f"GDrive: Created App Data folder '{folder_name}' with ID: {folder_id}")
            return folder_id
    except HttpError as error:
        print(f"GDrive: An HttpError occurred while getting/creating folder '{folder_name}': {error}")
        raise
    except Exception as e:
        print(f"GDrive: An unexpected error while getting/creating folder '{folder_name}': {e}")
        raise

if __name__ == "__main__":
    print("Attempting to connect to GDrive and get/create app data folder...")
    g_service_instance = None
    try:
        g_service_instance = get_gdrive_service()
        print("Service client created.")
        
        folder_id_retrieved = test_get_or_create_app_data_folder(g_service_instance, GOOGLE_DRIVE_APP_DATA_FOLDER_NAME)
        if folder_id_retrieved:
            print(f"SUCCESS: App Data Folder ID is {folder_id_retrieved}")
        else:
            print("FAILURE: Could not get/create App Data Folder ID.")

        print("\nAttempting to list root folder content as a second call...")
        results = g_service_instance.files().list(pageSize=5, fields="files(id, name)").execute()
        items = results.get('files', [])
        print(f"Found {len(items)} items in root (or shared with me):")
        for item_info in items:
            print(f" - {item_info['name']} ({item_info['id']})")
        print("\nTest script completed successfully (no SSL error during these calls).")

    except Exception as e:
        print(f"\nTest script FAILED: {e}")
        print("--- Traceback ---")
        traceback.print_exc()
        print("-----------------")