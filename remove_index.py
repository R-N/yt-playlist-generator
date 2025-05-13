import pandas as pd
import csv

file = "mp3_youtube_matches_0.csv"
EXPECTED_COLS = 14

def is_numeric(s):
    try:
        float(s)
        return True
    except ValueError:
        return False

# Load the file manually row by row to handle inconsistencies
cleaned_rows = []
with open(file, "r", encoding="utf-8") as f:
    reader = csv.reader(f)
    for row in reader:
        # Strip leading index-like values (digits) — keep rest
        i = 0
        while i < len(row) and (row[i].isdigit() or is_numeric(row[i])):
            i += 1
        cleaned = row[i:]
        # Ensure exactly EXPECTED_COLS columns (cut or pad with empty)
        cleaned = cleaned[:EXPECTED_COLS] + [""] * max(0, EXPECTED_COLS - len(cleaned))
        cleaned_rows.append(cleaned)

# Convert to DataFrame (optional, if you want to handle as DataFrame)
df_cleaned = pd.DataFrame(cleaned_rows)

# Save cleaned file
df_cleaned.to_csv(file, index=False, header=False)
