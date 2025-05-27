from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles # Added StaticFiles
from fastapi.templating import Jinja2Templates
import os

from routes import upload # Import the router from routes/upload.py

app = FastAPI()

# Mount the upload router
app.include_router(upload.router)

# Configure templates
TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), 'templates')
templates = Jinja2Templates(directory=TEMPLATES_DIR)

# Mount static files directory for CSS, JS
STATIC_DIR = os.path.join(os.path.dirname(__file__), 'static')
if not os.path.exists(STATIC_DIR):
    os.makedirs(STATIC_DIR) # Ensure static directory exists
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# Mount videos directory to allow video playback in preview
# IMPORTANT: This makes your 'videos' directory publicly accessible if the server is exposed.
# For production, consider a more secure way to serve or stream private videos.
VIDEO_FILES_DIR = os.path.join(os.path.dirname(__file__), 'videos') # Assuming 'videos' is at the root of barged_api
# Video directories are now created in config.py
app.mount("/videos_serve", StaticFiles(directory=VIDEO_FILES_DIR), name="videos_serve")


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("home.html", {"request": request})
