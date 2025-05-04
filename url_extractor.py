import pandas as pd
import re

dump_file_name = "dump.csv"
id_file_name = "ids.txt"
url_file_name = "urls.txt"
playlist_file_name = "playlists.txt"
username = "linearch"
limit = 50

# Function to extract YouTube video ID
def extract_youtube_id(url):
    if not isinstance(url, str):
        return None
    match = re.search(r'(?:v=|youtu\.be/)([a-zA-Z0-9_-]{11})', url)
    return match.group(1) if match else None

def main():
    df = pd.read_csv(dump_file_name)
    df = df[df['Author'] == username].copy()
    ids = pd.Series(df['Content'].apply(extract_youtube_id).dropna().unique())
    ids.to_csv(id_file_name, index=False, header=False)
    urls = 'https://www.youtube.com/watch?v=' + ids
    urls.to_csv(url_file_name, index=False, header=False)
    playlists = []
    ids = ids.tolist()
    for i in range(0, len(ids), limit):
        group = ids[i:i + 50]
        playlist = 'https://www.youtube.com/watch_videos?video_ids=' + ','.join(group)
        print(playlist)
        playlists.append(playlist)
    with open(playlist_file_name, 'w') as f:
        f.writelines([f"{line}\n" for line in playlists])

if __name__ == '__main__':
    main()
