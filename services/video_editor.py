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
    except subprocess.CalledProcessError as e:
        print(f"FFprobe error getting duration for {video_path}: {e.stderr}")
        return 0.0
    except FileNotFoundError:
        print(f"FFprobe command '{ffprobe_cmd}' not found while getting duration for {video_path}.")
        return 0.0
    except ValueError:
        print(f"Could not convert FFprobe duration output to float for {video_path}.")
        return 0.0
    except Exception as e: # Catch any other unexpected error
        print(f"Unexpected error in get_video_duration for {video_path}: {e}")
        return 0.0

def natural_sort_key(s):
    return [int(text) if text.isdigit() else text.lower() for text in re.split(r'([0-9]+)', os.path.basename(s))]

MIN_CLIP_DURATION_SECONDS = 0.2
PREPROCESS_IF_SHORTER_THAN_SECONDS = 1.5
DEFAULT_PREPROCESS_FPS = "30"
DEFAULT_PREPROCESS_RESOLUTION = "1280x720"

# Forward declare BackgroundTasks for type hinting if not already imported
from fastapi import BackgroundTasks
from services import gemini # Ensure gemini service is importable

def merge_videos_and_replace_audio(background_tasks: BackgroundTasks, relative_raw_clips_path_from_db: str, recipe_db_id: str, recipe_name_orig: str):
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
        print(f"BACKGROUND TASK: VideoEditor: DEBUG - Top of try block for {recipe_db_id}")
        # ---- DIAGNOSTIC STEP: Use a fresh GDrive client for this background task ----
        print("BACKGROUND TASK: VideoEditor: Obtaining a fresh GDrive service client for this task.")
        # Call the new factory function to get a new instance for this task
        task_specific_gdrive_service = gdrive.create_gdrive_service() 
        # ---- END DIAGNOSTIC STEP ----
        print(f"BACKGROUND TASK: VideoEditor: DEBUG - GDrive service client obtained for {recipe_db_id}")

        # Use the task_specific_gdrive_service for GDrive operations within this function
        app_data_folder_id = gdrive.get_or_create_app_data_folder_id(service=task_specific_gdrive_service)
        if not app_data_folder_id:
            raise VideoEditingError("Could not get/create GDrive App Data Folder for storing merged video.")
        
        recipe_merged_video_gdrive_folder_id = gdrive.get_or_create_recipe_subfolder_id(
            app_data_folder_id, recipe_db_id, "merged_videos", service=task_specific_gdrive_service
        )
        if not recipe_merged_video_gdrive_folder_id:
            raise VideoEditingError(f"Could not get/create GDrive subfolder for merged videos for recipe {recipe_db_id}")

        ffmpeg_cmd = get_ffmpeg_tool_path("ffmpeg")
        ffprobe_cmd = get_ffmpeg_tool_path("ffprobe")
        print(f"BACKGROUND TASK: VideoEditor: DEBUG - FFmpeg path: {ffmpeg_cmd}, FFprobe path: {ffprobe_cmd} for {recipe_db_id}")

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
        print(f"BACKGROUND TASK: VideoEditor: DEBUG - Starting clip preprocessing loop for {recipe_db_id}.")

        for clip_path in sorted(list(unique_clip_paths), key=natural_sort_key):
            print(f"BACKGROUND TASK: VideoEditor: DEBUG - Processing clip: {clip_path} for {recipe_db_id}")
            duration = get_video_duration(clip_path, ffprobe_cmd)
            base_name = os.path.basename(clip_path)
            print(f"BACKGROUND TASK: VideoEditor: DEBUG - Clip: {base_name}, Duration: {duration}s for {recipe_db_id}")

            if duration < MIN_CLIP_DURATION_SECONDS: 
                print(f"BACKGROUND TASK: VideoEditor: DEBUG - Skipping clip {base_name} (too short) for {recipe_db_id}")
                continue
            if duration < PREPROCESS_IF_SHORTER_THAN_SECONDS:
                preprocessed_clip_name = f"preprocessed_{base_name}"
                preprocessed_clip_path = os.path.join(temp_preprocess_dir_local, preprocessed_clip_name)
                preprocess_cmd_args = [ffmpeg_cmd, '-y', '-i', clip_path, '-c:v', 'libx264', '-preset', 'medium', '-crf', '22', '-pix_fmt', 'yuv420p', '-r', DEFAULT_PREPROCESS_FPS, '-s', DEFAULT_PREPROCESS_RESOLUTION, '-an', preprocessed_clip_path]
                print(f"BACKGROUND TASK: VideoEditor: DEBUG - Preprocessing {base_name} with command: {' '.join(preprocess_cmd_args)} for {recipe_db_id}")
                try:
                    subprocess.run(preprocess_cmd_args, check=True, capture_output=True, text=True, timeout=120, creationflags=creationflags)
                    print(f"BACKGROUND TASK: VideoEditor: DEBUG - Preprocessing successful for {base_name} to {preprocessed_clip_path} for {recipe_db_id}")
                    clips_for_concat_list.append(preprocessed_clip_path)
                except Exception as e_pre:
                     print(f"BACKGROUND TASK: VideoEditor: WARN Pre-processing {base_name} failed: {e_pre}. Excluding for {recipe_db_id}.")
            else:
                print(f"BACKGROUND TASK: VideoEditor: DEBUG - Adding original clip to list: {clip_path} for {recipe_db_id}")
                clips_for_concat_list.append(clip_path)
        
        print(f"BACKGROUND TASK: VideoEditor: DEBUG - Finished clip preprocessing loop for {recipe_db_id}. Clips for concat: {clips_for_concat_list}")
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
        print(f"BACKGROUND TASK: VideoEditor: DEBUG - Created ffmpeg_filelist.txt at {list_file_path} for {recipe_db_id}")
        
        ffmpeg_merge_cmd_args = [ffmpeg_cmd, '-y', '-f', 'concat', '-safe', '0', '-i', list_file_path, '-c:v', 'libx264', '-preset', 'medium', '-crf', '23', '-pix_fmt', 'yuv420p', '-an', local_intermediate_merged_path]
        print(f"BACKGROUND TASK: VideoEditor: DEBUG - Starting silent merge. Command: {' '.join(ffmpeg_merge_cmd_args)} for {recipe_db_id}")
        process = subprocess.Popen(ffmpeg_merge_cmd_args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, creationflags=creationflags)
        
        print(f"BACKGROUND TASK: VideoEditor: DEBUG - Waiting for silent merge FFmpeg process to complete (timeout 900s) for {recipe_db_id}...")
        stdout, stderr = process.communicate(timeout=900) # Allow 15 mins for merge
        print(f"BACKGROUND TASK: VideoEditor: DEBUG - Silent merge FFmpeg process finished for {recipe_db_id}. RC: {process.returncode}")
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
            print(f"BACKGROUND TASK: VideoEditor: DEBUG - No music file found, using sine wave for {recipe_db_id}.")
            audio_cmd_args = [ffmpeg_cmd, '-y', '-i', local_intermediate_merged_path, '-f', 'lavfi', '-i', "sine=frequency=1000", '-c:v', 'copy', '-c:a', 'aac', '-b:a', '128k', '-map', '0:v:0', '-map', '1:a:0', '-shortest', local_final_output_path]
        
        print(f"BACKGROUND TASK: VideoEditor: DEBUG - Starting audio addition. Command: {' '.join(audio_cmd_args)} for {recipe_db_id}")
        audio_process = subprocess.Popen(audio_cmd_args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, creationflags=creationflags)
        print(f"BACKGROUND TASK: VideoEditor: DEBUG - Waiting for audio addition FFmpeg process to complete (timeout 300s) for {recipe_db_id}...")
        audio_stdout, audio_stderr = audio_process.communicate(timeout=300)
        print(f"BACKGROUND TASK: VideoEditor: DEBUG - Audio addition FFmpeg process finished for {recipe_db_id}. RC: {audio_process.returncode}")
        if audio_process.returncode != 0:
            # If audio fails, we might still consider the silent merged video as partially successful.
            # For now, let's treat it as a full merge failure if audio can't be added.
            raise VideoEditingError(f"FFmpeg audio addition failed. RC: {audio_process.returncode}\nStderr: {audio_stderr[:500]}")
        print(f"BACKGROUND TASK: VideoEditor: Audio addition to local temp successful: {local_final_output_path}")

        # --- Upload final video to Google Drive ---
        print(f"BACKGROUND TASK: VideoEditor: Uploading {local_final_output_path} to GDrive folder {recipe_merged_video_gdrive_folder_id} as {gdrive_final_output_filename}")
        
        # Check if a file with the same name already exists in the target GDrive folder to update it
        existing_gdrive_file_id = gdrive.find_file_id_by_name(recipe_merged_video_gdrive_folder_id, gdrive_final_output_filename, service=task_specific_gdrive_service)
        
        final_merged_gdrive_file_id = gdrive.upload_file_to_drive(
            local_file_path=local_final_output_path,
            drive_folder_id=recipe_merged_video_gdrive_folder_id,
            drive_filename=gdrive_final_output_filename,
            mimetype='video/mp4',
            service=task_specific_gdrive_service,
            existing_file_id=existing_gdrive_file_id
        )
        print(f"BACKGROUND TASK: VideoEditor: DEBUG - GDrive upload attempt finished for {recipe_db_id}. GDrive File ID: {final_merged_gdrive_file_id}")
        if not final_merged_gdrive_file_id:
            raise VideoEditingError(f"Failed to upload merged video to Google Drive.")
        
        print(f"BACKGROUND TASK: VideoEditor: Successfully uploaded merged video to GDrive. File ID: {final_merged_gdrive_file_id}")
        current_db_status_on_exit = "MERGED"
        error_message_on_exit = None

    except VideoEditingError as ve:
        error_message_on_exit = str(ve)
        print(f"BACKGROUND TASK: VideoEditor: VideoEditingError: {error_message_on_exit}")
    except Exception as e:
        error_message_on_exit = f"Overall error in VideoEditor for {recipe_db_id}: {str(e)} - Type: {type(e).__name__}"
        import traceback
        print(f"BACKGROUND TASK: VideoEditor: TRACEBACK for {recipe_db_id} ---")
        traceback.print_exc()
        print(f"BACKGROUND TASK: VideoEditor: --- END TRACEBACK for {recipe_db_id}")
        print(f"BACKGROUND TASK: VideoEditor: Unexpected Exception: {error_message_on_exit}")
    
    finally:
        print(f"BACKGROUND TASK: VideoEditor: DEBUG - Entering finally block for {recipe_db_id}.")
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
                        print(f"BACKGROUND TASK: VideoEditor: DEBUG - Attempting to delete directory: {item_path} for {recipe_db_id}")
                        shutil.rmtree(item_path)
                    else:
                        print(f"BACKGROUND TASK: VideoEditor: DEBUG - Attempting to delete file: {item_path} for {recipe_db_id}")
                        os.remove(item_path)
                    print(f"BACKGROUND TASK: VideoEditor: Cleaned local temp: {item_path}")
            except Exception as e_clean:
                print(f"BACKGROUND TASK: VideoEditor: WARN Failed to clean local temp {item_path} for {recipe_db_id}: {e_clean}")
        
        print(f"BACKGROUND TASK: VideoEditor: DEBUG - Finished cleanup for {recipe_db_id}.")
        # The calling background task manager in routes/upload.py 
        # will use trigger_next_background_task if this step was successful.
        
        # If merge was successful, automatically trigger metadata generation
        if current_db_status_on_exit == "MERGED":
            print(f"BACKGROUND TASK: VideoEditor: Merge successful for {recipe_db_id}. Automatically triggering metadata generation.")
            # Ensure this call matches the signature of gemini.generate_youtube_metadata_from_video_info
            # It needs recipe_db_id, recipe_name_orig, and optionally custom_prompt_str (None for default)
            update_recipe_status(recipe_id=recipe_db_id, name=recipe_name_orig, status="GENERATING_METADATA") # Set status before adding task
            background_tasks.add_task(
                gemini.generate_youtube_metadata_from_video_info,
                recipe_db_id=recipe_db_id,
                recipe_name_orig=recipe_name_orig,
                custom_prompt_str=None # Use default prompt
            )
            print(f"BACKGROUND TASK: VideoEditor: Added metadata generation task for {recipe_db_id} to background.")

# __main__ block for testing would need significant rework to use GDrive for DB and outputs.
# For now, focusing on the main function logic.
