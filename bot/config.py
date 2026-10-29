"""Loads config.yaml plus the calibration and route files it points to."""
from pathlib import Path

import yaml

from bot import keys

SETTINGS_FILE = "settings.yaml"
MODES = ("route", "stand", "hunt")

def read_yaml(path, default=None):
    path = Path(path)
    if not path.exists():
        return default
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or default


def save_yaml(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=False)


def deep_merge(base, override):
    """Merges override into base in place; nested dicts merge, everything else (lists too) replaces."""
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            deep_merge(base[key], value)
        else:
            base[key] = value
    return base


def load(path="config.yaml", settings_path=SETTINGS_FILE):
    """config.yaml holds the documented defaults; settings.yaml (saved by the control panel) overrides them."""
    cfg = read_yaml(path)
    if cfg is None:
        raise FileNotFoundError(path)
    deep_merge(cfg, read_yaml(settings_path, {}) or {})
    cal = read_yaml(cfg["calibration_file"], {}) or {}
    cfg["regions"] = cal.get("regions", {})
    cfg["calibrated_width"] = cal.get("frame_width", 1024)  # older calibrations were all made in a 1024x768 window
    cfg["route_data"] = read_yaml(cfg["route"], {"mode": "loop", "waypoints": []})
    for name in cfg["keys"].values():
        if name is not None:
            keys.validate(name)
    for buff in cfg.get("buffs") or []:
        keys.validate(buff["key"])
    if cfg.get("mode", "route") not in MODES:
        raise ValueError(f"mode must be one of {', '.join(MODES)}, not {cfg.get('mode')!r}")
    if (cfg.get("hunt") or {}).get("attack_key"):
        keys.validate(cfg["hunt"]["attack_key"])
    for i, wp in enumerate(cfg["route_data"].get("waypoints") or [], 1):
        every = wp.get("every", 1)
        if isinstance(every, bool) or not isinstance(every, int) or every < 1:
            raise ValueError(f"{cfg['route']}, waypoint {i}: `every` must be a whole number of laps (1 or more), "
                             f"not {every!r}")
    return cfg
