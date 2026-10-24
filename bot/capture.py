"""Finds the game window and captures its client area."""
import ctypes
import os

import mss
import numpy as np
import win32gui
import win32process


def set_dpi_aware():
    """Make window coordinates match real screen pixels on scaled displays."""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass


def list_windows():
    titles = []
    own_pid = os.getpid()

    def cb(hwnd, _):
        # Skip our own windows: reading their title would block until the GUI thread answers.
        if win32process.GetWindowThreadProcessId(hwnd)[1] == own_pid:
            return
        title = win32gui.GetWindowText(hwnd)
        if win32gui.IsWindowVisible(hwnd) and title:
            titles.append((hwnd, title))

    win32gui.EnumWindows(cb, None)
    return titles


def process_name(hwnd):
    """Executable file name of the window's process (e.g. 'MapleStory.exe'), or None if Windows won't say."""
    pid = win32process.GetWindowThreadProcessId(hwnd)[1]
    handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return None
    try:
        buf, size = ctypes.create_unicode_buffer(1024), ctypes.c_uint32(1024)
        if not ctypes.windll.kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return None
        return os.path.basename(buf.value)
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def find_window(title_part, process=None):
    """Exact (case-insensitive) title match first, then the first title containing title_part.

    With process (an exe name), windows of other programs are skipped, so the launcher or a folder
    with a similar name never counts as the game.
    """
    title_part = title_part.lower()
    windows = list_windows()
    if process:
        windows = [(h, t) for h, t in windows if (process_name(h) or process).lower() == process.lower()]
    for hwnd, title in windows:
        if title.lower() == title_part:
            return hwnd
    for hwnd, title in windows:
        if title_part in title.lower():
            return hwnd
    return None


def game_window(cfg):
    return GameWindow(cfg["window_title"], cfg.get("window_process"))


class GameWindow:
    def __init__(self, title_part, process=None):
        self.title_part = title_part
        self.process = process
        self.hwnd = None
        self._sct = getattr(mss, "MSS", mss.mss)()  # mss 10 renamed mss.mss to mss.MSS

    def locate(self):
        self.hwnd = find_window(self.title_part, self.process)
        return self.hwnd is not None

    def bring_to_front(self):
        """Ask Windows to focus the game. Works when our own window has focus (e.g. a button was just clicked)."""
        if not self.hwnd:
            return False
        try:
            if win32gui.IsIconic(self.hwnd):
                win32gui.ShowWindow(self.hwnd, 9)  # SW_RESTORE
            win32gui.SetForegroundWindow(self.hwnd)
        except win32gui.error:
            return False
        return self.is_foreground()

    @property
    def title(self):
        return win32gui.GetWindowText(self.hwnd) if self.hwnd else None

    def client_rect(self):
        left, top, right, bottom = win32gui.GetClientRect(self.hwnd)
        x, y = win32gui.ClientToScreen(self.hwnd, (0, 0))
        return x, y, right - left, bottom - top

    def is_foreground(self):
        return self.hwnd is not None and win32gui.GetForegroundWindow() == self.hwnd

    def grab(self):
        """Returns the client area as a BGR image, or None if the window is missing or minimized."""
        if not self.hwnd or not win32gui.IsWindow(self.hwnd):
            if not self.locate():
                return None
        x, y, w, h = self.client_rect()
        if w <= 0 or h <= 0:
            return None
        shot = self._sct.grab({"left": x, "top": y, "width": w, "height": h})
        return np.ascontiguousarray(np.asarray(shot)[:, :, :3])
