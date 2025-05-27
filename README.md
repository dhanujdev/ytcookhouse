# 📦 Barged API Server: Detailed Project Plan & Spec

## 🧠 Project Goal

Build a FastAPI-based server called **Barged API** that:

* Automatically scans a Google Drive folder containing recipe video clips
* Merges and replaces audio in the videos
* Sends the merged video to Gemini for YouTube metadata generation
* Previews the result in a UI
* Uploads the final video to YouTube upon confirmation

---

## 📂 Project Structure

```
barged_api/
├── main.py                      # FastAPI entry point
├── routes/
│   └── upload.py                # API endpoints
├── services/
│   ├── gdrive.py                # GDrive folder scanning
│   ├── video_editor.py          # Merging & replacing music
│   ├── gemini.py                # Prompt + YouTube metadata
│   └── youtube_uploader.py      # Upload to YouTube
├── templates/
│   └── home.html                # Home UI to trigger workflows
│   └── preview.html             # Jinja2 or frontend preview
│   └── select_folder.html       # Step-by-step folder selection UI
├── static/                      # JS, CSS
├── config.py                    # API keys, paths
├── db.json                      # State tracking of processed folders
├── requirements.txt
└── .env                         # Credentials and keys
```

---

## ⚙️ Functional Flow

### 1. Google Drive Folder Scanning

* Target: Specific folder ID
* Scan for subfolders (each representing a recipe)
* Each folder should contain sequential video clips (e.g., 1.mp4, 2.mp4...)
* Check processed state via `db.json`
* Download unprocessed folders to `/videos/raw/<recipe_name>/`
* UI lists available folders and lets user select one to process

### 2. Merge & Replace Music

* Merge all videos into one using FFmpeg
* Replace original audio with random royalty-free background music
* Save to `/videos/merged/<recipe_name>.mp4`

### 3. Gemini Metadata Generation

* Send merged video reference to Gemini with detailed prompt
* Expected output: structured JSON with title, description, tags, chapters, transcript
* Save metadata to `/videos/output/<recipe_name>.json`

### 4. Preview Interface

* Render video preview (`<video>` tag)
* Display auto-generated metadata
* Include an editable form and "Submit to YouTube" button

### 5. Upload to YouTube

* Use YouTube Data API v3 with OAuth2
* Upload video and apply metadata
* Save YouTube link to `db.json`

---

## 🌐 UI Capabilities

* Built-in using FastAPI + Jinja2 + static HTML/JS/CSS

* Step-by-step guidance for the user:

  1. `/` → Home: Introduction and Start button
  2. `/select_folder` → Lists all unprocessed folders in Google Drive

     * User selects one folder to process
     * Triggers `/fetch_clips` to download
  3. Auto-redirects to `/merge_and_process` upon confirmation
  4. Then, redirects to `/preview` after metadata generation
  5. User can edit and submit to `/upload_youtube`

* Interactive elements:

  * Folder picker (radio buttons or dropdown)
  * Status feedback (downloaded, merged, metadata ready, uploaded)

---

## 📁 Storage Layout

```
/videos/
├── raw/        # Downloaded clips from GDrive
├── merged/     # Single video with replaced audio
└── output/     # Metadata JSON and upload tracking
```

---

## 🔌 Endpoints Overview

| Endpoint             | Method | Description                          |
| -------------------- | ------ | ------------------------------------ |
| `/fetch_clips`       | POST   | Download selected folder contents    |
| `/merge_and_process` | POST   | Merge and replace music              |
| `/generate_metadata` | POST   | Gemini-based metadata generation     |
| `/preview`           | GET    | Preview page with editable metadata  |
| `/upload_youtube`    | POST   | Upload final video to YouTube        |
| `/select_folder`     | GET    | Render UI for choosing GDrive folder |

---

## 🛡️ Security

* API Keys and secrets stored in `.env`
* YouTube uses OAuth2, token refresh handled
* Gemini API secured via key

---

## 📝 Configurable Settings (via `config.py`)

```python
GDRIVE_FOLDER_ID = "..."
VIDEO_DIR = "videos"
RAW_DIR = os.path.join(VIDEO_DIR, "raw")
MERGED_DIR = os.path.join(VIDEO_DIR, "merged")
OUTPUT_DIR = os.path.join(VIDEO_DIR, "output")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
YOUTUBE_CREDENTIALS = os.getenv("YT_CREDS")
```

---

## 🔄 Feedback Loops & Enhancements (Future)

* AI Critic Agent: Review Gemini metadata and refine prompt automatically
* Batch processing of multiple recipes in parallel
* Dashboard view of past uploads and status
* Optional Webhooks or email alerts after upload
* Public sharing page with embedded YouTube player

---

## ✅ Immediate Tasks to Start (Progress Update)

1.  Set up FastAPI boilerplate with `main.py` - **DONE (basic setup)**
2.  Create `/select_folder` page and fetch available folders via `gdrive.py` - **DONE (simulated GDrive fetch, UI implemented)**
3.  Connect selected folder to `/fetch_clips` (download) - **DONE (simulated download, UI flow implemented)**
4.  Build `video_editor.py` to handle merging - **DONE (simulated merging, placeholder function created)**
5.  Add UI in `templates/select_folder.html` and `templates/home.html` - **DONE**
6.  Commit minimal working MVP to Render for testing - **PENDING (Manual Step)**

---

## 🚀 Running the Application

### Local Development Setup

1.  **Clone the Repository (if you haven't):**
    ```bash
    # git clone https://github.com/dhanujdev/ytcookhouse.git
    # cd ytcookhouse/barged_api 
    # Assuming barged_api is the root of this project within the repo
    ```

2.  **Create a Python Virtual Environment:**
    ```bash
    python -m venv venv
    # Activate it:
    # Windows:
    venv\Scripts\activate
    # macOS/Linux:
    # source venv/bin/activate
    ```

3.  **Install Dependencies:**
    ```bash
    pip install -r requirements.txt
    ```

4.  **Set up Environment Variables:**
    *   Copy `.env.example` to a new file named `.env` in the `barged_api` directory.
    *   Edit `.env` and fill in your actual API keys, Google Cloud project credentials (Service Account JSON content or path, or OAuth client secret filename), GDrive Folder ID, etc., as per the comments in `.env.example`.
    *   Ensure any necessary JSON key files (like an OAuth `client_secret_*.json` if using that method locally, or a service account key if using the file path method for SA) are placed in the `barged_api` root directory.

5.  **FFmpeg**: Ensure FFmpeg (and ffprobe) is installed on your system and accessible in your system's PATH.

6.  **(Optional) Background Music**: To test with custom background music, place `.mp3` files in the `barged_api/static/audio/` directory. If no MP3s are found, a sine wave will be generated as placeholder audio.

### Running Locally

Once set up, run the FastAPI server using Uvicorn from the `barged_api` directory:

```bash
uvicorn main:app --reload
```

The application will typically be available at `http://127.0.0.1:8000`.

### Deployment (e.g., to Render.com)

1.  Push your code to your GitHub repository.
2.  On Render.com, create a new Web Service and connect it to your GitHub repository.
3.  **Build Command**: Render might auto-detect Python; if not, it's usually `pip install -r requirements.txt`.
4.  **Start Command**: Render will use the `Procfile`: `web: uvicorn main:app --host 0.0.0.0 --port $PORT`.
5.  **Environment Variables**: In Render's dashboard for your service, go to "Environment" and add all the necessary environment variables as defined in your `.env.example` (especially `GEMINI_API_KEY`, `GOOGLE_SERVICE_ACCOUNT_CREDENTIALS_JSON_CONTENT` (paste the full JSON here), `GDRIVE_TARGET_FOLDER_ID`).
6.  **Persistent Storage (Disk on Render)**: You will need to configure a persistent disk on Render for:
    *   The `videos` directory (to store raw, merged, output files).
    *   The `db.json` file (to persist application state).
    *   Mount this disk to a path like `/opt/render/project/src/data` (Render's default persistent path area) or similar, and adjust `config.py` if necessary to point `VIDEO_DIR` and `DB_FILE_PATH` to this persistent location. This might involve making paths in `config.py` configurable via environment variables themselves or adapting them based on a `RENDER_DISK_MOUNT_PATH` environment variable.
        *   For example, `VIDEO_DIR = os.getenv("PERSISTENT_VIDEO_DIR", os.path.join(BASE_DIR, "videos"))`.

Let me know when ready to start coding step-by-step.
