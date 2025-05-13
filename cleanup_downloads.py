import os
import re
import sys

DEFAULT_EXTENSION = [
    '.mp4', '.webm', '.m4a', 
    '.mp4.part', '.webm.part', '.m4a.part', 
    '.mp4.ytdl', '.webm.ytdl', '.m4a.ytdl',
]

def get_ids_and_paths_by_ext(download_dir, extension):
    files = [f for f in os.listdir(download_dir) if f.endswith(extension)]
    ids = []
    paths_to_delete = []

    pattern = re.compile(r'\[([a-zA-Z0-9_-]{11})\]' + re.escape(extension) + r'$')
    for file in files:
        match = pattern.search(file)
        if match:
            ids.append(match.group(1))
            paths_to_delete.append(os.path.join(download_dir, file))
    return ids, paths_to_delete

def remove_ids_from_file(ids_to_remove, file_path):
    if not os.path.exists(file_path):
        print(f"{file_path} does not exist.")
        return

    with open(file_path, 'r') as f:
        existing_ids = set(line.strip() for line in f)

    updated_ids = existing_ids - set(ids_to_remove)

    with open(file_path, 'w') as f:
        for vid in sorted(updated_ids):
            f.write(vid + '\n')

    print(f"Removed {len(ids_to_remove)} IDs. Remaining: {len(updated_ids)}")

def delete_files(file_paths):
    deleted = 0
    for path in file_paths:
        try:
            os.remove(path)
            deleted += 1
        except Exception as e:
            print(f"Could not delete {path}: {e}")
    print(f"Deleted {deleted} files.")

if __name__ == "__main__":
    extensions = sys.argv[1:] if len(sys.argv) > 1 else DEFAULT_EXTENSION
    extensions = [ext if ext.startswith('.') else '.' + ext for ext in extensions]

    download_folder = 'downloads'
    downloaded_ids_file = 'downloaded_ids.txt'

    all_ids = set()
    all_paths = []

    for ext in extensions:
        ids, paths = get_ids_and_paths_by_ext(download_folder, ext)
        print(f"Found {len(ids)} files with extension {ext}.")
        all_ids.update(ids)
        all_paths.extend(paths)

    if all_ids:
        remove_ids_from_file(all_ids, downloaded_ids_file)
        delete_files(all_paths)
    else:
        print("No matching files found.")
