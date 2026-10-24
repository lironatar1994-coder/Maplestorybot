"""Control panel for the bot.

  python gui.py
"""
import base64
import logging
import os
import queue
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from tkinter.scrolledtext import ScrolledText

import cv2
import keyboard
import win32gui

from bot import capture, config
from bot import keys as keymod
from bot.autosetup import AutoSetup
from bot.engine import Engine
from bot.vision import Vision, crop

HERE = Path(__file__).resolve().parent
KEY_NAMES = sorted(set(keymod.SCAN) | set(keymod.EXTENDED), key=lambda k: (len(k) > 1, k))
GREY, GREEN, AMBER, RED, BLUE = "#6b7280", "#15803d", "#b45309", "#b91c1c", "#1d4ed8"
PHASES = {
    "stopped": ("Stopped", GREY),
    "paused": ("Paused", AMBER),
    "running": ("Running", GREEN),
    "unfocused": ("Waiting: click into the game window", AMBER),
    "no_window": ("Waiting: game window not found", RED),
}
PREVIEW_WIDTH = 340

# (dotted config path, label, type)
GAME_FIELDS = [("window_title", "Window title", str), ("route", "Route file", "route"),
               ("loop_hz", "Loop rate (per second)", int)]
KEY_FIELDS = [(f"keys.{name}", label, "key") for name, label in (
    ("left", "Left"), ("right", "Right"), ("up", "Up / climb"), ("down", "Down"), ("jump", "Jump"),
    ("attack", "Attack"), ("hp_potion", "HP potion"))] + [("keys.mp_potion", "MP potion (blank = none)", "key?")]
POTION_FIELDS = [("healer.hp_threshold", "Drink HP below (%)", "pct"),
                 ("healer.mp_threshold", "Drink MP below (%)", "pct"),
                 ("healer.cooldown", "Potion cooldown (s)", float)]
MOVE_FIELDS = [("movement.tolerance_x", "Arrive within x (minimap px)", int),
               ("movement.tolerance_y", "Arrive within y (minimap px)", int),
               ("movement.jump_interval", "Seconds between jumps", float)]
SAFETY_FIELDS = [("safety.pause_on_other_player", "Pause when another player shows up", bool),
                 ("safety.lost_player_seconds", "Pause if my dot is lost for (s)", float),
                 ("safety.stuck_seconds", "Pause if stuck for (s)", float),
                 ("safety.template_threshold", "Popup match threshold (0-1)", float)]
MODE_FIELDS = [("mode", "Mode (route = walk it, stand = attack in place)", ("choice", ["route", "stand"])),
               ("stand.face", "Stand: face this way first", ("choice", ["", "left", "right"])),
               ("stand.jump_every", "Stand: jump every (s, 0 = never)", float)]
ALL_FIELDS = GAME_FIELDS + MODE_FIELDS + KEY_FIELDS + POTION_FIELDS + MOVE_FIELDS + SAFETY_FIELDS


def get_path(data, dotted):
    for part in dotted.split("."):
        data = data.get(part) if isinstance(data, dict) else None
    return data


def set_path(data, dotted, value):
    *parents, last = dotted.split(".")
    for part in parents:
        data = data.setdefault(part, {})
    data[last] = value


def to_photo(img):
    ok, buf = cv2.imencode(".png", img)
    return tk.PhotoImage(data=base64.b64encode(buf.tobytes()).decode("ascii"))


class QueueLogHandler(logging.Handler):
    def __init__(self, q):
        super().__init__()
        self.q = q

    def emit(self, record):
        self.q.put(self.format(record))


class App:
    def __init__(self, root):
        self.root = root
        self.cfg = config.load()
        self.engine = None
        self.thread = None
        self.commands = queue.Queue()  # hotkey thread -> Tk thread
        self.log_queue = queue.Queue()
        self.procs = []
        self.preview_game = capture.game_window(self.cfg)
        self.preview_vision = Vision(self.cfg)
        self.last_idle_grab = 0.0
        self.pending_cfg = None  # settings saved while running, applied once the bot stops
        self.setup_thread = None
        self.setup_cancel = threading.Event()
        self.setup_results = None  # set by the Auto setup thread when it finishes

        handler = QueueLogHandler(self.log_queue)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%H:%M:%S"))
        logging.getLogger().addHandler(handler)
        logging.getLogger().setLevel(logging.INFO)

        root.title("Maple training bot")
        root.geometry("860x720")
        root.minsize(760, 600)
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.tabs = ttk.Notebook(root)
        self.tabs.pack(fill="both", expand=True, padx=8, pady=8)
        self.build_run_tab()
        self.build_settings_tab()
        self.build_calibrate_tab()

        hk = self.cfg["hotkeys"]
        keyboard.add_hotkey(hk["toggle"], lambda: self.commands.put(self.toggle))
        keyboard.add_hotkey(hk["quit"], lambda: self.commands.put(self.stop))
        logging.getLogger("bot").info("Control panel ready. %s start/pause, %s stop.",
                                      hk["toggle"].upper(), hk["quit"].upper())
        self.poll()

    # ---------------------------------------------------------------- Run tab
    def build_run_tab(self):
        tab = ttk.Frame(self.tabs, padding=8)
        self.tabs.add(tab, text="Run")
        hk = self.cfg["hotkeys"]

        bar = ttk.Frame(tab)
        bar.pack(fill="x")
        self.start_btn = ttk.Button(bar, text=f"Start ({hk['toggle'].upper()})", command=self.start)
        self.pause_btn = ttk.Button(bar, text=f"Pause ({hk['toggle'].upper()})", command=self.pause)
        self.stop_btn = ttk.Button(bar, text=f"Stop ({hk['quit'].upper()})", command=self.stop)
        self.auto_btn = ttk.Button(bar, text="Auto setup", command=self.auto_setup)
        for btn in (self.start_btn, self.pause_btn, self.stop_btn, self.auto_btn):
            btn.pack(side="left", padx=(0, 6))
        self.dry_run = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar, text="Dry run (log keys, don't press them)", variable=self.dry_run).pack(side="left", padx=12)

        self.banner = tk.Label(tab, text="Stopped", fg="white", bg=GREY, anchor="w", padx=10, pady=6,
                               font=("Segoe UI", 11, "bold"))
        self.banner.pack(fill="x", pady=8)

        mid = ttk.Frame(tab)
        mid.pack(fill="x")
        preview_box = ttk.LabelFrame(mid, text="Minimap", padding=6)
        preview_box.pack(side="left", fill="y")
        self.preview = ttk.Label(preview_box, text="", anchor="center", justify="center", width=44)
        self.preview.pack()

        info = ttk.LabelFrame(mid, text="Status", padding=8)
        info.pack(side="left", fill="both", expand=True, padx=(8, 0))
        self.info = {}
        for row, (key, label) in enumerate((("pos", "Position"), ("waypoint", "Waypoint"),
                                            ("others", "Other players"), ("route", "Route"))):
            ttk.Label(info, text=label + ":").grid(row=row, column=0, sticky="w", pady=2)
            self.info[key] = ttk.Label(info, text="-")
            self.info[key].grid(row=row, column=1, columnspan=2, sticky="w", padx=6)
        self.bars = {}
        for i, name in enumerate(("HP", "MP")):
            row = 4 + i
            ttk.Label(info, text=name + ":").grid(row=row, column=0, sticky="w", pady=2)
            bar_widget = ttk.Progressbar(info, length=160, maximum=100)
            bar_widget.grid(row=row, column=1, sticky="w", padx=6)
            text = ttk.Label(info, text="n/a", width=6)
            text.grid(row=row, column=2, sticky="w")
            self.bars[name] = (bar_widget, text)
        ttk.Label(info, text="The game must be in front and uncovered while the bot runs.",
                  foreground=GREY, wraplength=320).grid(row=6, column=0, columnspan=3, sticky="w", pady=(10, 0))

        log_box = ttk.LabelFrame(tab, text="Log", padding=4)
        log_box.pack(fill="both", expand=True, pady=(8, 0))
        self.log_text = ScrolledText(log_box, height=12, state="disabled", font=("Consolas", 9), wrap="word")
        self.log_text.pack(fill="both", expand=True)

    def alive(self):
        return self.thread is not None and self.thread.is_alive()

    def setting_up(self):
        return self.setup_thread is not None and self.setup_thread.is_alive()

    def start(self):
        if self.setting_up():
            logging.getLogger("bot").info("Auto setup is still running; Start is available when it finishes.")
            return
        if self.alive():
            self.engine.resume()
            return
        try:
            cfg = config.load()
        except Exception as e:  # bad key name, broken YAML...
            messagebox.showerror("Can't start", str(e))
            return
        if cfg.get("mode") != "stand" and not cfg["regions"].get("minimap"):
            messagebox.showwarning("Calibrate first", "No minimap region yet. Use Select regions in the Calibrate "
                                   "tab, or switch Training mode to stand in Settings.")
            return
        self.cfg = cfg
        self.engine = Engine(cfg, dry_run=self.dry_run.get())
        self.engine.resume()
        self.thread = threading.Thread(target=self.engine.run, daemon=True)
        self.thread.start()

    def pause(self):
        if self.alive():
            self.engine.pause()

    def toggle(self):
        if self.alive() and self.engine.running.is_set():
            self.pause()
        else:
            self.start()

    def stop(self):
        if self.alive():
            self.engine.stop()
        if self.setting_up():
            self.setup_cancel.set()

    def auto_setup(self):
        if self.alive() or self.setting_up():
            return
        try:
            cfg = config.load()
        except Exception as e:
            messagebox.showerror("Can't run Auto setup", str(e))
            return
        self.setup_cancel = threading.Event()
        setup = AutoSetup(cfg, cancel=self.setup_cancel, launch=self.open_game.get())
        self.setup_results = None

        def work():
            self.setup_results = setup.run()

        logging.getLogger("bot").info("Auto setup started. Keep the game in front; Stop (F10) cancels.")
        self.setup_thread = threading.Thread(target=work, daemon=True)
        self.setup_thread.start()
        self.tabs.select(0)

    def finish_auto_setup(self):
        results, self.setup_results = self.setup_results, None
        self.reload_config()
        self.load_settings_into_form()
        problems = [msg for ok, msg in results or [] if not ok]
        if problems:
            messagebox.showwarning("Auto setup: things to fix", "\n\n".join(problems))
        else:
            messagebox.showinfo("Auto setup", "Everything checked out. Press Start when you're ready.")

    def show_status(self, st):
        phase = st["phase"]
        text, colour = PHASES.get(phase, (phase, GREY))
        if phase == "paused" and st.get("reason"):
            text, colour = f"PAUSED: {st['reason']}. Handle it, then press Start.", RED
        if self.alive() and self.engine.dry_run:
            text += "   [dry run]"
        self.banner.config(text=text, bg=colour)

        pos = st.get("pos")
        self.info["pos"].config(text=f"{pos[0]:.0f}, {pos[1]:.0f}" if pos else "not found")
        count = st.get("count", 0)
        if self.cfg.get("mode") == "stand":
            jump = (self.cfg.get("stand") or {}).get("jump_every") or 0
            self.info["waypoint"].config(text="stand mode: attacking in place" +
                                         (f", jumping every {jump:g}s" if jump else ""))
        else:
            self.info["waypoint"].config(text=f"{st['index'] + 1} of {count} ({st['routine']})"
                                         if count and self.alive()
                                         else (f"{count} waypoints" if count else "no route recorded"))
        self.info["others"].config(text=str(len(st.get("others") or [])))
        self.info["route"].config(text=self.cfg["route"])
        for name in ("HP", "MP"):
            value = st.get(name.lower())
            widget, label = self.bars[name]
            widget["value"] = 0 if value is None else value * 100
            label.config(text="n/a" if value is None else f"{value:.0%}")

        img = st.get("minimap")
        if img is None or not img.size:
            self.preview.config(image="", text=st.get("preview_msg", "No minimap yet."))
            self.preview.image = None
            return
        photo = to_photo(self.render_minimap(img, st))
        self.preview.config(image=photo, text="")
        self.preview.image = photo

    def render_minimap(self, img, st):
        zoom = max(1, min(4, PREVIEW_WIDTH // max(1, img.shape[1])))
        view = cv2.resize(img, None, fx=zoom, fy=zoom, interpolation=cv2.INTER_NEAREST)
        waypoints = (self.cfg.get("route_data") or {}).get("waypoints") or []
        target = st.get("index") if self.alive() else None
        for i, wp in enumerate(waypoints):
            centre = (int(wp["x"] * zoom), int(wp["y"] * zoom))
            colour = (80, 200, 80) if wp.get("action") == "attack" else (200, 200, 200)
            cv2.circle(view, centre, 2 + zoom // 2, colour, -1)
            if i == target:
                cv2.circle(view, centre, 4 + zoom * 2, (255, 255, 255), 1)
        if st.get("pos"):
            cv2.circle(view, (int(st["pos"][0] * zoom), int(st["pos"][1] * zoom)), 3 + zoom * 2, (0, 255, 255), 2)
        for ox, oy, _ in st.get("others") or []:
            cv2.circle(view, (int(ox * zoom), int(oy * zoom)), 3 + zoom * 2, (0, 0, 255), 2)
        return view

    def idle_status(self):
        """What the bot would see right now, while it isn't running."""
        st = {"phase": "stopped", "reason": None, "pos": None, "others": [], "hp": None, "mp": None, "index": 0,
              "count": len((self.cfg.get("route_data") or {}).get("waypoints") or []), "routine": "", "minimap": None}
        region = self.cfg["regions"].get("minimap")
        if not region:
            st["preview_msg"] = "No minimap region yet.\nUse Select regions in the Calibrate tab."
            return st
        frame = self.preview_game.grab()
        if frame is None:
            st["preview_msg"] = f"Game window '{self.cfg['window_title']}' not found."
            return st
        state = self.preview_vision.read(frame)
        st.update(pos=state.pos, others=state.others, hp=state.hp, mp=state.mp, minimap=crop(frame, region).copy())
        return st

    # ----------------------------------------------------------- Settings tab
    def build_settings_tab(self):
        tab = ttk.Frame(self.tabs, padding=8)
        self.tabs.add(tab, text="Settings")
        self.vars = {}
        left, right = ttk.Frame(tab), ttk.Frame(tab)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        right.grid(row=0, column=1, sticky="nsew")
        tab.columnconfigure(0, weight=1)
        tab.columnconfigure(1, weight=1)

        self.field_group(left, "Game", GAME_FIELDS)
        self.field_group(left, "Training mode", MODE_FIELDS)
        self.field_group(left, "Keys (as bound in the game)", KEY_FIELDS)
        self.field_group(right, "Potions", POTION_FIELDS)
        self.build_buffs(right)
        self.field_group(right, "Movement", MOVE_FIELDS)
        self.field_group(right, "Safety", SAFETY_FIELDS)

        bottom = ttk.Frame(tab)
        bottom.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        ttk.Button(bottom, text="Save settings", command=self.save_settings).pack(side="left")
        ttk.Button(bottom, text="Revert", command=self.load_settings_into_form).pack(side="left", padx=6)
        ttk.Label(bottom, text="Saved to settings.yaml. Changes apply the next time you press Start.",
                  foreground=GREY).pack(side="left", padx=10)
        self.load_settings_into_form()

    def field_group(self, parent, title, fields):
        box = ttk.LabelFrame(parent, text=title, padding=8)
        box.pack(fill="x", pady=(0, 8))
        for row, (path, label, kind) in enumerate(fields):
            if kind is bool:
                var = tk.BooleanVar()
                ttk.Checkbutton(box, text=label, variable=var).grid(row=row, column=0, columnspan=2, sticky="w", pady=1)
            else:
                var = tk.StringVar()
                ttk.Label(box, text=label).grid(row=row, column=0, sticky="w", pady=1)
                if isinstance(kind, tuple):
                    widget = ttk.Combobox(box, textvariable=var, width=10, values=kind[1], state="readonly")
                elif kind in ("key", "key?"):
                    widget = ttk.Combobox(box, textvariable=var, width=12,
                                          values=([""] if kind == "key?" else []) + KEY_NAMES)
                elif kind == "route":
                    widget = ttk.Combobox(box, textvariable=var, width=24, postcommand=lambda: self.refresh_routes())
                    self.route_box = widget
                else:
                    widget = ttk.Entry(box, textvariable=var, width=26 if kind is str else 10)
                widget.grid(row=row, column=1, sticky="w", padx=6, pady=1)
            self.vars[path] = (var, kind)
        box.columnconfigure(1, weight=1)

    def refresh_routes(self):
        routes = sorted(p.as_posix() for p in Path("routes").glob("*.yaml"))
        self.route_box["values"] = routes

    def build_buffs(self, parent):
        box = ttk.LabelFrame(parent, text="Buffs (recast on a timer)", padding=8)
        box.pack(fill="x", pady=(0, 8))
        header = ttk.Frame(box)
        header.pack(fill="x")
        for col, (text, width) in enumerate((("Key", 10), ("Every (s)", 9), ("Cast time (s)", 11))):
            ttk.Label(header, text=text, width=width).grid(row=0, column=col, sticky="w", padx=(0, 4))
        self.buff_frame = ttk.Frame(box)
        self.buff_frame.pack(fill="x")
        ttk.Button(box, text="+ Add buff", command=lambda: self.add_buff_row({})).pack(anchor="w", pady=(4, 0))
        self.buff_rows = []

    def add_buff_row(self, buff):
        row = ttk.Frame(self.buff_frame)
        row.pack(fill="x", pady=1)
        key = tk.StringVar(value=str(buff.get("key", "")))
        interval = tk.StringVar(value=str(buff.get("interval", 180)))
        cast = tk.StringVar(value=str(buff.get("cast_time", 1.0)))
        ttk.Combobox(row, textvariable=key, values=KEY_NAMES, width=8).grid(row=0, column=0, padx=(0, 4))
        ttk.Entry(row, textvariable=interval, width=9).grid(row=0, column=1, padx=(0, 4))
        ttk.Entry(row, textvariable=cast, width=11).grid(row=0, column=2, padx=(0, 4))
        entry = (row, key, interval, cast)
        ttk.Button(row, text="Remove", width=8, command=lambda: self.remove_buff_row(entry)).grid(row=0, column=3)
        self.buff_rows.append(entry)

    def remove_buff_row(self, entry):
        entry[0].destroy()
        self.buff_rows.remove(entry)

    def load_settings_into_form(self):
        cfg = config.load()
        for path, (var, kind) in self.vars.items():
            value = get_path(cfg, path)
            if kind is bool:
                var.set(bool(value))
            elif kind == "pct":
                var.set(f"{value * 100:g}")
            else:
                var.set("" if value is None else str(value))
        for entry in list(self.buff_rows):
            self.remove_buff_row(entry)
        for buff in cfg.get("buffs") or []:
            self.add_buff_row(buff)

    def read_form(self):
        """Settings dict from the form; raises ValueError naming the bad field."""
        out = {}
        labels = {path: label for path, label, _ in ALL_FIELDS}
        for path, (var, kind) in self.vars.items():
            raw = var.get()
            try:
                if kind is bool:
                    value = bool(raw)
                elif isinstance(kind, tuple):
                    if raw not in kind[1]:
                        raise ValueError(f"pick one of {', '.join(v or '(none)' for v in kind[1])}")
                    value = raw or None
                elif kind in ("key", "key?"):
                    raw = raw.strip().lower()
                    if not raw and kind == "key?":
                        value = None
                    else:
                        keymod.validate(raw)
                        value = raw
                elif kind == "pct":
                    value = float(raw) / 100
                    if not 0 <= value <= 1:
                        raise ValueError("must be 0-100")
                elif kind in (str, "route"):
                    value = raw.strip()
                    if not value:
                        raise ValueError("can't be empty")
                else:
                    value = kind(raw)
                    if value < 0:
                        raise ValueError("can't be negative")
            except ValueError as e:
                raise ValueError(f"{labels[path]}: {e}") from None
            set_path(out, path, value)
        buffs = []
        for i, (_, key, interval, cast) in enumerate(self.buff_rows, 1):
            try:
                name = key.get().strip().lower()
                keymod.validate(name)
                buffs.append({"key": name, "interval": float(interval.get()), "cast_time": float(cast.get())})
            except ValueError as e:
                raise ValueError(f"Buff {i}: {e}") from None
        out["buffs"] = buffs
        return out

    def save_settings(self):
        try:
            settings = self.read_form()
        except ValueError as e:
            messagebox.showerror("Check this setting", str(e))
            return
        current = config.read_yaml(config.SETTINGS_FILE, {}) or {}
        config.save_yaml(config.SETTINGS_FILE, config.deep_merge(current, settings))
        self.reload_config()
        note = " Stop and start the bot to use them." if self.alive() else ""
        logging.getLogger("bot").info("Settings saved.%s", note)

    def reload_config(self):
        try:
            cfg = config.load()
        except Exception as e:
            logging.getLogger("bot").error("Config error: %s", e)
            return
        if self.alive():
            self.pending_cfg = cfg
            return
        self.cfg = cfg
        self.preview_game = capture.game_window(cfg)
        self.preview_vision = Vision(cfg)

    # ---------------------------------------------------------- Calibrate tab
    def build_calibrate_tab(self):
        tab = ttk.Frame(self.tabs, padding=12)
        self.tabs.add(tab, text="Calibrate")
        auto = ttk.LabelFrame(tab, text="Automatic", padding=10)
        auto.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 12))
        self.auto_btn2 = ttk.Button(auto, text="Auto setup", command=self.auto_setup, width=26)
        self.auto_btn2.grid(row=0, column=0, sticky="w")
        ttk.Label(auto, wraplength=480, text=(
            "Opens the game if needed and waits for you to log in. Then it finds the minimap and HP/MP bars, "
            "works out which dot is you (it walks a step left and right), tests jump, reads your attack and "
            "potion keys off the quickslot bar, and saves it all. Have HP and MP full and the minimap open.")
        ).grid(row=0, column=1, sticky="w", padx=10)
        self.open_game = tk.BooleanVar(value=True)
        ttk.Checkbutton(auto, text="Open the game if it isn't running", variable=self.open_game).grid(
            row=1, column=1, sticky="w", padx=10, pady=(6, 0))
        auto.columnconfigure(1, weight=1)

        manual = ttk.Frame(tab)
        manual.grid(row=1, column=0, columnspan=2, sticky="ew")
        ttk.Label(manual, text="Or by hand (each step opens its own window with instructions):",
                  foreground=GREY).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 6))
        tab = manual
        steps = [
            ("1. Find game window", "Lists open windows in the log and shows which one the bot will use.",
             self.find_window),
            ("2. Key test", "Click into the game within 5 seconds: your character should jump 3 times.",
             lambda: self.run_calibrate("keytest")),
            ("3. Select regions", "Drag boxes around the minimap map area, the HP bar and the MP bar.",
             lambda: self.run_calibrate("regions")),
            ("4. Live check / colours", "Zoomed minimap with detections. Hover a dot to read its HSV colour.",
             lambda: self.run_calibrate("check")),
            ("5. Record route...", "Walk the route and mark points with F5/F6 (attack), F7 (pass), F8 (undo), "
                                   "F10 (save).", self.record_route),
            ("Save popup template...", "With a popup on screen (lie detector, death...), box part of it. "
                                       "The bot pauses whenever it appears.", self.save_template),
            ("Open overlay image", "debug/overlay.png from the last regions or check run.", self.open_overlay),
        ]
        self.calib_buttons = []
        for row, (text, desc, cmd) in enumerate(steps, start=1):
            btn = ttk.Button(tab, text=text, command=cmd, width=26)
            btn.grid(row=row, column=0, sticky="w", pady=4)
            ttk.Label(tab, text=desc, wraplength=480).grid(row=row, column=1, sticky="w", padx=10)
            self.calib_buttons.append(btn)

        tpl_box = ttk.LabelFrame(tab, text="Popup templates", padding=8)
        tpl_box.grid(row=len(steps) + 1, column=0, columnspan=2, sticky="ew", pady=(14, 0))
        self.tpl_list = tk.Listbox(tpl_box, height=5)
        self.tpl_list.pack(side="left", fill="x", expand=True)
        ttk.Button(tpl_box, text="Delete selected", command=self.delete_template).pack(side="left", padx=8, anchor="n")
        ttk.Label(tab, text="Calibration is disabled while the bot is running.", foreground=GREY).grid(
            row=len(steps) + 2, column=0, columnspan=2, sticky="w", pady=(8, 0))
        tab.columnconfigure(1, weight=1)
        tab.master.columnconfigure(1, weight=1)
        self.refresh_templates()

    def run_calibrate(self, *args):
        exe = Path(sys.executable)
        if exe.name.lower() == "pythonw.exe":  # need a console for the prompts
            exe = exe.with_name("python.exe")
        proc = subprocess.Popen([str(exe), str(HERE / "calibrate.py"), *args, "--pause"], cwd=HERE,
                                creationflags=subprocess.CREATE_NEW_CONSOLE)
        self.procs.append(proc)

    def find_window(self):
        log = logging.getLogger("bot")
        for hwnd, title in capture.list_windows():
            log.info("window %s: %s", hwnd, title)
        hwnd = capture.find_window(self.cfg["window_title"], self.cfg.get("window_process"))
        if hwnd:
            log.info("The bot will use: %r", win32gui.GetWindowText(hwnd))
        else:
            log.warning("No window matches %r. Set Window title in Settings.", self.cfg["window_title"])
        self.tabs.select(0)

    def record_route(self):
        Path("routes").mkdir(exist_ok=True)
        current = Path(self.cfg["route"])
        path = filedialog.asksaveasfilename(title="Route file", initialdir=HERE / "routes", initialfile=current.name,
                                            defaultextension=".yaml", filetypes=[("Route", "*.yaml")],
                                            confirmoverwrite=False)
        if not path:
            return
        path = Path(path)
        try:
            path = path.relative_to(HERE)
        except ValueError:
            pass
        route = path.as_posix()
        current_settings = config.read_yaml(config.SETTINGS_FILE, {}) or {}
        current_settings["route"] = route
        config.save_yaml(config.SETTINGS_FILE, current_settings)
        self.vars["route"][0].set(route)
        logging.getLogger("bot").info("Route set to %s.", route)
        self.run_calibrate("route", route)

    def save_template(self):
        name = simpledialog.askstring("Popup template", "Name (letters, digits, - or _), e.g. lie_detector:",
                                      parent=self.root)
        if not name:
            return
        if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
            messagebox.showerror("Bad name", "Use only letters, digits, - and _.")
            return
        self.run_calibrate("template", name)

    def refresh_templates(self):
        names = sorted(p.name for p in Path(self.cfg["safety"]["template_dir"]).glob("*.png"))
        if list(self.tpl_list.get(0, "end")) != names:
            self.tpl_list.delete(0, "end")
            for name in names:
                self.tpl_list.insert("end", name)

    def delete_template(self):
        sel = self.tpl_list.curselection()
        if not sel:
            return
        name = self.tpl_list.get(sel[0])
        if messagebox.askyesno("Delete template", f"Delete {name}? The bot will stop pausing for this popup."):
            (Path(self.cfg["safety"]["template_dir"]) / name).unlink(missing_ok=True)
            self.refresh_templates()

    def open_overlay(self):
        path = HERE / "debug" / "overlay.png"
        if path.exists():
            os.startfile(path)
        else:
            messagebox.showinfo("No overlay yet", "Run Select regions first, or press S in Live check.")

    # ------------------------------------------------------------- main loop
    def poll(self):
        try:
            while True:
                self.commands.get_nowait()()
        except queue.Empty:
            pass

        lines = []
        try:
            while len(lines) < 200:
                lines.append(self.log_queue.get_nowait())
        except queue.Empty:
            pass
        if lines:
            self.log_text.config(state="normal")
            self.log_text.insert("end", "\n".join(lines) + "\n")
            if int(self.log_text.index("end-1c").split(".")[0]) > 1500:
                self.log_text.delete("1.0", "500.0")
            self.log_text.see("end")
            self.log_text.config(state="disabled")

        alive, setting_up = self.alive(), self.setting_up()
        if not alive and self.pending_cfg:
            self.pending_cfg = None
            self.reload_config()
        if not setting_up and self.setup_results is not None:
            self.finish_auto_setup()
        if alive:
            self.show_status(self.engine.status())
        elif self.tabs.index("current") == 0 and time.monotonic() - self.last_idle_grab > 0.25:
            self.last_idle_grab = time.monotonic()
            self.show_status(self.idle_status())
        if setting_up:
            self.banner.config(text="Auto setup running... keep the game in front (Stop / F10 cancels)", bg=BLUE)

        running = alive and self.engine.running.is_set()
        busy = alive or setting_up
        self.start_btn.state(["disabled"] if running or setting_up else ["!disabled"])
        self.pause_btn.state(["!disabled"] if running else ["disabled"])
        self.stop_btn.state(["!disabled"] if busy else ["disabled"])
        for btn in self.calib_buttons + [self.auto_btn, self.auto_btn2]:
            btn.state(["disabled"] if busy else ["!disabled"])

        finished = [p for p in self.procs if p.poll() is not None]
        if finished:
            self.procs = [p for p in self.procs if p.poll() is None]
            self.reload_config()
            self.refresh_templates()
        self.root.after(100, self.poll)

    def on_close(self):
        if self.alive():
            self.engine.stop()
            self.thread.join(timeout=2)
        if self.setting_up():
            self.setup_cancel.set()
            self.setup_thread.join(timeout=2)
        keyboard.unhook_all()
        self.root.destroy()


def main():
    os.chdir(HERE)
    capture.set_dpi_aware()
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
