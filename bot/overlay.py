"""Windows drawn over the game: a panel while Auto setup runs, and one status line while the bot runs.

They never take focus or catch clicks, so the game stays in front (and fullscreen) under them, and
they're left out of screen captures, so the bot never sees them in its screenshots.
"""
import ctypes
import tkinter as tk

import win32con
import win32gui

WS_EX_NOACTIVATE = 0x08000000
WDA_EXCLUDEFROMCAPTURE = 0x11  # Windows 10 2004 and later; older Windows shows it in captures too
BG, FG, DIM, GREEN, AMBER, RED = "#111827", "#f9fafb", "#9ca3af", "#4ade80", "#fbbf24", "#f87171"
WIDTH = 520   # px of text per line
SHOWN = 6     # result lines kept while it runs


def float_over_game(win):
    """Shows a withdrawn, borderless Toplevel on top without focus, ignoring clicks and left out of captures.
    Returns its window handle."""
    win.update_idletasks()
    hwnd = int(win.wm_frame(), 16)
    style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
    win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE, style | win32con.WS_EX_LAYERED |
                           win32con.WS_EX_TRANSPARENT | win32con.WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE)
    ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE)
    # Shown by hand: Tk's deiconify would activate it and take focus from the game.
    win32gui.ShowWindow(hwnd, win32con.SW_SHOWNOACTIVATE)
    win.update_idletasks()
    return hwnd


class Overlay:
    def __init__(self, root):
        self.root = root
        self.win = None
        self.hwnd = None
        self.rect = None
        self.hide_job = None

    @property
    def shown(self):
        return self.win is not None

    def show(self, rect, title):
        """Opens the panel at the top centre of rect (x, y, w, h on screen), without taking focus."""
        self.hide()
        win = self.win = tk.Toplevel(self.root, bg=BG, padx=16, pady=12)
        win.withdraw()
        win.overrideredirect(True)
        win.attributes("-topmost", True, "-alpha", 0.92)
        self.title = tk.Label(win, text=title, bg=BG, fg=FG, font=("Segoe UI", 12, "bold"), anchor="w")
        self.title.pack(fill="x")
        self.status = tk.Label(win, bg=BG, fg=DIM, font=("Segoe UI", 10), anchor="w", justify="left",
                               wraplength=WIDTH)
        self.status.pack(fill="x", pady=(2, 0))
        self.body = tk.Frame(win, bg=BG)
        self.body.pack(fill="x")
        tk.Frame(win, bg=BG, width=WIDTH, height=0).pack()  # keeps the width steady as lines come and go
        self.lines = []
        self.rect = None
        self.place(rect)
        self.hwnd = float_over_game(win)

    def place(self, rect):
        """Moves the panel to the top centre of rect, if that changed (the game window moved)."""
        if self.win is None or rect == self.rect:
            return
        self.rect = rect
        x, y, w, h = rect
        self.win.geometry(f"+{x + (w - WIDTH - 32) // 2}+{y + h // 12}")

    def add(self, ok, text):
        """ok None: what it's doing now. True / False: a step that worked / something to fix."""
        if self.win is None:
            return
        if ok is None:
            self.status.config(text=text)
            return
        row = tk.Frame(self.body, bg=BG)
        row.pack(fill="x", pady=(4, 0))
        tk.Label(row, text="✓" if ok else "!", bg=BG, fg=GREEN if ok else AMBER, width=2,
                 font=("Segoe UI", 10, "bold"), anchor="nw").pack(side="left", anchor="n")
        tk.Label(row, text=text, bg=BG, fg=FG, font=("Segoe UI", 10), anchor="w", justify="left",
                 wraplength=WIDTH - 24).pack(side="left", fill="x")
        self.lines.append(row)
        while len(self.lines) > SHOWN:
            self.lines.pop(0).destroy()

    def finish(self, title, problems, footer, seconds):
        """Shows the outcome (and anything left to fix), then closes after `seconds`."""
        if self.win is None:
            return
        self.title.config(text=title)
        self.status.config(text=footer)
        if not footer:
            self.status.pack_forget()
        for row in self.lines:
            row.destroy()
        self.lines = []
        self.body.config(height=1)  # an emptied frame keeps its old size otherwise
        for text in problems:
            self.add(False, text)
        self.hide_job = self.root.after(int(seconds * 1000), self.hide)

    def hide(self):
        if self.hide_job:
            self.root.after_cancel(self.hide_job)
            self.hide_job = None
        if self.win is not None:
            self.win.destroy()
            self.win = self.hwnd = None


class StatusLine:
    """One thin line at the top of the game saying what the bot is doing, shown while it runs."""

    WIDTH = 640

    def __init__(self, root):
        self.root = root
        self.win = None
        self.rect = None
        self.text = None

    def show(self, rect, text, colour=FG):
        if self.win is None:
            self.win = tk.Toplevel(self.root, bg=BG)
            self.win.withdraw()
            self.win.overrideredirect(True)
            self.win.attributes("-topmost", True, "-alpha", 0.85)
            self.label = tk.Label(self.win, bg=BG, fg=colour, font=("Segoe UI", 10), anchor="w", padx=10, pady=2)
            self.label.pack(fill="both", expand=True)
            self.rect = None
            self._place(rect)
            float_over_game(self.win)
        self._place(rect)
        if (text, colour) != self.text:
            self.text = (text, colour)
            self.label.config(text=text, fg=colour)

    def _place(self, rect):
        if rect != self.rect:
            self.rect = rect
            x, y, w, _ = rect
            width = min(self.WIDTH, w - 20)
            self.win.geometry(f"{width}x24+{x + (w - width) // 2}+{y + 4}")

    def hide(self):
        if self.win is not None:
            self.win.destroy()
            self.win, self.text = None, None
