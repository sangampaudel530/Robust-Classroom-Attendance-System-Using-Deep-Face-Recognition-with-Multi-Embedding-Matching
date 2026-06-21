import os
import glob

def cleanup_uploads(upload_dir="data/uploads"):
    """Deletes all files in the specified uploads directory."""
    if not os.path.exists(upload_dir):
        print(f"Directory '{upload_dir}' does not exist.")
        return
    
    # Get all files in the directory
    files = glob.glob(os.path.join(upload_dir, "*"))
    count = 0
    
    for f in files:
        if os.path.isfile(f):
            try:
                os.remove(f)
                count += 1
                # print(f"Deleted: {f}") # Uncomment to see each deleted file
            except Exception as e:
                print(f"Error deleting {f}: {e}")
                
    print(f"Cleanup complete! Successfully deleted {count} files from '{upload_dir}'.")

if __name__ == "__main__":
    cleanup_uploads()
