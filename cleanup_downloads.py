import os
import re
import sys

DEFAULT_EXTENSION = [
    '.mp4', '.webm', '.m4a', 
    '.mp4.part', '.webm.part', '.m4a.part', 
    '.mp4.ytdl', '.webm.ytdl', '.m4a.ytdl',
]

ID_PATTERN = r'\[([a-zA-Z0-9_-]{11})\]'   

def extract_id(file, extension=None):
    if extension:
        pattern = re.compile(ID_PATTERN + re.escape(extension) + r'$')
    else:
        pattern = re.compile(ID_PATTERN + r'.')
    match = pattern.search(file)
    if match:
        return match.group(1)

def filter_ids(files, extension=None):
    pairs = [(extract_id(f, extension), f) for f in files]
    pairs = [(i, f) for i, f in pairs if i]
    if not pairs:
        return [], []
    ids, files = zip(*pairs)
    return list(ids), list(files)

def has_allowed_extension(filename, extensions):
    return any(filename.endswith(ext) for ext in ALLOWED_EXTENSIONS)

def get_files_by_ext(download_dir, extension):
    return [os.path.join(download_dir, f) for f in os.listdir(download_dir) if f.endswith(extension)]
    # return [os.path.join(download_dir, f) for f in os.listdir(download_dir) if has_allowed_extension(filename, extensions)]

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

def get_zero_byte_files(folder):
    return [
        os.path.join(folder, f)
        for f in os.listdir(folder)
        if os.path.isfile(os.path.join(folder, f)) and os.path.getsize(os.path.join(folder, f)) == 0
    ]

if __name__ == "__main__":
    extensions = sys.argv[1:] if len(sys.argv) > 1 else DEFAULT_EXTENSION
    extensions = [ext if ext.startswith('.') else '.' + ext for ext in extensions]

    download_folder = 'downloads'
    downloaded_ids_file = 'downloaded_ids.txt'

    all_ids = set()
    all_paths = []

    for ext in extensions:
        paths = get_files_by_ext(download_folder, ext)
        ids, paths = filter_ids(paths, ext)
        print(f"Found {len(ids)} files with extension {ext}.")
        all_ids.update(ids)
        all_paths.extend(paths)

    paths = get_zero_byte_files(download_folder)
    ids, paths = filter_ids(paths)
    print(f"Found {len(ids)} empty files.")
    all_ids.update(ids)
    all_paths.extend(paths)

    if all_ids:
        remove_ids_from_file(all_ids, downloaded_ids_file)
        delete_files(all_paths)
    else:
        print("No matching files found.")
