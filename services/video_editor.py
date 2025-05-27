import os
import subprocess
import sys
import glob
import re
import random
import tempfile
import shutil

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
# Import new config vars. LOCAL_TEMP_MERGED_DIR is now just MERGED_DIR from config.
from config import TEMP_PROCESSING_BASE_DIR, MERGED_DIR, GOOGLE_DRIVE_APP_DATA_FOLDER_NAME 
from utils import update_recipe_status
from services import gdrive # Import gdrive service

class VideoEditingError(Exception):
    pass

def get_ffmpeg_tool_path(tool_name: str = "ffmpeg") -> str:
    try:
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        subprocess.run([tool_name, "-version"], capture_output=True, check=True, text=True, creationflags=creationflags)
        print(f"BACKGROUND TASK: {tool_name.capitalize()} found in PATH: '{tool_name}'")
        return tool_name
    except FileNotFoundError:
        raise VideoEditingError(f"'{tool_name}' not found.")
    except subprocess.CalledProcessError as e:
        raise VideoEditingError(f"{tool_name.capitalize()} version check failed: {e.stderr}")

def get_video_duration(video_path: str, ffprobe_cmd: str) -> float:
    command = [ffprobe_cmd, "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", video_path]
    try:
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        result = subprocess.run(command, capture_output=True, text=True, check=True, creationflags=creationflags)
        return float(result.stdout.strip()) if result.stdout.strip() and result.stdout.strip() != "N/A" else 0.0
    except Exception:
        return 0.0

def natural_sort_key(s):
    return [int(text) if text.isdigit() else text.lower() for text in re.split(r'([0-9]+)', os.path.basename(s))]

MIN_CLIP_DURATION_SECONDS = 0.2
PREPROCESS_IF_SHORTER_THAN_SECONDS = 1.5
DEFAULT_PREPROCESS_FPS = "30"
DEFAULT_PREPROCESS_RESOLUTION = "1280x720"

def merge_videos_and_replace_audio(relative_raw_clips_path_from_db: str, recipe_db_id: str, recipe_name_orig: str):
    # relative_raw_clips_path_from_db is the path stored in db.json, relative to TEMP_PROCESSING_BASE_DIR.
    absolute_raw_clips_local_path = os.path.join(TEMP_PROCESSING_BASE_DIR, relative_raw_clips_path_from_db)
    print(f"BACKGROUND TASK: VideoEditor: Starting for {recipe_db_id} ({recipe_name_orig}). Relative raw clips path: '{relative_raw_clips_path_from_db}', Absolute: '{absolute_raw_clips_local_path}'")
    
    ffmpeg_cmd, ffprobe_cmd = "", ""
    temp_preprocess_dir_local = ""
    files_to_delete_locally = []
    current_db_status_on_exit = "MERGE_FAILED" 
    error_message_on_exit = "Unknown merge error"
    # final_merged_path_for_db is now final_merged_gdrive_file_id
    final_merged_gdrive_file_id = None 
    local_final_output_path = None # Keep track of the local final file before upload & cleanup

    try:
        gdrive_service = gdrive.get_gdrive_service()
        app_data_folder_id = gdrive.get_or_create_app_data_folder_id(service=gdrive_service)
        if not app_data_folder_id:
            raise VideoEditingError("Could not get/create GDrive App Data Folder for storing merged video.")
        
        recipe_merged_video_gdrive_folder_id = gdrive.get_or_create_recipe_subfolder_id(
            app_data_folder_id, recipe_db_id, "merged_videos", service=gdrive_service
        )
        if not recipe_merged_video_gdrive_folder_id:
            raise VideoEditingError(f"Could not get/create GDrive subfolder for merged videos for recipe {recipe_db_id}")

        ffmpeg_cmd = get_ffmpeg_tool_path("ffmpeg")
        ffprobe_cmd = get_ffmpeg_tool_path("ffprobe")

        if not os.path.isdir(absolute_raw_clips_local_path):
            raise VideoEditingError(f"Absolute raw clips local dir not found: {absolute_raw_clips_local_path}")

        video_extensions = ('*.mp4', '*.MP4', '*.mov', '*.MOV', '*.avi', '*.AVI', '*.mkv', '*.MKV')
        unique_clip_paths = {os.path.normpath(p) for ext in video_extensions for p in glob.glob(os.path.join(absolute_raw_clips_local_path, ext))}

        if not unique_clip_paths:
            raise VideoEditingError(f"No video files found in local raw clips dir {absolute_raw_clips_local_path}")

        # Create preprocess dir inside the TEMP_PROCESSING_BASE_DIR for better organization if desired
        # or keep it inside absolute_raw_clips_local_path if that's preferred for co-location.
        # For simplicity, let's keep it within the specific recipe's raw clips folder.
        temp_preprocess_dir_local = tempfile.mkdtemp(prefix="barged_preprocess_", dir=absolute_raw_clips_local_path)
        files_to_delete_locally.append(temp_preprocess_dir_local)
        
        clips_for_concat_list = []
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0

        for clip_path in sorted(list(unique_clip_paths), key=natural_sort_key):
            # ... (pre-processing logic remains the same, using local paths) ...
            duration = get_video_duration(clip_path, ffprobe_cmd)
            base_name = os.path.basename(clip_path)
            if duration < MIN_CLIP_DURATION_SECONDS: continue
            if duration < PREPROCESS_IF_SHORTER_THAN_SECONDS:
                preprocessed_clip_name = f"preprocessed_{base_name}"
                preprocessed_clip_path = os.path.join(temp_preprocess_dir_local, preprocessed_clip_name)
                # ... (ffmpeg pre-process call) ...
                preprocess_cmd_args = [ffmpeg_cmd, '-y', '-i', clip_path, '-c:v', 'libx264', '-preset', 'medium', '-crf', '22', '-pix_fmt', 'yuv420p', '-r', DEFAULT_PREPROCESS_FPS, '-s', DEFAULT_PREPROCESS_RESOLUTION, '-an', preprocessed_clip_path]
                try:
                    subprocess.run(preprocess_cmd_args, check=True, capture_output=True, text=True, timeout=120, creationflags=creationflags)
                    clips_for_concat_list.append(preprocessed_clip_path)
                except Exception as e_pre:
                     print(f"BACKGROUND TASK: VideoEditor: WARN Pre-processing {base_name} failed: {e_pre}. Excluding.")
            else:
                clips_for_concat_list.append(clip_path)
        
        if not clips_for_concat_list:
            raise VideoEditingError(f"No clips remaining after filtering/pre-processing.")

        # MERGED_DIR from config is already absolute and env-specific
        # os.makedirs(MERGED_DIR, exist_ok=True) # config.py handles this now

        safe_recipe_name = "".join(c if c.isalnum() else "_" for c in recipe_name_orig)
        local_intermediate_merged_filename = f"{safe_recipe_name}_merged_silent_temp.mp4"
        local_intermediate_merged_path = os.path.join(MERGED_DIR, local_intermediate_merged_filename)
        files_to_delete_locally.append(local_intermediate_merged_path)
        
        gdrive_final_output_filename = f"{safe_recipe_name}_final.mp4" # Filename on Google Drive
        local_final_output_path = os.path.join(MERGED_DIR, gdrive_final_output_filename) # Local path before upload
        files_to_delete_locally.append(local_final_output_path) # Will be cleaned up after upload

        list_file_path = os.path.join(temp_preprocess_dir_local, "ffmpeg_filelist.txt")
        files_to_delete_locally.append(list_file_path)

        with open(list_file_path, 'w') as lf:
            for clip_path in clips_for_concat_list:
                lf.write(f"file '{clip_path.replace(os.sep, '/')}'\n")
        
        ffmpeg_merge_cmd_args = [ffmpeg_cmd, '-y', '-f', 'concat', '-safe', '0', '-i', list_file_path, '-c:v', 'libx264', '-preset', 'medium', '-crf', '23', '-pix_fmt', 'yuv420p', '-an', local_intermediate_merged_path]
        process = subprocess.Popen(ffmpeg_merge_cmd_args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, creationflags=creationflags)
        stdout, stderr = process.communicate(timeout=900) # Allow 15 mins for merge
        if process.returncode != 0:
            raise VideoEditingError(f"Main FFmpeg silent merge failed. RC: {process.returncode}\nStderr: {stderr[:1000]}")
        
        print(f"BACKGROUND TASK: VideoEditor: Silent merge to local temp successful: {local_intermediate_merged_path}")

        # --- Add Audio --- (outputs to local_final_output_path)
        static_audio_dir = os.path.join(os.path.dirname(__file__), '..', 'static', 'audio')
        available_music_files = [os.path.join(static_audio_dir, f) for f in os.listdir(static_audio_dir) if f.lower().endswith('.mp3')] if os.path.exists(static_audio_dir) else []
        selected_music_path = random.choice(available_music_files) if available_music_files else None
        audio_cmd_args = []
        if selected_music_path:
            audio_cmd_args = [ffmpeg_cmd, '-y', '-i', local_intermediate_merged_path, '-i', selected_music_path, '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k', '-map', '0:v:0', '-map', '1:a:0', '-shortest', local_final_output_path]
        else:
            audio_cmd_args = [ffmpeg_cmd, '-y', '-i', local_intermediate_merged_path, '-f', 'lavfi', '-i', "sine=frequency=1000", '-c:v', 'copy', '-c:a', 'aac', '-b:a', '128k', '-map', '0:v:0', '-map', '1:a:0', '-shortest', local_final_output_path]
        
        audio_process = subprocess.Popen(audio_cmd_args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, creationflags=creationflags)
        audio_stdout, audio_stderr = audio_process.communicate(timeout=300)
        if audio_process.returncode != 0:
            # If audio fails, we might still consider the silent merged video as partially successful.
            # For now, let's treat it as a full merge failure if audio can't be added.
            raise VideoEditingError(f"FFmpeg audio addition failed. RC: {audio_process.returncode}\nStderr: {audio_stderr[:500]}")
        print(f"BACKGROUND TASK: VideoEditor: Audio addition to local temp successful: {local_final_output_path}")

        # --- Upload final video to Google Drive ---
        print(f"BACKGROUND TASK: VideoEditor: Uploading {local_final_output_path} to GDrive folder {recipe_merged_video_gdrive_folder_id} as {gdrive_final_output_filename}")
        
        # Check if a file with the same name already exists in the target GDrive folder to update it
        existing_gdrive_file_id = gdrive.find_file_id_by_name(recipe_merged_video_gdrive_folder_id, gdrive_final_output_filename, service=gdrive_service)
        
        final_merged_gdrive_file_id = gdrive.upload_file_to_drive(
            local_file_path=local_final_output_path,
            drive_folder_id=recipe_merged_video_gdrive_folder_id,
            drive_filename=gdrive_final_output_filename,
            mimetype='video/mp4',
            service=gdrive_service,
            existing_file_id=existing_gdrive_file_id
        )
        if not final_merged_gdrive_file_id:
            raise VideoEditingError(f"Failed to upload merged video to Google Drive.")
        
        print(f"BACKGROUND TASK: VideoEditor: Successfully uploaded merged video to GDrive. File ID: {final_merged_gdrive_file_id}")
        current_db_status_on_exit = "MERGED"
        error_message_on_exit = None

    except VideoEditingError as ve:
        error_message_on_exit = str(ve)
        print(f"BACKGROUND TASK: VideoEditor: VideoEditingError: {error_message_on_exit}")
    except Exception as e:
        error_message_on_exit = f"Overall error in VideoEditor: {str(e)}"
        print(f"BACKGROUND TASK: VideoEditor: Unexpected Exception: {error_message_on_exit}")
    
    finally:
        kwargs_for_status_update = {}
        if final_merged_gdrive_file_id and current_db_status_on_exit == "MERGED":
            kwargs_for_status_update['merged_video_gdrive_id'] = final_merged_gdrive_file_id
            # Remove the old local path if it exists in DB, GDrive ID is king now
            kwargs_for_status_update['merged_video_path'] = None 
        if error_message_on_exit and current_db_status_on_exit == "MERGE_FAILED":
            kwargs_for_status_update['error_message'] = error_message_on_exit
        
        update_recipe_status(
            recipe_id=recipe_db_id, 
            name=recipe_name_orig, 
            status=current_db_status_on_exit, 
            **kwargs_for_status_update
        )
        print(f"BACKGROUND TASK: VideoEditor: Final DB status for {recipe_db_id} set to '{current_db_status_on_exit}'. GDrive ID: {final_merged_gdrive_file_id if final_merged_gdrive_file_id else 'N/A'}, Error: {error_message_on_exit if error_message_on_exit else 'None'}")

        for item_path in files_to_delete_locally:
            try:
                if os.path.exists(item_path):
                    if os.path.isdir(item_path):
                        shutil.rmtree(item_path)
                    else:
                        os.remove(item_path)
                    print(f"BACKGROUND TASK: VideoEditor: Cleaned local temp: {item_path}")
            except Exception as e_clean:
                print(f"BACKGROUND TASK: VideoEditor: WARN Failed to clean local temp {item_path}: {e_clean}")
        
        # The calling background task manager in routes/upload.py 
        # will use trigger_next_background_task if this step was successful.
        # This function itself no longer returns a path directly for chaining.

# __main__ block for testing would need significant rework to use GDrive for DB and outputs.
# For now, focusing on the main function logic.
