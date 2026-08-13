import os
import glob
from pathlib import Path

VIDEO_EXTENSIONS = {".mp4", ".avi", ".mov", ".mkv", ".webm", ".wmv", ".flv", ".m4v"}

def cleanup_uploads(upload_dir="data/uploads"):
    """Deletes all video files (not photos) in the specified uploads directory."""
    if not os.path.exists(upload_dir):
        print(f"Directory '{upload_dir}' does not exist.")
        return

    p = Path(upload_dir)
    count = 0
    for f in p.iterdir():
        if f.is_file() and f.suffix.lower() in VIDEO_EXTENSIONS:
            try:
                f.unlink()
                count += 1
            except OSError as e:
                print(f"Error deleting {f}: {e}")

    print(f"Cleanup complete! Successfully deleted {count} video(s) from '{upload_dir}'.")

if __name__ == "__main__":
    cleanup_uploads()
