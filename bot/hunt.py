"""Hunt mode: finds your character and the monsters on screen, goes for the one that's quickest to get in range
(on your level first, climbing or dropping to another level when there's none on yours) and throws stars at it
once it's in range, then goes for the next.

  - You: your name is read off the HUD (bottom left), and the same text is found in the name tag under
    your character (same font and size).
  - Monsters, three ways:
      moving    standing still for a moment, whatever moves without a player's name tag under it
                (monsters walk, bounce or breathe; the background doesn't)
      HP bar    a monster that was hit shows one above it, found every time
      picture   what monsters look like, learned as they get hit: a hit monster sits between its HP bar
                and its name ("Lv.6 Slime"); kept in mobs/<minimap size>/, one folder per map
    Each one found is followed from frame to frame by its picture, so the bot can walk to it, but it stays
    on the list only while one of the three keeps showing it there.
  - It doesn't throw at nothing: something that moved but shows no HP bar after a second of throwing is
    written off, and things that look like it are ignored from then on.

Throwing stars reach 200 px ahead (400 px with Keen Eyes maxed), 124 px up and 68 px down from your feet;
inside 65 px the claw punches instead (MapleStory Classic numbers, from MeowDB).
All sizes are in pixels of frames scaled to 768 px tall (see capture.UI_HEIGHT).
"""
import logging
import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

log = logging.getLogger(__name__)

PLAY_BOTTOM = 700   # the chat line and HUD are below this
ASSETS = Path(__file__).resolve().parent.parent / "assets"
BAR_W, BAR_H = 52, 10          # a monster's HP bar, frame included
FILL = (3, 3, 46, 3)           # where its fill sits inside the frame (x, y, w, h)
HUD_NAME = (-325, -19, 175, 16)  # where your name is on the HUD, from the bottom centre (x, y, w, h)
SCALE = 0.5                    # monsters are looked for on frames shrunk this much (4x faster)


# ------------------------------------------------------------------ name tags
def text_mask(img):
    """Pixels of name-tag text (white-ish), as 0/1."""
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    return ((hsv[:, :, 1] < 70) & (hsv[:, :, 2] > 140)).astype(np.uint8)


def find_tags(frame, top=0, bottom=PLAY_BOTTOM):
    """Name tags (x, y, w, h) between rows top and bottom: white text in a dark box.
    Players, pets and monsters that were hit have one."""
    img = frame[top:bottom]
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    white = ((hsv[:, :, 1] < 70) & (hsv[:, :, 2] > 140)).astype(np.uint8)
    dark = hsv[:, :, 2] < 75
    joined = cv2.dilate(white, cv2.getStructuringElement(cv2.MORPH_RECT, (7, 1)))
    n, _, stats, _ = cv2.connectedComponentsWithStats(joined, connectivity=8)
    tags = []
    for i in range(1, n):
        x, y, w, h, _ = (int(v) for v in stats[i])
        if not (6 <= h <= 13 and 12 <= w <= 160):
            continue
        x0, y0, x1, y1 = max(0, x - 3), max(0, y - 3), min(img.shape[1], x + w + 3), min(img.shape[0], y + h + 3)
        text = white[y0:y1, x0:x1].astype(bool)
        background = dark[y0:y1, x0:x1][~text]
        if background.size and background.mean() > 0.45 and 0.08 < text.mean() < 0.5:
            tags.append((x0, top + y0, x1 - x0, y1 - y0))
    return tags


def merge_row(tags):
    """Joins boxes that sit side by side on one line ("Lv.6" + "Slime")."""
    out = []
    for t in sorted(tags):
        if out:
            x, y, w, h = out[-1]
            if abs(t[1] - y) <= 4 and 0 <= t[0] - (x + w) <= 8:
                top, right, bottom = min(y, t[1]), max(x + w, t[0] + t[2]), max(y + h, t[1] + t[3])
                out[-1] = (x, top, right - x, bottom - top)
                continue
        out.append(t)
    return out


class Me:
    """Finds your character on screen: your name from the HUD, matched against the name tags."""

    def __init__(self):
        self.tpl = None       # your name as text pixels
        self.last = None      # (x, y) where the tag was last seen
        self.last_full = float("-inf")

    @property
    def known(self):
        return self.tpl is not None

    def read_hud(self, frame):
        """Reads your name (as a picture of its letters) off the HUD. Returns True if it found one."""
        h, w = frame.shape[:2]
        dx, dy, rw, rh = HUD_NAME
        gray = cv2.cvtColor(frame[h + dy:h + dy + rh, w // 2 + dx:w // 2 + dx + rw], cv2.COLOR_BGR2GRAY)
        _, text = cv2.threshold(gray, 0, 1, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        cols = np.nonzero(text.sum(axis=0))[0]
        if len(cols) < 5:
            return False
        end = cols[0]
        for c in cols[1:]:  # the name ends at the first wide gap (the HP gauge's frame comes next)
            if c - end > 6:
                break
            end = c
        text = text[:, cols[0]:end + 1]
        rows = np.nonzero(text.sum(axis=1))[0]
        if end - cols[0] < 12 or not 5 <= rows.max() - rows.min() <= 13:
            return False
        self.tpl = np.pad(text[rows.min():rows.max() + 1], 2).astype(np.float32)
        return True

    def locate(self, frame, now):
        """(x, feet_y) of your character, or None. Looks near where it was last, the whole screen now and then."""
        if self.tpl is None:
            return None
        th, tw = self.tpl.shape
        hit = None
        if self.last:
            x, y = self.last
            hit = self._search(frame, x - 260, y - 160, x + tw + 260, y + th + 160)
        if hit is None and now - self.last_full >= 0.5:
            self.last_full = now
            hit = self._search(frame, 0, 0, frame.shape[1], PLAY_BOTTOM)
        if hit is None:
            return None
        self.last = hit
        return hit[0] + tw / 2, hit[1] - 1  # the tag hangs just under the feet

    def _search(self, frame, x0, y0, x1, y1):
        x0, y0, x1, y1 = max(0, x0), max(0, y0), min(frame.shape[1], x1), min(PLAY_BOTTOM, y1)
        th, tw = self.tpl.shape
        if x1 - x0 < tw or y1 - y0 < th:
            return None
        found = text_mask(frame[y0:y1, x0:x1]).astype(np.float32)
        _, score, _, (bx, by) = cv2.minMaxLoc(cv2.matchTemplate(found, self.tpl, cv2.TM_CCOEFF_NORMED))
        return (x0 + bx, y0 + by) if score >= 0.58 else None  # other tags score under 0.5


# ------------------------------------------------------------------ monsters
_bar = None


def _bar_template():
    global _bar
    if _bar is None:
        img = cv2.imread(str(ASSETS / "mob_hp_bar.png"), cv2.IMREAD_GRAYSCALE)
        if img is None:
            raise FileNotFoundError(ASSETS / "mob_hp_bar.png")
        mask = np.full(img.shape, 255, np.uint8)
        fx, fy, fw, fh = FILL
        mask[fy:fy + fh, fx:fx + fw] = 0  # the fill changes with HP; only the frame is compared
        _bar = (img, mask)
    return _bar


def find_hp_bars(frame, top=0, bottom=PLAY_BOTTOM):
    """Monster HP bars (x, y, w, h) between rows top and bottom. One shows above every monster hit lately."""
    img = frame[top:bottom]
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    hue, sat, val = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
    fill = ((sat > 150) & (val > 90) & (((hue > 45) & (hue < 75)) | (hue < 8) | (hue > 170))).astype(np.uint8)
    _, _, stats, _ = cv2.connectedComponentsWithStats(fill, connectivity=4)
    tpl, mask = _bar_template()
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    xs, ys, widths, heights = (stats[:, i] for i in (cv2.CC_STAT_LEFT, cv2.CC_STAT_TOP, cv2.CC_STAT_WIDTH,
                                                       cv2.CC_STAT_HEIGHT))
    shaped = (heights >= 2) & (heights <= 5) & (widths >= 2) & (ys >= 3) & (ys + 6 < img.shape[0]) & \
             (xs + 46 < img.shape[1])
    shaped[0] = False  # the background
    idx = np.nonzero(shaped)[0]
    y = ys[idx]
    g = gray.astype(np.int16)

    def framed(col):
        """A quick check before matching: in this column, the frame's dark outline, then its light grey line
        just above the fill, and another light line just below it. The fill's top row is sometimes caught and
        sometimes not, hence two cases above and two rows below."""
        above, line, outline = g[y - 1, col], g[y - 2, col], g[y - 3, col]
        top = ((line > 110) & (outline < 110) & (above < 110)) | ((above > 110) & (line < 110))
        return top & (np.maximum(g[y + 5, col], g[y + 6, col]) > 110)

    idx = idx[framed(xs[idx] + widths[idx] // 2) & framed(xs[idx] + 40)]  # mid-fill, and where the bar ends
    bars = []
    for i in idx:
        x, y, w, h, _ = (int(v) for v in stats[i])
        x0, y0 = x - FILL[0] - 2, y - FILL[1] - 2  # the fill starts at the bar's left edge
        if any(abs(x0 - b[0]) <= 4 and abs(top + y0 - b[1]) <= 4 for b in bars):
            continue
        if x0 < 0 or y0 < 0 or x0 + BAR_W + 4 > img.shape[1] or y0 + BAR_H + 4 > img.shape[0]:
            continue
        diff = cv2.matchTemplate(gray[y0:y0 + BAR_H + 4, x0:x0 + BAR_W + 4], tpl, cv2.TM_SQDIFF, mask=mask)
        err, _, (ox, oy), _ = cv2.minMaxLoc(diff)
        if err / np.count_nonzero(mask) <= 30 ** 2:
            bars.append((x0 + ox, top + y0 + oy, BAR_W, BAR_H))
    return bars


def under_bar(bar, tags):
    """The monster under an HP bar: (box between the bar and its name tag, or None; its feet y)."""
    bx, by, bw, bh = bar
    cx = bx + bw / 2
    below = [t for t in merge_row(tags) if by + bh + 15 <= t[1] <= by + bh + 260 and abs(t[0] + t[2] / 2 - cx) <= 45]
    if not below:
        return None, by + bh + 50  # name hidden: guess a small monster's height
    tx, ty, tw, th = min(below, key=lambda t: t[1])
    half = max(bw, tw) / 2 + 10
    x0, y0, x1, y1 = int(cx - half), by + bh + 2, int(cx + half), ty - 1
    return ((x0, y0, x1 - x0, y1 - y0) if y1 - y0 >= 12 else None), ty


def cut_out(frame, box):
    """The monster's own pixels inside box (GrabCut): (picture, mask), or None if it didn't come out clean."""
    x, y, w, h = box
    pad = 6
    x0, y0 = max(0, x - pad), max(0, y - pad)
    crop = frame[y0:min(PLAY_BOTTOM, y + h + pad), x0:x + w + pad]
    if crop.shape[0] < 20 or crop.shape[1] < 20:
        return None
    mask = np.zeros(crop.shape[:2], np.uint8)
    try:
        cv2.grabCut(crop, mask, (x - x0, y - y0, w, h), np.zeros((1, 65)), np.zeros((1, 65)), 3,
                    cv2.GC_INIT_WITH_RECT)
    except cv2.error:
        return None
    fg = ((mask == cv2.GC_FGD) | (mask == cv2.GC_PR_FGD)).astype(np.uint8)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(fg)
    if n < 2:
        return None
    fg = (labels == 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))).astype(np.uint8)
    ys, xs = np.nonzero(fg)
    if ys.max() - ys.min() < 14 or xs.max() - xs.min() < 14:
        return None
    pic = crop[ys.min():ys.max() + 1, xs.min():xs.max() + 1].copy()
    fg = fg[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    return (pic, fg) if fg.mean() > 0.35 else None


def _alike(a, b):
    """How alike two pictures are (0-1), either facing."""
    if a.shape[0] > b.shape[0] or a.shape[1] > b.shape[1]:
        a, b = b, a
    if a.shape[0] > b.shape[0] or a.shape[1] > b.shape[1]:
        h, w = min(a.shape[0], b.shape[0]), min(a.shape[1], b.shape[1])
        a, b = a[:h, :w], b[:h, :w]
    return max(cv2.minMaxLoc(cv2.matchTemplate(b, t, cv2.TM_CCOEFF_NORMED))[1] for t in (a, cv2.flip(a, 1)))


def _overlap(a, b):
    """Shared area of two boxes, as a share of the smaller one."""
    w = min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])
    h = min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
    return 0.0 if w <= 0 or h <= 0 else w * h / max(1, min(a[2] * a[3], b[2] * b[3]))


class MobBook:
    """The monster pictures learned on one map, and spotting those monsters on screen."""

    MAX = 12           # pictures kept per map (the one that went longest without finding a monster goes)
    MIN_SIZE = 24      # px; smaller cut-outs are scraps of a monster, which match all sorts of things
    THRESHOLD = 0.68   # how alike a spot must be to a picture
    COLOUR = 28        # and how close in colour, over the monster's own pixels (0-255)

    def __init__(self, folder):
        self.folder = Path(folder)
        self.pictures = []  # (picture, mask, small picture, small mask, small grey picture, file)
        self.pending = []   # (when, x, picture): cut-outs waiting for a second look to confirm them
        self.used = {}      # file -> when that picture last found a monster
        for path in sorted(self.folder.glob("*.png")):
            img = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
            if img is None or img.ndim != 3 or img.shape[2] != 4:
                continue
            if min(img.shape[:2]) < self.MIN_SIZE:
                path.unlink(missing_ok=True)  # learned by an older version
                continue
            self._add(img[:, :, :3].copy(), (img[:, :, 3] > 0).astype(np.uint8), path)

    def __len__(self):
        return len(self.pictures)

    def _add(self, pic, mask, path):
        small = cv2.resize(pic, None, fx=SCALE, fy=SCALE, interpolation=cv2.INTER_AREA)
        small_mask = cv2.resize(mask, (small.shape[1], small.shape[0]), interpolation=cv2.INTER_NEAREST).astype(bool)
        self.pictures.append((pic, mask, small, small_mask, cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), path))

    def find(self, frame, top=0, bottom=PLAY_BOTTOM):
        """Spots between rows top and bottom that look like a learned monster: [(x, y, w, h)].
        Matched in grey (3x faster), then checked in colour."""
        if not self.pictures:
            return []
        top = max(0, top) // 2 * 2
        img = cv2.resize(frame[top:bottom], None, fx=SCALE, fy=SCALE, interpolation=cv2.INTER_AREA)
        grey = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        wide = img.astype(np.int16)
        found = []
        for _, _, small, small_mask, small_grey, path in self.pictures:
            h, w = small.shape[:2]
            if h >= img.shape[0] or w >= img.shape[1]:
                continue
            for pic, mask, pic_grey in ((small, small_mask, small_grey),
                                        (cv2.flip(small, 1), small_mask[:, ::-1], cv2.flip(small_grey, 1))):
                result = cv2.matchTemplate(grey, pic_grey, cv2.TM_CCOEFF_NORMED)
                for _ in range(10):
                    _, score, _, (x, y) = cv2.minMaxLoc(result)
                    if score < self.THRESHOLD:
                        break
                    cv2.rectangle(result, (x - w // 2, y - h // 2), (x + w // 2, y + h // 2), -1, -1)
                    if np.abs(wide[y:y + h, x:x + w] - pic)[mask].mean() <= self.COLOUR:
                        found.append((score, (int(x / SCALE), int(top + y / SCALE), int(w / SCALE), int(h / SCALE))))
                        self.used[path] = time.monotonic()
        found.sort(key=lambda f: -f[0])
        kept = []
        for _, box in found:  # one monster per spot
            if all(_overlap(box, k) < 0.3 for k in kept):
                kept.append(box)
        return kept

    def learn(self, frame, bars, tags, now):
        """Cuts out the monsters under these HP bars and keeps the ones that look new, once a second look at
        the same monster confirms the cut-out (a hit flash or someone walking in front spoils it)."""
        self.pending = [p for p in self.pending if now - p[0] <= 2.0]
        for bar in bars:
            box, _ = under_bar(bar, tags)
            if box is None:
                continue
            x, y, w, h = box
            if x < 0 or x + w > frame.shape[1]:
                continue
            tag_row = [t for t in tags if abs(t[1] - (y + h + 1)) <= 6 and t[0] < x + w and x < t[0] + t[2]]
            if len(merge_row(tag_row)) > 1:
                continue  # another name on the monster's row: someone stands in front of it
            cut = cut_out(frame, box)
            if cut is None:
                continue
            cx = bar[0] + bar[2] / 2
            confirmed = any(abs(p[1] - cx) <= 60 and _alike(p[2], cut[0]) >= 0.75 for p in self.pending)
            self.pending.append((now, cx, cut[0]))
            if confirmed:
                self.remember(*cut)

    def remember(self, pic, mask):
        """Keeps a picture of a monster, unless it's a scrap or one alike is kept already."""
        if min(pic.shape[:2]) >= self.MIN_SIZE and all(_alike(p[0], pic) < 0.7 for p in self.pictures):
            self._save(pic, mask)

    def _save(self, pic, mask):
        self.folder.mkdir(parents=True, exist_ok=True)
        if len(self.pictures) >= self.MAX:
            idle = min(range(len(self.pictures)), key=lambda i: self.used.get(self.pictures[i][-1], 0.0))
            self.pictures.pop(idle)[-1].unlink(missing_ok=True)
        path = self.folder / f"mob_{int(time.time() * 1000)}.png"
        cv2.imwrite(str(path), np.dstack([pic, mask * 255]))
        self._add(pic, mask, path)
        self.used[path] = time.monotonic()  # a new picture gets its chance before it can go
        log.info("Learned what another monster looks like (%d pictures for this map).", len(self.pictures))


# ------------------------------------------------------------------ movement
SHIFT_SCALE = 0.25  # camera panning is measured on frames shrunk this much
_k3 = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
_k11 = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (11, 11))


def small_gray(frame):
    gray = cv2.cvtColor(frame[:PLAY_BOTTOM], cv2.COLOR_BGR2GRAY)
    return cv2.resize(gray, None, fx=SHIFT_SCALE, fy=SHIFT_SCALE, interpolation=cv2.INTER_AREA).astype(np.float32)


def camera_shift(before, after):
    """How far the scene moved on screen between two small_gray frames (the camera panning), in screen px."""
    (dx, dy), _ = cv2.phaseCorrelate(before, after)
    return dx / SHIFT_SCALE, dy / SHIFT_SCALE


def _changed(a, b, top, bottom):
    """Pixels that changed from frame a to b between rows top and bottom, after undoing the camera's drift.
    Drifting clouds don't count."""
    ga, gb = (cv2.GaussianBlur(cv2.cvtColor(f[top:bottom], cv2.COLOR_BGR2GRAY), (3, 3), 0).astype(np.float32)
              for f in (a, b))
    (dx, dy), _ = cv2.phaseCorrelate(ga, gb)
    ga = cv2.warpAffine(ga, np.float32([[1, 0, dx], [0, 1, dy]]), (ga.shape[1], ga.shape[0]),
                        borderMode=cv2.BORDER_REPLICATE)
    hsv = cv2.cvtColor(b[top:bottom], cv2.COLOR_BGR2HSV)
    cloud = (hsv[:, :, 1] < 45) & (hsv[:, :, 2] > 175)
    changed = ((np.abs(gb - ga) > 28) & ~cloud).astype(np.uint8)
    changed = cv2.morphologyEx(changed, cv2.MORPH_CLOSE, _k11)
    return cv2.morphologyEx(changed, cv2.MORPH_OPEN, _k3)


def _blobs(changed, hsv, top):
    n, _, stats, _ = cv2.connectedComponentsWithStats(changed)
    out = []
    for i in range(1, n):
        x, y, w, h, area = (int(v) for v in stats[i])
        if area < 150 or not (14 <= w <= 160 and 14 <= h <= 140) or area / (w * h) < 0.3:
            continue
        patch = hsv[y:y + h, x:x + w]
        number = (patch[:, :, 1] > 200) & (patch[:, :, 2] > 180) & \
                 ((patch[:, :, 0] < 30) | ((patch[:, :, 0] > 125) & (patch[:, :, 0] < 165)))
        if h < 36 and number.mean() > 0.6:
            continue  # a damage number floating up
        out.append((x, top + y, w, h))
    return out


def moving_things(frames, players, top, bottom):
    """Things that moved across frames taken a moment apart while standing (x, y, w, h), between rows top and
    bottom, in both halves of the look, and without a player's or pet's name tag under them. Monsters are
    never fully still: they walk, bounce or breathe."""
    halves = []
    for a, b in zip(frames, frames[1:]):
        hsv = cv2.cvtColor(b[top:bottom], cv2.COLOR_BGR2HSV)
        halves.append(_blobs(_changed(a, b, top, bottom), hsv, top))
    found = []
    for box in halves[-1]:
        x, y, w, h = box
        if not all(any(abs((x + w / 2) - (p[0] + p[2] / 2)) < 30 and abs((y + h) - (p[1] + p[3])) < 25 for p in half)
                   for half in halves[:-1]):
            continue  # moved only once: a passing effect
        if any(t[1] - 45 <= y + h <= t[1] + 25 and x - 10 < t[0] + t[2] / 2 < x + w + 10 for t in players):
            continue  # a player or pet
        found.append(box)
    return found


def player_tags(tags):
    """The tags of players and pets: a hit monster's tag comes in two boxes ("Lv.6" + "Slime")."""
    singles = []
    for t in tags:
        paired = any(o is not t and abs(o[1] - t[1]) <= 4 and (0 <= o[0] - (t[0] + t[2]) <= 8 or
                                                                  0 <= t[0] - (o[0] + o[2]) <= 8) for o in tags)
        if not paired:
            singles.append(t)
    return singles


# ------------------------------------------------------------------ hunting
@dataclass(eq=False)
class Track:
    """A monster (or something that might be one) followed from frame to frame."""
    box: tuple              # (x, y, w, h) on screen
    pic: np.ndarray         # its picture, to find it again in the next frame
    found_by: str           # "moving", "picture" or "hp bar"
    confirmed: float        # when an HP bar, a picture match or movement last showed it's there
    hit: bool = False       # showed an HP bar: certainly a monster
    bar_seen: float = None  # when its HP bar last showed
    attacked: float = None  # when the bot started throwing at it
    heading: float = None   # when the bot set off for it on another level

    @property
    def x(self):
        return self.box[0] + self.box[2] / 2

    @property
    def feet(self):
        return self.box[1] + self.box[3]

    @property
    def sure(self):
        return self.hit or self.found_by == "picture"


class Hunter:
    """Hunts the monsters it can see, on the whole screen. It goes for the one that's cheapest to get in range:
    one in range on your level costs nothing, one further along costs the walk, one on another level the climb
    or drop on top, a guess (something that moved) more than any sure monster, and one with another player
    next to it extra (they'll likely kill it first). It throws once it's in range and goes for the next one
    when it dies. With none in sight it walks on (along the route, if there is one) and, when it knows no
    monster pictures for this map yet, stops now and then to look for movement.
    It doesn't throw at nothing: a monster stays on its list only while an HP bar, a picture match or
    movement keeps showing it there, and a guess that shows no HP bar after a second of throwing is written off."""

    HOLD_HIT = 0.5     # seconds a monster stays on the list after its HP bar last showed (it shows until it dies)
    HOLD_PIC = 0.6     # ... after the next picture scan was due
    HOLD_GUESS = 2.5   # ... after it was seen moving (movement is only looked for now and then)
    VERIFY = 1.0       # seconds of throwing before a guess that shows no HP bar is written off
    NEAR_SCAN = 0.3    # seconds between picture scans of your level
    FAR_SCAN = 1.0     # ... and of the whole screen
    SETTLE = 0.25      # seconds to stand before looking for movement (the camera glides a little after you stop)
    WALK_STEP = 1.2    # seconds to walk between looks for movement, when it knows no monster pictures
    LOOK_AFTER = 4.0   # when it does: seconds without a monster in sight before it stops to look for movement
    GIVE_UP = 8.0      # seconds heading for a monster on another level before trying elsewhere for a while
    AVOID = 20.0       # ... that while
    STICK = 0.75       # it switches targets only for one that costs this share of the current one, or less
    SCALE = 16.0       # screen px per minimap px until it has measured it (MapleStory maps measured 15-16)

    def __init__(self, cfg, keys, routine, mob_root):
        h = cfg["hunt"]
        self.k = cfg["keys"]
        self.attack_key = h.get("attack_key") or self.k["attack"]
        self.range, self.dead_zone = h["range"], h["dead_zone"]
        self.reach_up, self.reach_down = h["reach_up"], h["reach_down"]
        self.learn = h.get("learn", True)
        self.keys, self.routine = keys, routine
        self.mob_root = Path(mob_root)
        self.me = Me()
        self.book = None
        self.minimap = None
        self.tracks = []
        self.not_mobs = []       # (picture, until when) of things written off
        self.avoid = []          # (minimap y, until when) of levels it couldn't get to
        self.target = None
        self.state = "looking"
        self.facing = None
        self.pos_on_screen = None
        self.band = None
        self.prev_small = None
        self.me_lost_since = None
        self.look_frames, self.look_from = [], 0.0
        self.walk_until = 0.0
        self.backing_since = None
        self.last_near = self.last_far = self.last_learn = self.last_me_try = float("-inf")
        self.last_sight = self.last_look = float("-inf")
        self.patrol_dir = "right"
        self.stuck_x, self.stuck_since = None, None
        self.scale, self.ratios = self.SCALE, []
        self.scale_from = self.scale_prev = None
        self.world_x = 0.0

    # ---------------------------------------------------------------- status
    @property
    def moving(self):
        return self.state in ("walking", "chasing", "backing", "heading")

    @property
    def stationary(self):
        return self.state in ("attacking", "looking")

    def summary(self):
        seen = sum(1 for t in self.tracks if t.sure)
        if self.target is not None and self.pos_on_screen and self.state in ("attacking", "chasing", "backing",
                                                                              "heading"):
            dx = self.target.x - self.pos_on_screen[0]
            what = {"heading": "going to a monster on another level"}.get(
                self.state, f"{self.state} a monster {abs(dx):.0f} px {'right' if dx > 0 else 'left'}")
        else:
            what = {"looking": "looking for monsters", "walking": "walking on to find monsters",
                    "lost": "can't see my name tag"}.get(self.state, self.state)
        return f"{what} · {seen} in sight" + (f" · {len(self.book)} learned" if self.book else "")

    def set_map(self, minimap):
        """minimap: its region on screen (x, y, w, h). Its size tells maps apart; nothing in it is a monster."""
        x, y, w, h = (int(v) for v in minimap)
        self.minimap = (x - 10, y - 30, w + 20, h + 40)  # with its title bar and frame
        self.book = MobBook(self.mob_root / f"{w}x{h}")
        self.not_mobs, self.avoid = [], []
        self.scale, self.ratios = self.SCALE, []  # each map draws its minimap at its own scale
        log.info("Hunting: %d monster pictures known for this map; it learns more as monsters get hit.",
                 len(self.book))

    def resume(self):
        self.tracks, self.target, self.facing = [], None, None
        self.prev_small = self.scale_from = self.scale_prev = None
        self._start_look(None)  # the first tick sets when to look
        self.routine.resume()

    # ---------------------------------------------------------------- each tick
    def tick(self, frame, pos, now):
        if not self.me.known and now - self.last_me_try >= 1.0:
            self.last_me_try = now
            if self.me.read_hud(frame):
                log.info("Read your name off the HUD; finding your character by its name tag.")
        small = small_gray(frame)
        shift = camera_shift(self.prev_small, small) if self.prev_small is not None else (0.0, 0.0)
        self.prev_small = small

        found = me = self.me.locate(frame, now)
        if me is not None:
            self.me_lost_since = None
        else:
            self.me_lost_since = self.me_lost_since or now
            if self.pos_on_screen and now - self.me_lost_since < 1.0:  # hidden for a moment: it moved with the camera
                me = (self.pos_on_screen[0] + shift[0], self.pos_on_screen[1] + shift[1])
        self._measure_scale(found, pos, shift)
        if me is None:
            self.pos_on_screen = None
            self._release_all()
            self.state = "lost"
            return
        self.pos_on_screen = me
        x, feet = me
        top = max(0, int(feet - self.reach_up - 120))
        bottom = min(PLAY_BOTTOM, int(feet + self.reach_down + 15))
        self.band = (top, bottom)

        tags = find_tags(frame)
        players = [p for p in player_tags(tags) if not (abs(p[0] + p[2] / 2 - x) < 30 and abs(p[1] - feet) < 15)]
        self._follow(frame, shift)
        self._mark_hits(frame, find_hp_bars(frame), tags, now)
        self._scan(frame, now)
        self._forget(now, me, players)
        if self.state == "looking":
            self._look(frame, now, players)

        self.target = self._choose(me, pos, players, now)
        if self.target is not None:
            self.last_sight = now
            self._engage(self.target, me, pos, now)
        elif self.state not in ("looking", "walking"):
            self._search(now)
        elif self.state == "walking":
            if self._look_due(now):
                self._start_look(now)
            else:
                self._walk_on(pos, now)

    def _in_reach(self, track):
        """On your level: within the stars' reach up and down."""
        return self.pos_on_screen is not None and \
            -self.reach_up <= track.feet - self.pos_on_screen[1] <= self.reach_down

    def _measure_scale(self, me, pos, shift):
        """Learns how many screen px one minimap px is, from your own walking: your movement in the world (on
        screen, plus the camera's panning) against your dot's movement on the minimap."""
        if me is None or pos is None:
            self.scale_from = self.scale_prev = None
            return
        if self.scale_prev is not None:
            self.world_x += (me[0] - self.scale_prev[0]) - shift[0]
        self.scale_prev = me
        if self.scale_from is None:
            self.scale_from, self.world_x = pos[0], 0.0
        elif abs(pos[0] - self.scale_from) >= 6:
            ratio = self.world_x / (pos[0] - self.scale_from)
            if 4 <= ratio <= 40:
                self.ratios = (self.ratios + [ratio])[-15:]
                self.scale = float(np.median(self.ratios))
            self.scale_from, self.world_x = pos[0], 0.0

    # ---------------------------------------------------------------- seeing
    def _follow(self, frame, shift):
        """Moves each monster on the list to where it is in this frame (or where the camera's panning put it).
        Following alone never keeps a monster on the list: only a detector does (see _forget)."""
        for t in self.tracks:
            x, y, w, h = t.box
            px, py = int(round(x + shift[0])), int(round(y + shift[1]))
            x0, y0 = max(0, px - 40), max(0, py - 30)
            x1, y1 = min(frame.shape[1], px + w + 40), min(PLAY_BOTTOM, py + h + 30)
            if x1 - x0 <= w or y1 - y0 <= h:
                continue
            result = cv2.matchTemplate(frame[y0:y1, x0:x1], t.pic, cv2.TM_CCOEFF_NORMED)
            _, score, _, (bx, by) = cv2.minMaxLoc(result)
            t.box = (x0 + bx, y0 + by, w, h) if score >= 0.6 else (px, py, w, h)

    def _scan(self, frame, now):
        """Looks for the monsters it knows by picture: on your level often, on the whole screen now and then."""
        if self.book is None or not len(self.book):
            return
        if now - self.last_far >= self.FAR_SCAN:
            self.last_far = self.last_near = now
            boxes = self.book.find(frame)
        elif now - self.last_near >= self.NEAR_SCAN:
            self.last_near = now
            boxes = self.book.find(frame, *self.band)
        else:
            return
        for box in boxes:
            self._add(frame, box, "picture", now)

    def _add(self, frame, box, found_by, now, hit=False):
        """Starts following a newly found monster, or marks one already followed as still there."""
        x, y, w, h = (int(v) for v in box)
        x, y = max(0, x), max(0, y)
        if w < 8 or h < 8 or x + w > frame.shape[1] or y + h > PLAY_BOTTOM:
            return None
        box = (x, y, w, h)
        if self.minimap and _overlap(box, self.minimap) > 0:
            return None
        for t in self.tracks:
            if _overlap(t.box, box) >= 0.4:
                t.confirmed, t.hit = now, t.hit or hit
                if found_by == "picture" or t.found_by == "moving":  # a better look at it
                    t.box, t.pic, t.found_by = box, frame[y:y + h, x:x + w].copy(), found_by
                return t
        pic = frame[y:y + h, x:x + w].copy()
        if not hit and any(until > now and _alike(n, pic) >= 0.75 for n, until in self.not_mobs):
            return None
        track = Track(box, pic, found_by, now, hit, now if hit else None)
        self.tracks.append(track)
        return track

    def _mark_hits(self, frame, bars, tags, now):
        """An HP bar marks the monster under it as certain (and teaches the bot what it looks like)."""
        for bar in bars:
            cx, bottom = bar[0] + bar[2] / 2, bar[1] + bar[3]
            under = [t for t in self.tracks if abs(t.x - cx) <= max(20, t.box[2] / 2) and
                     bottom - 15 <= t.box[1] <= bottom + 70]
            if under:
                for t in under:
                    if not t.hit and self.learn and self.book is not None and t.found_by == "moving":
                        cut = cut_out(frame, t.box)
                        if cut is not None:
                            self.book.remember(*cut)
                    t.hit, t.confirmed, t.bar_seen = True, now, now
            else:
                box, feet = under_bar(bar, tags)
                if box is None:
                    box = (int(cx - 25), bottom + 2, 50, max(12, int(feet - bottom - 2)))
                t = self._add(frame, box, "hp bar", now, hit=True)
                if t is not None:
                    t.bar_seen = now
        if self.learn and self.book is not None and bars and now - self.last_learn >= 0.5:
            self.last_learn = now
            self.book.learn(frame, bars, tags, now)  # monsters others are fighting teach it too

    def _hold(self, t):
        """How long a monster stays on the list after a detector last showed it."""
        if t.bar_seen is not None and t.confirmed - t.bar_seen < 0.3:
            return self.HOLD_HIT  # its HP bar showed last: once that's gone, so is the monster
        if t.found_by == "picture":
            return (self.NEAR_SCAN if self._in_reach(t) else self.FAR_SCAN) + self.HOLD_PIC
        return self.HOLD_GUESS

    def _forget(self, now, me, players):
        """Drops monsters no detector showed lately (killed or gone), players, yourself, and guesses that took a
        second of throwing without showing an HP bar."""
        x, feet = me
        keep = []
        for t in self.tracks:
            if now - t.confirmed > self._hold(t):
                continue
            if _overlap(t.box, (x - 25, feet - 70, 50, 70)) > 0.5:
                continue  # that's you
            if not t.hit and any(p[1] - 45 <= t.feet <= p[1] + 25 and t.box[0] - 10 < p[0] + p[2] / 2 <
                                 t.box[0] + t.box[2] + 10 for p in players):
                continue
            if not t.hit and t.attacked is not None and now - t.attacked >= self.VERIFY:
                # Something that moved is ignored from then on; a picture match only for a while, since the
                # same picture finds the real monsters.
                until = float("inf") if t.found_by == "moving" else now + 10
                self.not_mobs = (self.not_mobs + [(t.pic, until)])[-30:]
                log.info("That wasn't a monster (no HP bar after throwing at it): ignoring things like it.")
                continue
            keep.append(t)
        self.tracks = keep
        self.avoid = [a for a in self.avoid if a[1] > now]

    def _choose(self, me, pos, players, now):
        """The monster that's cheapest to get in range, keeping the current one unless another is much cheaper."""
        if not self.tracks:
            return None
        cost = {id(t): self._cost(t, me, pos, players, now) for t in self.tracks}
        best = min(self.tracks, key=lambda t: cost[id(t)])
        current = next((t for t in self.tracks if t is self.target), None)
        if current is not None and cost[id(best)] > self.STICK * cost[id(current)] - 20:
            return current
        return best

    def _cost(self, t, me, pos, players, now):
        """About how many px it takes to get t in range, plus what makes it a worse pick."""
        x, feet = me
        dx = abs(t.x - x)
        if self._in_reach(t):
            cost = max(0.0, dx - self.range) + (60 if dx < self.dead_zone else 0)
        else:
            cost = 150 + dx + 3 * abs(t.feet - feet)  # a climb or drop away
            if pos is not None:
                y = pos[1] + (t.feet - feet) / self.scale
                if any(abs(y - a[0]) <= 4 for a in self.avoid):
                    cost += 2000
        if not t.sure:
            cost += 400
        if any(abs(p[0] + p[2] / 2 - t.x) < 70 and -10 <= p[1] - t.feet <= 30 for p in players):
            cost += 150  # someone's next to it
        return cost + dx * 0.01  # the nearer of two that cost the same

    def _search(self, now):
        """No monster in sight any more: stop to look for movement if it knows no pictures, else walk on."""
        if self.book is not None and len(self.book):
            self._release_all()
            self.state = "walking"
        else:
            self._start_look(now)

    def _look_due(self, now):
        if self.book is not None and len(self.book):
            return now - max(self.last_sight, self.last_look) >= self.LOOK_AFTER
        return now >= self.walk_until

    def _start_look(self, now):
        self._release_all()
        self.state, self.look_frames = "looking", []
        self.look_from = None if now is None else now + self.SETTLE

    def _look(self, frame, now, players):
        """Standing still: takes 3 frames 0.2 s apart and follows whatever moves in them, on your level."""
        if self.look_from is None:
            self.look_from = now + self.SETTLE
        if now < self.look_from:
            return
        self.look_frames.append(frame.copy())
        self.look_from = now + 0.2
        if len(self.look_frames) < 3:
            return
        top, bottom = self.band
        for box in moving_things(self.look_frames, players, top, bottom):
            self._add(frame, box, "moving", now)
        self.look_frames, self.last_look = [], now
        if not self.tracks:
            self.state, self.walk_until = "walking", now + self.WALK_STEP

    # ---------------------------------------------------------------- acting
    def _engage(self, target, me, pos, now):
        x, feet = me
        if not self._in_reach(target):
            self._head_for(target, me, pos, now)
            return
        target.heading = None
        dx = target.x - x
        side = "right" if dx > 0 else "left"
        if self.dead_zone <= abs(dx) <= self.range:
            self.backing_since = None
            self._attack(side, target, now)
        elif abs(dx) < self.dead_zone:  # too close to throw: step back, or punch if there's no room
            self.backing_since = self.backing_since or now
            if now - self.backing_since < 0.6:
                self.keys.up(self.attack_key)
                self._walk("left" if side == "right" else "right", "backing")
            else:
                self._attack(side, target, now)
        else:
            self.backing_since = None
            self.keys.up(self.attack_key)
            self._walk(side, "chasing")

    def _head_for(self, target, me, pos, now):
        """Goes to a monster on another level: along the learned map if it knows a way, else straight for it
        (climbing up or dropping down where it's above or below)."""
        self.keys.up(self.attack_key)
        x, feet = me
        dx, dy = (target.x - x) / self.scale, (target.feet - feet) / self.scale  # in minimap px
        target.heading = target.heading or now
        if now - target.heading >= self.GIVE_UP:
            if pos is not None:
                self.avoid.append((pos[1] + dy, now + self.AVOID))
                log.info("Couldn't get to the monsters on that level; hunting elsewhere for a while.")
            self.tracks = [t for t in self.tracks if t is not target]
            self._release_all()
            self.state = "walking"
            return
        if pos is not None:
            self.routine.go_to(pos, (pos[0] + dx, pos[1] + dy), now)
        else:
            self.routine.step_toward(dx, dy, now)
        held = self.keys.held
        self.facing = "right" if self.k["right"] in held else "left" if self.k["left"] in held else self.facing
        self.state = "heading"

    def _attack(self, side, target, now):
        self._release("left", "right", "up", "down")
        if self.facing != side:
            self.keys.up(self.attack_key)
            self.keys.press(self.k[side], hold=0.04)  # turn around without walking far
            self.facing = side
        if target.attacked is None:
            target.attacked = now
        self.keys.down(self.attack_key)
        self.state = "attacking"

    def _walk(self, side, state):
        self._release("up", "down", "left" if side == "right" else "right")
        self.keys.down(self.k[side])
        self.facing, self.state = side, state

    def _walk_on(self, pos, now):
        """Walks on to find monsters: along the route if there is one, else one way until a wall, then back."""
        self.keys.up(self.attack_key)
        if self.routine.waypoints and pos:
            self.routine.tick(pos, now)
            held = self.keys.held
            self.facing = "right" if self.k["right"] in held else "left" if self.k["left"] in held else self.facing
            return
        if pos:
            if self.stuck_x is None or abs(pos[0] - self.stuck_x) >= 1:
                self.stuck_x, self.stuck_since = pos[0], now
            elif now - self.stuck_since >= 0.6:
                self.patrol_dir = "left" if self.patrol_dir == "right" else "right"
                self.stuck_x, self.stuck_since = pos[0], now
        self._walk(self.patrol_dir, "walking")

    def _release(self, *names):
        for name in names:
            self.keys.up(self.k[name])

    def _release_all(self):
        self.keys.up(self.attack_key)
        self._release("left", "right", "up", "down")

    # ---------------------------------------------------------------- what it sees
    def draw(self, frame, scale=0.5):
        """A copy of the frame with what the bot sees on it: you, your reach, the monsters, the target."""
        view = frame.copy()
        if self.pos_on_screen:
            x, feet = (int(v) for v in self.pos_on_screen)
            cv2.rectangle(view, (x - self.range, feet - self.reach_up), (x + self.range, feet + self.reach_down),
                          (0, 200, 255), 1)
            cv2.rectangle(view, (x - self.dead_zone, feet - self.reach_up),
                          (x + self.dead_zone, feet + self.reach_down), (0, 120, 255), 1)
            cv2.circle(view, (x, feet), 5, (0, 255, 255), -1)
            if self.target is not None:
                cv2.line(view, (x, feet - 30), (int(self.target.x), int(self.target.box[1] + self.target.box[3] / 2)),
                         (255, 255, 255), 1, cv2.LINE_AA)
        for t in self.tracks:
            x, y, w, h = (int(v) for v in t.box)
            colour = (0, 0, 255) if t.hit else (255, 0, 255) if t.found_by == "picture" else (0, 165, 255)
            cv2.rectangle(view, (x, y), (x + w, y + h), colour, 3 if t is self.target else 1)
        cv2.rectangle(view, (0, 0), (view.shape[1], 26), (20, 20, 20), -1)
        cv2.putText(view, self.summary(), (8, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA)
        return cv2.resize(view, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
