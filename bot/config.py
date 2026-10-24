"""Loads config.yaml plus the calibration and route files it points to."""
from pathlib import Path

import yaml

from bot import keys

SETTINGS_FILE = "settings.yaml"

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
    cfg["regions"] = (read_yaml(cfg["calibration_file"], {}) or {}).get("regions", {})
    cfg["route_data"] = read_yaml(cfg["route"], {"mode": "loop", "waypoints": []})
    for name in cfg["keys"].values():
        if name is not None:
            keys.validate(name)
    for buff in cfg.get("buffs") or []:
        keys.validate(buff["key"])
    return cfg
