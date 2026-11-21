"""
settings_manager.py - save / load the user's Unsay settings in settings.json.

Each profile (e.g. "cam0") is stored separately. Missing keys are filled from
DEFAULT_SETTINGS, so old settings files keep working after updates.
"""

import copy
import json
import os

SETTINGS_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "settings.json")

DEFAULT_SETTINGS = {
    "name": "",
    "audio": {"audio_in": None, "audio_out": None},
    "video": {"enabled": True, "camera": 0, "width": 1280, "height": 720, "fps": 30, "badge": True},
    "controls": {"delay": 2.0, "undo_key": "ctrl+shift+z"},
}


def _merge(defaults, saved):
    """Defaults overwritten by whatever was saved (recursively)."""
    out = copy.deepcopy(defaults)
    for k, v in (saved or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        elif k in out:
            out[k] = v
    return out


def _read_all():
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def get_settings(cam_name: str = "cam0") -> dict:
    return _merge(DEFAULT_SETTINGS, _read_all().get(cam_name))


def save_settings(settings: dict, cam_name: str = "cam0"):
    data = _read_all()
    data[cam_name] = settings
    with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def get_cam_number() -> int:
    return len(_read_all())
