import pandas as pd

file = "mp3_youtube_matches_2.csv"

# Load cleaned CSV
df = pd.read_csv(file)

df = df[df['yt_id'].notna() & (df['yt_id'] != '')]

# Drop duplicates based on filename, keeping the last occurrence
df = df.drop_duplicates(subset="filename", keep="last")

# Save cleaned CSV
df.to_csv(file, index=False)
