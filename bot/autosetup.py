"""Auto setup: opens the game, finds its UI, tests the keys, and saves what it learned.

Login stays with you: it opens the game and waits until you're standing in a map.
"""
import logging
import os
import subprocess
import threading
import time
from pathlib import Path

import cv2

from bot import capture, config, detect, vision
from bot.keys import Keys

log = logging.getLogger("bot.setup")

SHORTCUT_NAME = "MapleStory Classic World.lnk"
LAUNCHER = (r"C:\Program Files (x86)\Nexon\Nexon Launcher\nexon_launcher.exe", "nxl://launch/59813")
WIDE_YELLOW = [((15, 100, 150), (40, 255, 255))]  # any yellow-ish dot, before the range is fitted


class SetupError(Exception):
    """A step failed in a way the rest of the setup can't work around."""


class Cancelled(Exception):
    pass


def find_shortcut():
    folders = [Path.home() / "Desktop", Path.home() / "OneDrive" / "Desktop",
               Path(os.environ.get("PUBLIC", "")) / "Desktop",
               Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs",
               Path(os.environ.get("PROGRAMDATA", "")) / "Microsoft/Windows/Start Menu/Programs"]
    for folder in folders:
        if folder.is_dir():
            for path in folder.rglob(SHORTCUT_NAME):
                return path
    return None


def launch_game(target="auto"):
    """Starts the game the way its shortcut does. Returns a description of what was started."""
    if target and target != "auto":
        os.startfile(target)
        return str(target)
    shortcut = find_shortcut()
    if shortcut:
        os.startfile(shortcut)
        return str(shortcut)
    if Path(LAUNCHER[0]).exists():
        subprocess.Popen(list(LAUNCHER))
        return " ".join(LAUNCHER)
    raise SetupError(f"Couldn't find the '{SHORTCUT_NAME}' shortcut or the Nexon Launcher. "
                     "Set `launch:` in config.yaml to the game's shortcut, or open the game yourself.")


class AutoSetup:
    def __init__(self, cfg, cancel=None, launch=True, on_update=None):
        self.cfg = cfg
        self.on_update = on_update  # called (ok, message) from this thread; ok None = what it's doing now
        self.k = cfg["keys"]
        self.cancel = cancel or threading.Event()
        self.launch = launch
        self.keys = Keys()
        self.game = capture.game_window(cfg)
        self.results = []  # (ok, message)

    # ------------------------------------------------------------- helpers
    def note(self, ok, message):
        self.results.append((ok, message))
        (log.info if ok else log.warning)("%s %s", "OK  " if ok else "FIX ", message)
        if self.on_update:
            self.on_update(ok, message)

    def say(self, message):
        log.info(message)
        if self.on_update:
            self.on_update(None, message)

    def sleep(self, seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if self.cancel.is_set():
                raise Cancelled
            time.sleep(min(0.05, max(0.0, end - time.monotonic())))

    def grab(self):
        frame = self.game.grab()
        if frame is None:
            raise SetupError("The game window closed or was minimized.")
        return frame

    def ensure_focus(self, timeout=30):
        if self.game.is_foreground() or self.game.bring_to_front():
            return
        self.say("Click into the game window so I can test the keys...")
        end = time.monotonic() + timeout
        while not self.game.is_foreground():
            if time.monotonic() > end:
                raise SetupError("The game never got focus, so the key tests were skipped.")
            self.sleep(0.2)
        self.sleep(0.3)

    def hold(self, key, seconds):
        self.ensure_focus()
        self.keys.down(key)
        try:
            self.sleep(seconds)
        finally:
            self.keys.up(key)

    def dots(self, minimap_region, ranges=WIDE_YELLOW):
        img = vision.crop(self.grab(), minimap_region)
        return detect.dot_candidates(img, ranges, self.cfg["vision"]["dot_min_area"],
                                     self.cfg["vision"]["dot_max_size"])

    @staticmethod
    def nearest(blobs, pos):
        if not blobs:
            return None
        return min(blobs, key=lambda b: (b[0] - pos[0]) ** 2 + (b[1] - pos[1]) ** 2)

    # --------------------------------------------------------------- steps
    def run(self):
        try:
            self.step_window()
            frame, minimap = self.step_wait_for_map()
            player = self.step_player(minimap)
            hsv = self.step_fit_colour(minimap, player)
            self.step_jump(minimap, player, hsv)
            frame = self.grab()
            hp, mp = self.step_bars(frame)
            keys = self.step_keys(frame)
            self.step_others(frame, minimap, hsv)
            self.save(frame, minimap, hp, mp, keys, hsv)
        except Cancelled:
            self.note(False, "Auto setup was cancelled.")
        except SetupError as e:
            self.note(False, str(e))
        finally:
            self.keys.release_all()
        return self.results

    def step_window(self):
        if self.game.locate():
            self.note(True, f"Game window found ({self.game.title}).")
            return
        if not self.launch:
            raise SetupError("The game isn't running. Open it (or tick 'Open the game') and try again.")
        started = launch_game(self.cfg.get("launch", "auto"))
        self.say(f"Opened the game ({started}). Log in, pick your character and enter a map.")
        end = time.monotonic() + 180
        while not self.game.locate():
            if time.monotonic() > end:
                raise SetupError("The game window didn't appear within 3 minutes.")
            self.sleep(1)
        self.note(True, "Game opened.")

    def step_wait_for_map(self):
        end = time.monotonic() + self.cfg.get("login_timeout", 900)
        last_hint = 0.0
        while True:
            frame = self.game.grab()
            region = detect.find_minimap(frame) if frame is not None else None
            if region:
                self.note(True, f"Minimap found at {region}.")
                return frame, region
            now = time.monotonic()
            if now > end:
                raise SetupError("Didn't see the minimap in time. Log in, enter a map, open the minimap, "
                                 "keep the game uncovered, and run Auto setup again.")
            if now - last_hint > 20:
                self.say("Waiting for you to be in a map with the minimap open (keep the game uncovered)...")
                last_hint = now
            self.sleep(1)

    def step_player(self, minimap):
        """Finds which yellow dot is you by walking a little and seeing which one moves."""
        self.say("Finding your dot: walking a step right and left...")
        before = self.dots(minimap)
        if not before:
            raise SetupError("Couldn't find your yellow dot on the minimap.")
        for direction, back in (("right", "left"), ("left", "right")):
            self.hold(self.k[direction], 0.4)
            self.sleep(0.4)
            after = self.dots(minimap)
            moves = []
            for b in before:
                a = self.nearest(after, b)
                if a and abs(a[1] - b[1]) <= 3:
                    moves.append((abs(a[0] - b[0]), a))
            self.hold(self.k[back], 0.4)  # walk back to where we started
            self.sleep(0.4)
            if moves and max(moves)[0] >= 1:
                _, moved = max(moves, key=lambda m: m[0])
                self.note(True, f"Arrow keys work: your dot is the one that moved ({len(before)} yellow dot(s) seen).")
                now = self.nearest(self.dots(minimap), moved) or moved
                return now[:2]
        if len(before) == 1:
            self.note(False, "Your character didn't move when I pressed the arrow keys. If the game runs as admin, "
                             "open the bot with 'Start Bot (Admin).bat'; also check Left/Right in Settings.")
            return before[0][:2]
        raise SetupError("Your character didn't move when I pressed the arrow keys, so I can't tell which "
                         "yellow dot is you. Check the admin note and the Left/Right keys, then try again.")

    def step_fit_colour(self, minimap, player):
        img = vision.crop(self.grab(), minimap)
        hsv = detect.tuned_hsv(img, player)
        if hsv is None:
            self.note(False, "Couldn't read your dot's colour; keeping the default colour range.")
            return self.cfg["vision"]["player_hsv"]
        found = self.dots(minimap, hsv)
        if len(found) == 1:
            self.note(True, "Fitted the colour range to your dot (it now matches only you).")
        else:
            self.note(False, f"The fitted colour range matches {len(found)} dots; the bot will follow the one "
                             "nearest your last position.")
        return hsv

    def step_jump(self, minimap, player, hsv):
        self.say("Testing the jump key...")
        start = self.nearest(self.dots(minimap, hsv), player)
        if not start:
            self.note(False, "Lost your dot before the jump test.")
            return
        self.ensure_focus()
        self.keys.press(self.k["jump"], hold=0.1)
        highest = start[1]
        end = time.monotonic() + 0.9
        pos = start
        while time.monotonic() < end:
            pos = self.nearest(self.dots(minimap, hsv), pos) or pos
            highest = min(highest, pos[1])
            self.sleep(0.03)
        if start[1] - highest >= 1:
            self.note(True, f"Jump key ({self.k['jump']}) works.")
        else:
            self.note(False, f"Your character didn't jump when I pressed {self.k['jump']}. "
                             "Set the Jump key in Settings to the key you jump with.")

    def step_bars(self, frame):
        self.say("Reading the HP/MP bars and your quickslot keys...")
        hp, mp = detect.find_bars(frame)
        self.note(hp is not None, f"HP bar found at {hp}." if hp else "HP bar not found (drink a potion so "
                                                                        "it's not empty, then run again).")
        self.note(mp is not None, f"MP bar found at {mp}." if mp else "MP bar not found.")
        if (hp is None) != (mp is None):
            self.note(False, "Only one bar was found, so its width may be short if it isn't full. "
                             "Run again with HP and MP full for an exact reading.")
        return hp, mp

    def step_keys(self, frame):
        keys = detect.quickslot_keys(frame)
        names = {"attack": "Attack", "hp_potion": "HP potion", "mp_potion": "MP potion"}
        for name, label in names.items():
            if name in keys:
                self.note(True, f"{label} key: {keys[name]} (read from your quickslot bar).")
            else:
                self.note(False, f"{label} key not found on the quickslot bar; keeping '{self.k.get(name)}'.")
        return keys

    def step_others(self, frame, minimap, hsv):
        img = vision.crop(frame, minimap)
        v = self.cfg["vision"]
        others = detect.dot_candidates(img, v["other_player_hsv"], v["dot_min_area"], v["dot_max_size"])
        if others and self.cfg["safety"]["pause_on_other_player"]:
            self.note(False, f"{len(others)} other player(s) are on this map. The bot pauses whenever someone "
                             "is on the minimap, so train on a quieter map or channel.")

    def save(self, frame, minimap, hp, mp, keys, hsv):
        cal = config.read_yaml(self.cfg["calibration_file"], {}) or {}
        regions = vision.Vision(self.cfg).regions_for(frame)  # a bar not found again stays where it was on screen
        regions["minimap"] = minimap
        if hp:
            regions["hp_bar"] = hp
        if mp:
            regions["mp_bar"] = mp
        cal["regions"], cal["frame_width"] = regions, frame.shape[1]
        config.save_yaml(self.cfg["calibration_file"], cal)

        settings = config.read_yaml(config.SETTINGS_FILE, {}) or {}
        config.deep_merge(settings, {"keys": keys, "vision": {"player_hsv": hsv}})
        route = config.read_yaml(self.cfg["route"], {}) or {}
        if self.cfg.get("mode") == "route" and not route.get("waypoints"):
            settings["mode"] = "hunt"
            self.note(True, "No route is recorded yet, so I set Training mode to hunt (it finds monsters and "
                            "throws at them, walking back and forth). Record a route to make it walk one.")
        config.save_yaml(config.SETTINGS_FILE, settings)

        merged = dict(self.cfg, regions=regions, calibrated_width=frame.shape[1])
        merged["vision"] = dict(self.cfg["vision"], player_hsv=hsv)
        state = vision.Vision(merged).read(frame)
        Path("debug").mkdir(exist_ok=True)
        cv2.imwrite("debug/screenshot.png", frame)
        cv2.imwrite("debug/overlay.png", vision.draw_overlay(frame, regions, state))
        self.note(True, "Saved to calibration.yaml and settings.yaml (picture: debug/overlay.png).")
