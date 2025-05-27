import os
from fastapi.templating import Jinja2Templates

# Configure templates
TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), 'templates')
templates = Jinja2Templates(directory=TEMPLATES_DIR)
