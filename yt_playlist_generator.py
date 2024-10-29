#!/usr/bin/env python3
# yt_playlist_generator.py - Creates a YouTube playlist from a list of URLs.

limit = 50
url_file_name = "urls.txt"
playlist_file_name = "playlists.txt"

def main():
    file = open(url_file_name)
    urls = [line.strip() for line in file.readlines()]
    file.close()
    ids = [url[-11:] for url in urls]
    print('Playlist created at:')
    playlists = []
    for i in range(len(ids)//limit):
        start = i * limit
        end = (i+1) * limit
        playlist = 'https://www.youtube.com/watch_videos?video_ids=' + ','.join(ids[start:end])
        print(playlist)
        playlists.append(playlist)
    with open(playlist_file_name, 'w') as f:
        f.writelines([f"{line}\n" for line in playlists])

if __name__ == '__main__':
    main()