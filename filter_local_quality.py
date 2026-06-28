"""
Mark / exclude tracks whose local MP3 is already good enough.

For every row in matches.csv, find the matching local file in MP3_FOLDERS,
read its real bitrate, and flag rows where local bitrate >= THRESHOLD_KBPS.
Those tracks don't need a (lower-quality) YouTube re-download.

Outputs:
  - matches.csv / matches.xlsx : original rows + two new columns
        local_bitrate  (kbps of the local file, blank if file not found)
        local_better   (True when local_bitrate >= THRESHOLD_KBPS)
  - ids2.txt : yt_id download list, with local_better rows removed
               (and, if ONLY_CHECKED, only your verified rows kept)

Note: bitrate is compared as a raw number. The YouTube candidates are opus
(~130 kbps) which is roughly mp3 ~256 in quality, so 192 is a deliberately
conservative "I already have a decent copy" cutoff, not an exact equivalence.
"""

import os
import mutagen
import pandas as pd

MATCHES_CSV = "matches.csv"
MATCHES_XLSX = "matches.xlsx"
IDS_OUTPUT = "ids2.txt"

MP3_FOLDERS = [
    "E:/Music/My Music",
    "E:/Music/My Music Out 2",
    "E:/Music/downloads",
]

THRESHOLD_KBPS = 192      # local mp3 at or above this is "good enough" -> skip
ONLY_CHECKED = False      # True = ids2.txt keeps only rows where check == True


def build_file_index(folders):
    """Map bare filename -> full path. First folder wins on duplicates."""
    index = {}
    for folder in folders:
        if not os.path.isdir(folder):
            print(f"Folder missing, skipping: {folder}")
            continue
        for name in os.listdir(folder):
            if name.lower().endswith(".mp3") and name not in index:
                index[name] = os.path.join(folder, name)
    return index


def local_bitrate_kbps(path):
    """Real bitrate of a local file in kbps, or None if unreadable."""
    try:
        audio = mutagen.File(path)
        bitrate = getattr(getattr(audio, "info", None), "bitrate", None)
        return round(bitrate / 1000) if bitrate else None
    except Exception as e:
        print(f"Could not read bitrate for {path}: {e}")
        return None


# --- Pure logic (no IO) so it can be unit-tested -------------------------

def bitrate_for_rows(filenames, index, read_bitrate=local_bitrate_kbps):
    """Map each filename to its local bitrate (None when no file). Returns
    (list_of_bitrates, missing_count). `read_bitrate` is injectable for tests."""
    bitrates, missing = [], 0
    for filename in filenames:
        path = index.get(filename)
        if not path:
            missing += 1
            bitrates.append(None)
        else:
            bitrates.append(read_bitrate(path))
    return bitrates, missing


def annotate_local_quality(df, bitrates, threshold=THRESHOLD_KBPS):
    """Add local_bitrate + local_better columns. local_better == have a local
    copy at or above the threshold (so the YouTube re-download is unwanted)."""
    df = df.copy()
    df["local_bitrate"] = bitrates
    df["local_better"] = df["local_bitrate"].notna() & (df["local_bitrate"] >= threshold)
    return df


def download_ids(df, only_checked=ONLY_CHECKED):
    """yt_id download list: drop local_better rows (already have it better),
    optionally keep only verified (check==True) rows."""
    keep = df[~df["local_better"]]
    if only_checked:
        keep = keep[keep["check"] == True]
    return pd.Series(keep["yt_id"].dropna().unique())


def main():
    df = pd.read_csv(MATCHES_CSV)
    index = build_file_index(MP3_FOLDERS)

    bitrates, missing = bitrate_for_rows(df["filename"], index)
    df = annotate_local_quality(df, bitrates, THRESHOLD_KBPS)

    df.to_csv(MATCHES_CSV, index=False)
    try:
        df.to_excel(MATCHES_XLSX, index=False)
    except Exception as e:
        print(f"Skipped writing {MATCHES_XLSX}: {e}")

    ids = download_ids(df, ONLY_CHECKED)
    ids.to_csv(IDS_OUTPUT, index=False, header=False)

    excluded = int(df["local_better"].sum())
    print(f"Rows:            {len(df)}")
    print(f"File not found:  {missing}")
    print(f"Local >= {THRESHOLD_KBPS}k:   {excluded}  (excluded)")
    print(f"ids2.txt ids:    {len(ids)}")


if __name__ == "__main__":
    main()
