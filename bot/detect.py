"""Finds the game's UI on a captured frame: the minimap, the HP/MP bars and the quickslot keys.

Tuned on the MapleStory Classic World HUD, on frames scaled to 768 px tall (see capture.UI_HEIGHT).
The HUD sits at the bottom centre, so wider screens only move it sideways. Everything returns None
when not found.
"""
from pathlib import Path

import cv2
import numpy as np

ASSETS = Path(__file__).resolve().parent.parent / "assets"
_templates = {}

# Quickslot grid: top-left corner of each slot, relative to the bottom centre of the frame.
QUICKSLOT_SIZE = (28, 28)
QUICKSLOTS = {
    "shift": (258, -138), "insert": (294, -138), "home": (328, -138), "pageup": (364, -138),
    "ctrl": (258, -104), "delete": (294, -104), "end": (328, -104), "pagedown": (364, -104),
}
RED = [((0, 150, 100), (8, 255, 255)), ((170, 150, 100), (180, 255, 255))]
BLUE = [((95, 150, 100), (125, 255, 255))]


def template(name):
    if name not in _templates:
        img = cv2.imread(str(ASSETS / f"{name}.png"), cv2.IMREAD_GRAYSCALE)
        if img is None:
            raise FileNotFoundError(ASSETS / f"{name}.png")
        _templates[name] = img
    return _templates[name]


def find_template(gray, name, threshold=0.8):
    """(x, y, score) of the best match, or None."""
    tpl = template(name)
    if tpl.shape[0] > gray.shape[0] or tpl.shape[1] > gray.shape[1]:
        return None
    _, score, _, (x, y) = cv2.minMaxLoc(cv2.matchTemplate(gray, tpl, cv2.TM_CCOEFF_NORMED))
    return (x, y, score) if score >= threshold else None


def mask(img, ranges):
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    out = np.zeros(img.shape[:2], np.uint8)
    for lo, hi in ranges:
        out |= cv2.inRange(hsv, np.array(lo, np.uint8), np.array(hi, np.uint8))
    return out


def find_minimap(frame):
    """[x, y, w, h] of the minimap's map area (inside its white frame), or None if the minimap isn't open."""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    hit = find_template(gray, "minimap_title")
    if not hit:
        return None
    tx, ty, _ = hit
    title_h = template("minimap_title").shape[0]
    # The map area is ringed by a white border that starts a few pixels below the title bar
    # (white chat bubbles and shop signs further down don't count).
    x0, y0 = max(0, tx - 20), ty + title_h
    window = frame[y0:y0 + 400, x0:x0 + 600]
    white = cv2.inRange(window, (235, 235, 235), (255, 255, 255))
    n, _, stats, _ = cv2.connectedComponentsWithStats(white, connectivity=8)
    best = None
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if (w >= 40 and h >= 20 and y <= 20 and abs((x0 + x) - tx) <= 20
                and (best is None or area > best[4])):
            best = (x, y, w, h, area)
    if best is None:
        return None
    x, y, w, h, _ = best
    return [int(x0 + x), int(y0 + y), int(w), int(h)]


def find_bars(frame):
    """(hp_bar, mp_bar) as [x, y, w, h] each (None if not found). Bars must be at least partly filled."""
    h_img = frame.shape[0]
    top = max(0, h_img - 60)
    strip = frame[top:]
    found = {}
    for name, ranges in (("hp", RED), ("mp", BLUE)):
        n, _, stats, _ = cv2.connectedComponentsWithStats(mask(strip, ranges), connectivity=8)
        best = None
        for i in range(1, n):
            x, y, w, h, area = stats[i]
            if w >= 40 and 8 <= h <= 20 and w >= 4 * h and (best is None or w > best[2]):
                best = (x, y, w, h)
        if best:
            x, y, w, h = best
            found[name] = [int(x), int(top + y), int(w), int(h)]
    hp, mp = found.get("hp"), found.get("mp")
    # The two bars are the same width, so a full one fixes the width of a part-empty one.
    if hp and mp:
        width = max(hp[2], mp[2])
        hp[2] = mp[2] = width
    return hp, mp


def quickslot_keys(frame):
    """Keys found on the quickslot bar: {'attack': 'ctrl', 'hp_potion': 'delete', 'mp_potion': 'pagedown'} (any subset)."""
    h_img, w_img = frame.shape[:2]
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    sw, sh = QUICKSLOT_SIZE
    red, blue, attack = {}, {}, {}
    for key, (dx, dy) in QUICKSLOTS.items():
        x, y = w_img // 2 + dx, h_img + dy
        if x < 0 or y < 0:
            return {}
        slot = frame[y:y + sh, x:x + sw]
        red[key] = int(np.count_nonzero(mask(slot, RED)))
        blue[key] = int(np.count_nonzero(mask(slot, BLUE)))
        hit = find_template(gray[y:y + sh + 2, x - 2:x + sw + 2], "attack_label", threshold=0.7)
        if hit:
            attack[key] = hit[2]
    keys = {}
    if attack:
        keys["attack"] = max(attack, key=attack.get)
    for name, counts in (("hp_potion", red), ("mp_potion", blue)):
        key = max(counts, key=counts.get)
        if counts[key] >= 60:  # a potion icon fills ~150 px; small red/blue bits of other icons stay under ~40
            keys[name] = key
    return keys


def dot_candidates(minimap, ranges, min_area=6, max_size=10):
    """Round blobs of the given colour: [(x, y, area)], largest first."""
    n, _, stats, cents = cv2.connectedComponentsWithStats(mask(minimap, ranges), connectivity=8)
    out = []
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if area >= min_area and 2 <= w <= max_size and 2 <= h <= max_size and abs(int(w) - int(h)) <= 2:
            out.append((float(cents[i][0]), float(cents[i][1]), int(area)))
    return sorted(out, key=lambda b: -b[2])


def tuned_hsv(minimap, pos, radius=2):
    """HSV range fitted to the dot at pos (the player's dot), as config `player_hsv`."""
    x, y = int(round(pos[0])), int(round(pos[1]))
    patch = cv2.cvtColor(minimap[max(0, y - radius):y + radius + 1, max(0, x - radius):x + radius + 1],
                         cv2.COLOR_BGR2HSV).reshape(-1, 3)
    patch = patch[patch[:, 1] > 80]  # drop the ring / background pixels
    if not len(patch):
        return None
    h, s, v = np.median(patch, axis=0)
    # Fairly tight on saturation: coins on shop signs can show through the minimap in a duller yellow.
    return [[[int(max(0, h - 6)), int(max(100, s - 35)), int(max(140, v - 50))], [int(min(180, h + 6)), 255, 255]]]
