"""Keyboard output through SendInput with hardware scan codes.

A held key repeats its key-down about 30 times a second, as a real keyboard does: the game acts on
each key-down, so a key pressed down once and left there attacks or jumps only once.
"""
import ctypes
import logging
import threading
import time
from ctypes import wintypes

log = logging.getLogger(__name__)

user32 = ctypes.WinDLL("user32", use_last_error=True)

INPUT_KEYBOARD = 1
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008
REPEAT = 1 / 30  # seconds between repeated key-downs of a held key


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
user32.SendInput.restype = wintypes.UINT

SCAN = {
    "esc": 0x01, "-": 0x0C, "=": 0x0D, "backspace": 0x0E, "tab": 0x0F, "[": 0x1A, "]": 0x1B,
    "enter": 0x1C, "ctrl": 0x1D, ";": 0x27, "'": 0x28, "`": 0x29, "shift": 0x2A, "\\": 0x2B,
    ",": 0x33, ".": 0x34, "/": 0x35, "alt": 0x38, "space": 0x39, "f11": 0x57, "f12": 0x58,
}
for _i, _c in enumerate("1234567890"):
    SCAN[_c] = 0x02 + _i
for _row, _start in (("qwertyuiop", 0x10), ("asdfghjkl", 0x1E), ("zxcvbnm", 0x2C)):
    for _i, _c in enumerate(_row):
        SCAN[_c] = _start + _i
for _i in range(10):
    SCAN[f"f{_i + 1}"] = 0x3B + _i

# Keys that need the extended-key flag (the arrow / navigation cluster).
EXTENDED = {
    "home": 0x47, "up": 0x48, "pageup": 0x49, "left": 0x4B, "right": 0x4D,
    "end": 0x4F, "down": 0x50, "pagedown": 0x51, "insert": 0x52, "delete": 0x53,
}


def validate(name):
    if str(name).lower() not in SCAN and str(name).lower() not in EXTENDED:
        raise ValueError(f"unknown key name: {name!r}")


class Keys:
    def __init__(self, dry_run=False):
        self.dry_run = dry_run
        self.held = set()
        self._lock = threading.Lock()  # a repeat never lands after its key's key-up
        self._repeater = None          # thread repeating the held keys; runs only while some are held

    def _send(self, name, key_up):
        if self.dry_run:
            log.info("[dry] %s %s", "up" if key_up else "down", name)
            return
        if name in EXTENDED:
            scan, flags = EXTENDED[name], KEYEVENTF_SCANCODE | KEYEVENTF_EXTENDEDKEY
        else:
            scan, flags = SCAN[name], KEYEVENTF_SCANCODE
        if key_up:
            flags |= KEYEVENTF_KEYUP
        inp = INPUT(type=INPUT_KEYBOARD)
        inp.u.ki = KEYBDINPUT(wVk=0, wScan=scan, dwFlags=flags, time=0, dwExtraInfo=0)
        if user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT)) != 1:
            log.warning("SendInput failed for %s (error %d)", name, ctypes.get_last_error())

    def down(self, name):
        name = name.lower()
        with self._lock:
            if name in self.held:
                return
            self._send(name, False)
            self.held.add(name)
            if self._repeater is None and not self.dry_run:
                self._repeater = threading.Thread(target=self._repeat, daemon=True)
                self._repeater.start()

    def _repeat(self):
        while True:
            time.sleep(REPEAT)
            with self._lock:
                if not self.held:
                    self._repeater = None
                    return
                for name in self.held:
                    self._send(name, False)

    def up(self, name):
        name = name.lower()
        with self._lock:
            if name in self.held:
                self._send(name, True)
                self.held.discard(name)

    def press(self, name, hold=0.05):
        name = name.lower()
        with self._lock:
            self.held.discard(name)  # no repeats while it's pressed, and it ends up released
            self._send(name, False)
        time.sleep(hold)
        self._send(name, True)

    def release_all(self):
        for name in list(self.held):
            self.up(name)
