import os
import subprocess
import sys
import glob # For finding video files
import re # For natural sort key
import random # For selecting music
import tempfile # For temporary pre-processed clips

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from config import MERGED_DIR
from utils import update_recipe_status

class VideoEditingError(Exception):
    """Custom exception for video editing errors."""
    pass

def get_ffmpeg_tool_path(tool_name: str = "ffmpeg") -> str:
    """Checks for ffmpeg/ffprobe in PATH and returns the command name."""
    try:
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        subprocess.run([tool_name, "-version"], capture_output=True, check=True, text=True, creationflags=creationflags)
        print(f"{tool_name.capitalize()} found in PATH: '{tool_name}'")
        return tool_name
    except FileNotFoundError:
        msg = f"'{tool_name}' command not found. Please ensure FFmpeg (includes {tool_name}) is installed and in PATH."
        raise VideoEditingError(msg)
    except subprocess.CalledProcessError as e:
        msg = f"{tool_name.capitalize()} version check failed. Output: {e.stderr}"
        raise VideoEditingError(msg)

def get_video_duration(video_path: str, ffprobe_cmd: str) -> float:
    """Returns video duration in seconds. Returns 0.0 on error or if no duration."""
    command = [ffprobe_cmd, "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", video_path]
    try:
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        result = subprocess.run(command, capture_output=True, text=True, check=True, creationflags=creationflags)
        duration_str = result.stdout.strip()
        return float(duration_str) if duration_str and duration_str != "N/A" else 0.0
    except Exception as e:
        print(f"Warning: Could not get duration for {os.path.basename(video_path)} ({e}). Assuming 0.0s.")
        return 0.0

def natural_sort_key(s):
    return [int(text) if text.isdigit() else text.lower() for text in re.split(r'([0-9]+)', os.path.basename(s))]

MIN_CLIP_DURATION_SECONDS = 0.2  # Allow very short clips to be considered
PREPROCESS_IF_SHORTER_THAN_SECONDS = 1.5 # Pre-process clips shorter than this (but >= MIN_CLIP_DURATION_SECONDS)
DEFAULT_PREPROCESS_FPS = "30" # Target FPS for pre-processed short clips
DEFAULT_PREPROCESS_RESOLUTION = "1280x720" # Target resolution for pre-processed short clips (adjust as needed)

def merge_videos_and_replace_audio(raw_clips_path: str, recipe_db_id: str, recipe_name_orig: str) -> str | None:
    print(f"Starting FFmpeg processing for recipe ID: {recipe_db_id} ({recipe_name_orig}) from: {raw_clips_path}")
    ffmpeg_cmd, ffprobe_cmd = "", ""
    temp_preprocess_dir = ""
    files_to_delete_after_processing = []

    try:
        ffmpeg_cmd = get_ffmpeg_tool_path("ffmpeg")
        ffprobe_cmd = get_ffmpeg_tool_path("ffprobe")
    except VideoEditingError as e:
        update_recipe_status(recipe_id=recipe_db_id, name=recipe_name_orig, status="merge_failed", error_message=f"FFmpeg/ffprobe error: {e}")
        return None

    if not os.path.isdir(raw_clips_path):
        update_recipe_status(recipe_id=recipe_db_id, name=recipe_name_orig, status="merge_failed", error_message=f"Raw clips dir not found: {raw_clips_path}")
        return None

    video_extensions = ('*.mp4', '*.MP4', '*.mov', '*.MOV', '*.avi', '*.AVI', '*.mkv', '*.MKV')
    unique_clip_paths = {os.path.normpath(p) for ext in video_extensions for p in glob.glob(os.path.join(raw_clips_path, ext))}

    if not unique_clip_paths:
        update_recipe_status(recipe_id=recipe_db_id, name=recipe_name_orig, status="merge_failed", error_message=f"No video files found in {raw_clips_path}")
        return None

    print(f"Found {len(unique_clip_paths)} unique video files. Checking durations and pre-processing short clips...")
    temp_preprocess_dir = tempfile.mkdtemp(prefix="barged_preprocess_", dir=raw_clips_path) # Create temp dir inside raw_clips for easier cleanup if needed
    files_to_delete_after_processing.append(temp_preprocess_dir) # Mark dir for cleanup
    
    clips_for_concat_list = []
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0

    for clip_path in sorted(list(unique_clip_paths), key=natural_sort_key): # Sort before processing for consistent naming if needed
        duration = get_video_duration(clip_path, ffprobe_cmd)
        base_name = os.path.basename(clip_path)

        if duration < MIN_CLIP_DURATION_SECONDS:
            print(f"Excluding clip (too short): {base_name} (Duration: {duration:.2f}s)")
            continue

        if duration < PREPROCESS_IF_SHORTER_THAN_SECONDS:
            print(f"Pre-processing short clip: {base_name} (Duration: {duration:.2f}s)")
            preprocessed_clip_name = f"preprocessed_{base_name}"
            preprocessed_clip_path = os.path.join(temp_preprocess_dir, preprocessed_clip_name)
            preprocess_cmd_args = [
                ffmpeg_cmd, '-y', '-i', clip_path,
                '-c:v', 'libx264', '-preset', 'medium', '-crf', '22', '-pix_fmt', 'yuv420p',
                '-r', DEFAULT_PREPROCESS_FPS, '-s', DEFAULT_PREPROCESS_RESOLUTION, '-an', # Standardize and make silent
                preprocessed_clip_path
            ]
            try:
                subprocess.run(preprocess_cmd_args, check=True, capture_output=True, text=True, timeout=60, creationflags=creationflags) # 1 min timeout for short clip preprocess
                clips_for_concat_list.append(preprocessed_clip_path)
                print(f"Successfully pre-processed: {base_name} -> {preprocessed_clip_name}")
            except subprocess.CalledProcessError as e_pre:
                print(f"ERROR pre-processing {base_name}: {e_pre.stderr}. Excluding this clip.")
            except subprocess.TimeoutExpired:
                print(f"ERROR: Timeout pre-processing {base_name}. Excluding this clip.")
        else:
            clips_for_concat_list.append(clip_path) # Add original path if long enough
    
    if not clips_for_concat_list:
        msg = f"No clips remaining after duration filtering/pre-processing. Min duration: {MIN_CLIP_DURATION_SECONDS}s."
        update_recipe_status(recipe_id=recipe_db_id, name=recipe_name_orig, status="merge_failed", error_message=msg)
        return None

    print(f"Final list of {len(clips_for_concat_list)} clips for concatenation: {clips_for_concat_list}")
    if not os.path.exists(MERGED_DIR): os.makedirs(MERGED_DIR)

    safe_recipe_name = "".join(c if c.isalnum() else "_" for c in recipe_name_orig)
    intermediate_merged_filename = f"{safe_recipe_name}_merged_silent.mp4"
    intermediate_merged_path = os.path.join(MERGED_DIR, intermediate_merged_filename)
    final_output_filename = f"{safe_recipe_name}_final.mp4"
    final_output_path = os.path.join(MERGED_DIR, final_output_filename)
    list_file_path = os.path.join(temp_preprocess_dir, "ffmpeg_main_filelist.txt") # Put list file in temp dir
    files_to_delete_after_processing.append(list_file_path)

    processed_video_path_for_db = None

    try:
        with open(list_file_path, 'w') as lf:
            for clip_path in clips_for_concat_list:
                lf.write(f"file '{clip_path.replace(os.sep, '/')}'\n")
        print(f"Created main FFmpeg file list: {list_file_path}")

        ffmpeg_merge_cmd_args = [
            ffmpeg_cmd, '-y', '-f', 'concat', '-safe', '0', '-i', list_file_path,
            '-c:v', 'libx264', '-preset', 'medium', '-crf', '23', '-pix_fmt', 'yuv420p', '-an',
            intermediate_merged_path
        ]
        print(f"Executing main FFmpeg merge (re-encoding, silent): {' '.join(ffmpeg_merge_cmd_args)}")
        process = subprocess.Popen(ffmpeg_merge_cmd_args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, creationflags=creationflags)
        stdout, stderr = process.communicate(timeout=600)

        if process.returncode != 0:
            msg = f"Main FFmpeg merge (silent) failed. RC: {process.returncode}\nStderr: {stderr}"
            update_recipe_status(recipe_id=recipe_db_id, name=recipe_name_orig, status="merge_failed", error_message=stderr[:1000]); return None
        
        print(f"Main FFmpeg silent merge successful: {intermediate_merged_path}")
        processed_video_path_for_db = intermediate_merged_path
        current_db_status = "merged"

        static_audio_dir = os.path.join(os.path.dirname(__file__), '..', 'static', 'audio')
        available_music_files = [os.path.join(static_audio_dir, f) for f in os.listdir(static_audio_dir) if f.lower().endswith('.mp3')] if os.path.exists(static_audio_dir) else []
        selected_music_path = random.choice(available_music_files) if available_music_files else None

        if selected_music_path:
            print(f"Selected background music: {selected_music_path}. Adding audio.")
            ffmpeg_audio_cmd_args = [
                ffmpeg_cmd, '-y',
                '-i', intermediate_merged_path,  # Input 0: silent video
                '-i', selected_music_path,     # Input 1: background music MP3
                '-c:v', 'copy',
                '-c:a', 'aac', '-b:a', '192k', # Keep original bitrate for real music
                '-map', '0:v:0',
                '-map', '1:a:0',
                '-shortest', final_output_path
            ]
        else:
            print("No external .mp3 files found. Generating placeholder sine wave audio.")
            generated_audio_filter = "sine=frequency=1000" # Sine wave at 1kHz
            ffmpeg_audio_cmd_args = [
                ffmpeg_cmd, '-y',
                '-i', intermediate_merged_path,    # Input 0: silent video
                '-f', 'lavfi',                     # Specify format for next input
                '-i', generated_audio_filter,      # Input 1: generated sine wave
                '-c:v', 'copy',
                '-c:a', 'aac', '-b:a', '128k',  # Lower bitrate for generated tone is fine
                '-map', '0:v:0',
                '-map', '1:a:0',
                '-shortest', final_output_path
            ]

        print(f"Executing FFmpeg audio command: {' '.join(ffmpeg_audio_cmd_args)}")
        try:
            audio_process = subprocess.Popen(ffmpeg_audio_cmd_args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, creationflags=creationflags)
            audio_stdout, audio_stderr = audio_process.communicate(timeout=300)
            if audio_process.returncode == 0:
                print(f"FFmpeg audio addition successful. Final video: {final_output_path}")
                processed_video_path_for_db = final_output_path
                try: os.remove(intermediate_merged_path); print(f"Cleaned intermediate: {intermediate_merged_path}")
                except Exception as e_clean: print(f"Warning: Failed to clean intermediate {intermediate_merged_path}: {e_clean}")
            else:
                msg = f"FFmpeg audio addition failed. RC: {audio_process.returncode}\nStderr: {audio_stderr}"
                current_db_status = "merged_audio_failed"
                update_recipe_status(recipe_id=recipe_db_id, name=recipe_name_orig, status=current_db_status, merged_video_path=intermediate_merged_path, error_message=f"Audio add failed: {audio_stderr[:500]}"); return intermediate_merged_path
        except Exception as e_audio_add:
            msg = f"Error during audio addition: {e_audio_add}"
            current_db_status = "merged_audio_failed"
            update_recipe_status(recipe_id=recipe_db_id, name=recipe_name_orig, status=current_db_status, merged_video_path=intermediate_merged_path, error_message=msg); return intermediate_merged_path

        update_recipe_status(recipe_id=recipe_db_id, name=recipe_name_orig, status=current_db_status, merged_video_path=processed_video_path_for_db)
        return processed_video_path_for_db

    except Exception as e: # Catch-all for other errors like Popen issues, file list creation etc.
        msg = f"Overall error in FFmpeg processing: {e}"
        update_recipe_status(recipe_id=recipe_db_id, name=recipe_name_orig, status="merge_failed", error_message=msg); return None
    finally:
        for item_to_delete in files_to_delete_after_processing:
            if os.path.exists(item_to_delete):
                try:
                    if os.path.isdir(item_to_delete):
                        import shutil; shutil.rmtree(item_to_delete)
                        print(f"Cleaned up temp directory: {item_to_delete}")
                    else:
                        os.remove(item_to_delete)
                        print(f"Cleaned up temp file: {item_to_delete}")
                except Exception as e_final_clean:
                    print(f"Warning: Failed to clean up {item_to_delete}: {e_final_clean}")

if __name__ == '__main__':
    print("Automated Test for Video Editor (FFmpeg) Module...")
    # Ensure MIN_CLIP_DURATION_SECONDS and PREPROCESS_IF_SHORTER_THAN_SECONDS are set for testing
    # original_min_duration = MIN_CLIP_DURATION_SECONDS
    # MIN_CLIP_DURATION_SECONDS = 0.1 # Allow very short clips
    # PREPROCESS_IF_SHORTER_THAN_SECONDS = 2.0 # Preprocess clips under 2s
    # print(f"TESTING WITH: MIN_CLIP_DURATION={MIN_CLIP_DURATION_SECONDS}s, PREPROCESS_BELOW={PREPROCESS_IF_SHORTER_THAN_SECONDS}s")

    test_recipe_name = "Fish Pulusu"
    test_recipe_id_for_db = "1w2EV-KiQzX72y8LClmZ1hxxMVQnxMIgO"
    base_raw_dir = os.path.join(os.path.dirname(__file__), '..', 'videos', 'raw')
    test_raw_clips_dir = os.path.join(base_raw_dir, "Fish_Pulusu_gdriveTest") 
    print(f"Using raw clips directory: {test_raw_clips_dir}")

    # Check for dummy audio (no need to create one if real ones exist)
    static_audio_dir_test = os.path.join(os.path.dirname(__file__), '..', 'static', 'audio')
    if not os.path.exists(static_audio_dir_test): os.makedirs(static_audio_dir_test)
    real_mp3s = [f for f in os.listdir(static_audio_dir_test) if f.lower().endswith('.mp3')]
    if not real_mp3s:
        print("No real .mp3s found in static/audio. Placeholder sine wave will be used by the function if this dir is empty.")

    if not os.path.exists(test_raw_clips_dir) or not os.listdir(test_raw_clips_dir):
        print(f"ERROR: Test raw clips directory '{test_raw_clips_dir}' is missing or empty.")
    else:
        print(f"Attempting to merge videos from: {test_raw_clips_dir} for recipe: {test_recipe_name} (ID: {test_recipe_id_for_db})\n")
        try:
            merged_file = merge_videos_and_replace_audio(test_raw_clips_dir, test_recipe_id_for_db, test_recipe_name)
            if merged_file:
                print(f"\nSUCCESS: Video processing test completed. Final video at: {merged_file}")
                from utils import get_recipe_status
                status_entry = get_recipe_status(test_recipe_id_for_db)
                print(f"DB status for '{test_recipe_name}': {status_entry}")
            else:
                print("\nFAILURE: Video processing test failed. Check logs.")
        except VideoEditingError as e:
            print(f"VIDEO EDITING ERROR: {e}")
        except Exception as e:
            print(f"UNEXPECTED TEST ERROR: {e}")
    # MIN_CLIP_DURATION_SECONDS = original_min_duration # Reset if changed
    print("\nVideo Editor Module automated test finished.")
