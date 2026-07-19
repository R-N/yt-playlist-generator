"""
Settings backed by a repo-root .env file (gitignored).

The Settings page in the UI writes secrets here (Discord bot token, AcoustID
API key). Values are applied to os.environ so the scripts the app launches as
subprocesses inherit them, and persisted to .env so they survive a restart.
Real environment variables win over .env (apply_to_environ uses setdefault),
so `set DISCORD_BOT_TOKEN=...` in the shell still overrides the file.
"""
import json
import os

from config import REPO_ROOT

ENV_PATH = os.path.join(REPO_ROOT, ".env")

# Keys the Settings page manages. Anything else in .env is preserved untouched.
MANAGED_KEYS = [
    "DISCORD_BOT_TOKEN", "DISCORD_CHANNEL_ID", "ACOUSTID_API_KEY",
    "MP3_FOLDERS_JSON", "DOWNLOAD_FOLDER",
    "YT_SEARCH_TOP_N", "TASK_DELAY_MIN", "TASK_DELAY_MAX",
    "YT_MIN_SCORE", "LOCAL_MIN_SCORE",
    "SEARCH_RESULT_LIMIT", "DELETE_TOKEN_TTL", "CLEANUP_EXTENSIONS",
]

_DEFAULT_CLEANUP_EXTS = (".mp4", ".webm", ".m4a", ".mp4.part", ".webm.part",
                         ".m4a.part", ".mp4.ytdl", ".webm.ytdl", ".m4a.ytdl")
# Which of those are secret -> never returned in full to the client.
SECRET_KEYS = {"DISCORD_BOT_TOKEN", "ACOUSTID_API_KEY"}
_APP_OWNED_ENV = {}


def parse_mp3_folders(value):
    """Validate and normalize configured local music folders."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError) as e:
            raise ValueError("MP3_FOLDERS_JSON must be a JSON list") from e
    if not isinstance(value, list):
        raise ValueError("MP3_FOLDERS_JSON must be a JSON list")
    out = []
    seen = set()
    for folder in value:
        if not isinstance(folder, str) or not folder.strip():
            raise ValueError("MP3 folder paths must be nonempty strings")
        if not os.path.isabs(folder):
            raise ValueError(f"MP3 folder must be absolute: {folder}")
        folder = os.path.normcase(os.path.realpath(os.path.abspath(folder)))
        if not os.path.isdir(folder):
            raise ValueError(f"MP3 folder does not exist or is not absolute: {folder}")
        if folder in seen:
            continue
        for existing in out:
            try:
                common = os.path.commonpath((existing, folder))
            except ValueError:
                continue
            if common in (existing, folder):
                raise ValueError(f"MP3 folders overlap: {existing} and {folder}")
        seen.add(folder)
        out.append(folder)
    return out


def parse_download_folder(value):
    """Validate the single download destination folder."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("DOWNLOAD_FOLDER must be a nonempty path")
    if not os.path.isabs(value):
        raise ValueError(f"DOWNLOAD_FOLDER must be absolute: {value}")
    folder = os.path.normpath(os.path.abspath(value))
    if not os.path.isdir(folder):
        raise ValueError(f"DOWNLOAD_FOLDER does not exist: {folder}")
    return folder


def search_top_n(default=3):
    """How many YouTube search hits to score when auto-finding a link (1..10)."""
    try:
        return max(1, min(int(get("YT_SEARCH_TOP_N", default)), 10))
    except (TypeError, ValueError):
        return default


def task_delay(default=(1.5, 4.0)):
    """(min, max) seconds between paced background-task fetches. max >= min >= 0."""
    def num(key, fallback):
        try:
            return max(0.0, float(get(key, fallback)))
        except (TypeError, ValueError):
            return fallback
    lo = num("TASK_DELAY_MIN", default[0])
    hi = num("TASK_DELAY_MAX", default[1])
    return (lo, max(lo, hi))


def yt_min_score(default=0):
    """Lowest review score an auto find-YouTube hit may have and still be applied.
    Lower = accept weaker matches (may be negative; the ranker penalizes)."""
    try:
        return int(float(get("YT_MIN_SCORE", default)))
    except (TypeError, ValueError):
        return default


def local_min_score(default=60):
    """Lowest filename partial-ratio (0..100) an auto find-local match may have."""
    try:
        return max(0, min(int(float(get("LOCAL_MIN_SCORE", default))), 100))
    except (TypeError, ValueError):
        return default


def search_result_limit(default=10):
    """How many ranked candidates a search picker returns (1..50)."""
    try:
        return max(1, min(int(float(get("SEARCH_RESULT_LIMIT", default))), 50))
    except (TypeError, ValueError):
        return default


def delete_token_ttl(default=60):
    """Seconds a delete-confirmation token stays valid before it expires (min 5)."""
    try:
        return max(5, int(float(get("DELETE_TOKEN_TTL", default))))
    except (TypeError, ValueError):
        return default


def cleanup_extensions(default=_DEFAULT_CLEANUP_EXTS):
    """Extensions the download-cleanup sweep treats as junk (besides zero-byte files).
    Stored as a comma/space list; normalized to a lowercase, dot-prefixed tuple."""
    raw = get("CLEANUP_EXTENSIONS")
    if not raw:
        return tuple(default)
    parts = [p.strip().lower() for p in raw.replace(",", " ").split()]
    exts = tuple("." + p.lstrip(".") for p in parts if p.strip("."))
    return exts or tuple(default)


def configured_download_folder(default):
    raw = _env_or_file("DOWNLOAD_FOLDER")
    if not raw:
        return default
    return parse_download_folder(raw)


def configured_mp3_folders(fallback=None):
    """Resolve process-env/.env value, using fallback only when key is absent."""
    raw = _env_or_file("MP3_FOLDERS_JSON")
    if raw is None:
        return parse_mp3_folders(list(fallback or []))
    return parse_mp3_folders(raw)


def external_mp3_folders_override():
    value = os.environ.get("MP3_FOLDERS_JSON")
    return value is not None and _APP_OWNED_ENV.get("MP3_FOLDERS_JSON") != value


def _parse(text):
    env = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        env[key.strip()] = val.strip().strip('"').strip("'")
    return env


def load_env():
    if not os.path.exists(ENV_PATH):
        return {}
    with open(ENV_PATH, encoding="utf-8") as f:
        return _parse(f.read())


def _env_or_file(key):
    """Resolve a key from the live process env, falling back to the .env file.
    None means it is set nowhere (distinct from an empty value)."""
    raw = os.environ.get(key)
    if raw is None:
        raw = load_env().get(key)
    return raw


def apply_to_environ():
    """Load .env into the process env without clobbering real env vars."""
    for key, val in load_env().items():
        if key not in os.environ:
            os.environ[key] = val
            _APP_OWNED_ENV[key] = val


def get(key, default=None):
    """Live env first (covers shell-exported + just-saved), then .env on disk."""
    val = os.environ.get(key)
    if val:
        return val
    return load_env().get(key, default)


def save(values):
    """Merge values into .env (atomic write) and os.environ. Empty/None clears."""
    env = load_env()
    updates = {}
    for key, val in values.items():
        if key == "MP3_FOLDERS_JSON" and external_mp3_folders_override():
            raise ValueError("MP3_FOLDERS_JSON is externally overridden")
        if val is None or val == "":
            env.pop(key, None)
            updates[key] = None
        else:
            if key == "MP3_FOLDERS_JSON":
                val = parse_mp3_folders(val)
                val = json.dumps(val, separators=(",", ":"))
            elif key == "DOWNLOAD_FOLDER":
                val = parse_download_folder(val)
            env[key] = str(val)
            updates[key] = str(val)
    tmp = ENV_PATH + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write("\n".join(f"{k}={v}" for k, v in env.items()) + "\n")
        os.replace(tmp, ENV_PATH)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    for key, val in updates.items():
        if val is None:
            os.environ.pop(key, None)
            _APP_OWNED_ENV.pop(key, None)
        else:
            os.environ[key] = val
            _APP_OWNED_ENV[key] = val
    return public_view()


def public_view():
    """Safe-to-send state: secrets masked to a short suffix, not the raw value."""
    out = {}
    for key in MANAGED_KEYS:
        val = get(key)
        if not val:
            out[key] = {"set": False, "preview": ""}
        elif key in SECRET_KEYS:
            out[key] = {"set": True, "preview": "••••" + val[-4:]}
        else:
            out[key] = {"set": True, "preview": val}
    return out
