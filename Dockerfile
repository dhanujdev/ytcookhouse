# Use an official Python runtime as a parent image
FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# Set the working directory in the container
WORKDIR /app

# Install system dependencies that ffmpeg might need (common ones)
# You might need to add more depending on the specifics of your ffmpeg build or other libraries
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Copy the requirements file into the container
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code into the container
COPY . .

# Render will automatically use the PORT environment variable,
# so explicitly exposing it here is good practice but Render handles it.
# EXPOSE 10000 # Or $PORT, but Render injects this

# The command to run your application
# Render will inject the $PORT variable
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "$PORT"]
