"""Reads the minimap, HP/MP bars and popup templates from a captured frame."""
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np


@dataclass
class State:
    pos: tuple | None = None                     # player (x, y) in minimap pixels
    others: list = field(default_factory=list)   # other players' (x, y, area)
    hp: float | None = None                      # 0..1
    mp: float | None = None


def crop(frame, region):
    if not region:
        return None
    x, y, w, h = region
    return frame[y:y + h, x:x + w]


def color_mask(img, ranges):
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    mask = np.zeros(img.shape[:2], np.uint8)
    for lo, hi in ranges:
        mask |= cv2.inRange(hsv, np.array(lo, np.uint8), np.array(hi, np.uint8))
    return mask


def find_blobs(img, ranges, min_area, max_area, max_size=None):
    """Centroids (x, y, area) of colour blobs within the size limits, largest first.

    With max_size, only roughly round blobs no wider/taller than that count (minimap dots, not coins or text).
    """
    n, _, stats, cents = cv2.connectedComponentsWithStats(color_mask(img, ranges), connectivity=8)
    blobs = []
    for i in range(1, n):
        _, _, w, h, area = (int(v) for v in stats[i])
        if not min_area <= area <= max_area:
            continue
        if max_size and not (w <= max_size and h <= max_size and abs(w - h) <= 2):
            continue
        blobs.append((float(cents[i][0]), float(cents[i][1]), area))
    return sorted(blobs, key=lambda b: -b[2])


def bar_fill(img, sat_min, val_min, column_ratio):
    """Share of the bar's columns that are coloured (filled) rather than grey/dark (empty)."""
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    coloured = (hsv[:, :, 1] >= sat_min) & (hsv[:, :, 2] >= val_min)
    return float((coloured.mean(axis=0) >= column_ratio).mean())


HUD_REGIONS = ("hp_bar", "mp_bar")  # parts of the bottom-centre HUD


class Vision:
    def __init__(self, cfg):
        self.v = cfg["vision"]
        self.regions = cfg["regions"]
        self.calibrated_width = cfg.get("calibrated_width", 1024)
        self.last_pos = None

    def regions_for(self, frame):
        """The calibrated regions placed on this frame: the HUD stays centred, so on a wider or narrower
        screen than the one it was calibrated on, the HP/MP bars move sideways by half the difference."""
        dx = round((frame.shape[1] - self.calibrated_width) / 2)
        return {name: [r[0] + dx, *r[1:]] if r and name in HUD_REGIONS else r for name, r in self.regions.items()}

    def read(self, frame):
        v, state = self.v, State()
        regions = self.regions_for(frame)
        minimap = crop(frame, regions.get("minimap"))
        if minimap is not None and minimap.size:
            size = v.get("dot_max_size")
            players = find_blobs(minimap, v["player_hsv"], v["dot_min_area"], v["dot_max_area"], size)
            if players:
                state.pos = self._pick_player(players)
            state.others = find_blobs(minimap, v["other_player_hsv"], v["dot_min_area"], v["dot_max_area"], size)
        self.last_pos = state.pos
        for name in ("hp", "mp"):
            bar = crop(frame, regions.get(f"{name}_bar"))
            if bar is not None and bar.size:
                setattr(state, name, bar_fill(bar, v["bar_sat_min"], v["bar_val_min"], v["bar_column_ratio"]))
        return state

    def _pick_player(self, blobs):
        """The blob nearest to last frame's position (the dot can't jump far between frames), else the largest."""
        if self.last_pos:
            near = min(blobs, key=lambda b: (b[0] - self.last_pos[0]) ** 2 + (b[1] - self.last_pos[1]) ** 2)
            if abs(near[0] - self.last_pos[0]) + abs(near[1] - self.last_pos[1]) <= 15:
                return near[:2]
        return blobs[0][:2]


class TemplateWatcher:
    """Matches saved popup crops (templates/*.png) against the whole frame."""

    def __init__(self, folder, threshold):
        self.threshold = threshold
        self.templates = []
        for path in sorted(Path(folder).glob("*.png")):
            img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
            if img is not None:
                self.templates.append((path.stem, img))

    def match(self, frame):
        """Returns (name, score) of the first template found, or None."""
        if not self.templates:
            return None
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        for name, tpl in self.templates:
            if tpl.shape[0] > gray.shape[0] or tpl.shape[1] > gray.shape[1]:
                continue
            _, score, _, _ = cv2.minMaxLoc(cv2.matchTemplate(gray, tpl, cv2.TM_CCOEFF_NORMED))
            if score >= self.threshold:
                return name, score
        return None


def draw_overlay(frame, regions, state):
    """Copy of the frame with regions, detected dots and bar readings drawn on it."""
    out = frame.copy()
    colours = {"minimap": (0, 255, 0), "hp_bar": (0, 0, 255), "mp_bar": (255, 0, 0)}
    for name, region in regions.items():
        if region:
            x, y, w, h = region
            cv2.rectangle(out, (x, y), (x + w, y + h), colours.get(name, (255, 255, 255)), 1)
            label = name
            if name in ("hp_bar", "mp_bar"):
                value = state.hp if name == "hp_bar" else state.mp
                label = f"{name} {value:.0%}" if value is not None else name
            cv2.putText(out, label, (x, max(12, y - 4)), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1)
    mm = regions.get("minimap")
    if mm:
        if state.pos:
            cv2.circle(out, (int(mm[0] + state.pos[0]), int(mm[1] + state.pos[1])), 5, (0, 255, 255), 1)
        for ox, oy, _ in state.others:
            cv2.circle(out, (int(mm[0] + ox), int(mm[1] + oy)), 5, (0, 0, 255), 1)
    return out
