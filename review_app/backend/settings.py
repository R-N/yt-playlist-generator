"""
Settings backed by a repo-root .env file (gitignored).

The Settings page in the UI writes secrets here (Discord bot token, AcoustID
API key). Values are applied to os.environ so the scripts the app launches as
subprocesses inherit them, and persisted to .env so they survive a restart.
Real environment variables win over .env (apply_to_environ uses setdefault),
so `set DISCORD_BOT_TOKEN=...` in the shell still overrides the file.
"""
import os

from config import REPO_ROOT

ENV_PATH = os.path.join(REPO_ROOT, ".env")

# Keys the Settings page manages. Anything else in .env is preserved untouched.
MANAGED_KEYS = ["DISCORD_BOT_TOKEN", "DISCORD_CHANNEL_ID", "ACOUSTID_API_KEY"]
# Which of those are secret -> never returned in full to the client.
SECRET_KEYS = {"DISCORD_BOT_TOKEN", "ACOUSTID_API_KEY"}


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


def apply_to_environ():
    """Load .env into the process env without clobbering real env vars."""
    for key, val in load_env().items():
        os.environ.setdefault(key, val)


def get(key, default=None):
    """Live env first (covers shell-exported + just-saved), then .env on disk."""
    val = os.environ.get(key)
    if val:
        return val
    return load_env().get(key, default)


def save(values):
    """Merge values into .env (atomic write) and os.environ. Empty/None clears."""
    env = load_env()
    for key, val in values.items():
        if val is None or val == "":
            env.pop(key, None)
            os.environ.pop(key, None)
        else:
            env[key] = str(val)
            os.environ[key] = str(val)
    tmp = ENV_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(f"{k}={v}" for k, v in env.items()) + "\n")
    os.replace(tmp, ENV_PATH)
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
