"""Resolve local music folders without changing standalone script defaults."""

import json
import os


def resolve_mp3_folders(defaults, env_name="MP3_FOLDERS_JSON"):
    raw = os.environ.get(env_name)
    if raw is None:
        return list(defaults)
    try:
        folders = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{env_name} must be a JSON list of absolute existing directories") from exc
    if not isinstance(folders, list) or any(not isinstance(folder, str) or not folder.strip() for folder in folders):
        raise ValueError(f"{env_name} must be a JSON list of absolute existing directories")

    roots = []
    for folder in folders:
        if not os.path.isabs(folder):
            raise ValueError(f"{env_name} contains relative path: {folder!r}")
        path = os.path.normcase(os.path.realpath(folder))
        if not os.path.isdir(path):
            raise ValueError(f"{env_name} path is not an existing directory: {folder!r}")
        if path not in roots:
            roots.append(path)

    for i, root in enumerate(roots):
        for other in roots[i + 1:]:
            try:
                common = os.path.commonpath((root, other))
            except ValueError:
                continue
            if common == root or common == other:
                raise ValueError(f"{env_name} contains overlapping directories: {root!r}, {other!r}")
    return roots
