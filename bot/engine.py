"""The bot loop, runnable from the command line or from the control panel thread."""
import logging
import threading
import time

from bot import capture, detect
from bot.buffs import Buffs
from bot.healer import Healer
from bot.keys import Keys
from bot.pathing import Layout, layout_path
from bot.routine import Routine
from bot.safety import Safety
from bot.vision import TemplateWatcher, Vision, crop

log = logging.getLogger("bot")


class Engine:
    def __init__(self, cfg, dry_run=False):
        self.cfg = cfg
        self.dry_run = dry_run
        self.keys = Keys(dry_run=dry_run)
        self.vision = Vision(cfg)
        self.healer = Healer(cfg, self.keys)
        self.buffs = Buffs(cfg.get("buffs"), self.keys, cfg["buff_overdue_grace"])
        pathing = cfg.get("pathing") or {}
        route_mode = cfg.get("mode", "route") != "stand"
        use_paths = route_mode and bool(pathing.get("enabled"))
        self.learn = route_mode and bool(pathing.get("learn"))
        self.layout_file = layout_path(cfg["route"])
        self.layout = Layout.load(self.layout_file) if use_paths or self.learn else None
        self.routine = Routine(cfg, self.keys, self.layout if use_paths else None)
        self.learning = False
        self._lost_since = None
        self._last_save = 0.0
        self.watcher = TemplateWatcher(cfg["safety"]["template_dir"], cfg["safety"]["template_threshold"])
        self.safety = Safety(cfg, self.watcher)
        self.running = threading.Event()
        self.stopping = threading.Event()
        self._lock = threading.Lock()
        self._status = {"phase": "paused", "reason": None, "pos": None, "others": [], "hp": None, "mp": None,
                        "index": 0, "count": len(self.routine.waypoints), "routine": "moving", "minimap": None,
                        "lap": 0, "path": []}

    # Control (safe to call from any thread)
    def resume(self):
        self.running.set()
        log.info("Running.")

    def pause(self):
        if self.running.is_set():
            self.running.clear()
            log.info("Paused.")

    def toggle(self):
        self.pause() if self.running.is_set() else self.resume()

    def stop(self):
        self.running.clear()
        self.stopping.set()

    def status(self):
        with self._lock:
            return dict(self._status)

    def _publish(self, **values):
        with self._lock:
            self._status.update(values)

    def _follow_minimap(self, frame):
        """Re-finds the minimap on (re)start, in case its window was moved since calibration."""
        found = detect.find_minimap(frame)
        regions = self.cfg["regions"]  # shared with Vision, so updating it in place is enough
        old = regions.get("minimap")
        if not found or found == old:
            return
        if old and found[2:] != old[2:]:
            log.warning("The minimap changed size (zoomed with +/-?). Route points may be off: undo the zoom "
                        "or record the route again.")
        elif old:
            log.info("The minimap moved; following it.")
        regions["minimap"] = found
        self.safety.has_minimap = True

    def _check_layout(self):
        """Drops the learned map if it was made with the minimap zoomed differently (every position would be off)."""
        minimap = self.cfg["regions"].get("minimap")
        if self.layout is None or not minimap:
            return
        size = [int(v) for v in minimap[2:]]
        if self.layout.size is None:
            self.layout.size = size
        elif list(self.layout.size) != size:
            log.warning("%s was learned with a %dx%d minimap, but it's %dx%d now: not using it. Undo the zoom, "
                        "or delete that file to learn the map again.", self.layout_file, *self.layout.size, *size)
            self.layout = self.routine.layout = None
            self.learning = False

    def _learn(self, pos, now):
        """Feeds the learned map. Stops at the first sign of a map change, until the next Start."""
        if pos:
            self._lost_since = None
            self.layout.observe(pos, now)
            if self.layout.dirty and now - self._last_save >= 30:
                self._save_layout(now)
            return
        self.layout.break_trace()
        if self._lost_since is None:
            self._lost_since = now
        elif now - self._lost_since >= 1.0:
            log.info("Lost the dot: not learning the map again until the next Start, in case this is another map.")
            self.learning = False

    def _save_layout(self, now):
        try:
            self.layout.save(self.layout_file)
        except OSError as e:
            log.warning("Couldn't save the learned map to %s: %s", self.layout_file, e)
        self._last_save = now

    def run(self):
        """Blocking loop; returns after stop()."""
        cfg = self.cfg
        if self.routine.stand:
            jump = self.routine.stand_jump_every
            log.info("Stand mode: attacking in place, %s. %d buffs, %d popup templates.%s",
                     f"jumping every {jump:g}s" if jump else "no jumping", len(self.buffs.buffs),
                     len(self.watcher.templates), " (dry run)" if self.dry_run else "")
        else:
            log.info("Loaded %d waypoints, %d buffs, %d popup templates.%s", len(self.routine.waypoints),
                     len(self.buffs.buffs), len(self.watcher.templates), " (dry run)" if self.dry_run else "")
            if self.routine.layout is not None:
                log.info("Pathfinding %s.", f"on ({len(self.layout.ground)} learned ground points)"
                         if not self.layout.empty else "on, but nothing learned yet: record the route to teach it")
        if not self.routine.stand and not self.routine.waypoints:
            log.warning("Route %s has no waypoints: the bot will only drink potions and buff.", cfg["route"])
        game = capture.game_window(cfg)  # mss handles belong to the thread that made them
        period = 1.0 / cfg["loop_hz"]
        was_running = check_minimap = False
        last_status = last_wait_log = 0.0
        try:
            while not self.stopping.is_set():
                start = time.monotonic()
                if not self.running.is_set():
                    if was_running:
                        self.keys.release_all()
                        was_running = False
                    if self._status["phase"] != "paused":
                        self._publish(phase="paused")
                    time.sleep(0.1)
                    continue
                if not was_running:
                    self.routine.resume()
                    self.safety.reset()
                    if self.layout is not None:
                        self.layout.break_trace()
                    self.learning = self.learn and self.layout is not None
                    self._lost_since = None
                    self._publish(reason=None)
                    was_running = True
                    check_minimap = True

                frame = game.grab()
                focused = self.dry_run or game.is_foreground()
                if frame is None or not focused:
                    self.keys.release_all()
                    self._publish(phase="no_window" if frame is None else "unfocused")
                    if start - last_wait_log > 5:
                        log.info("Waiting for the game window to be %s.", "open" if frame is None else "focused")
                        last_wait_log = start
                    time.sleep(0.5)
                    continue
                if check_minimap:
                    self._follow_minimap(frame)
                    self._check_layout()
                    check_minimap = False

                state = self.vision.read(frame)
                if self.learning:
                    self._learn(state.pos, start)
                minimap = crop(frame, cfg["regions"].get("minimap"))
                self._publish(phase="running", pos=state.pos, others=state.others, hp=state.hp, mp=state.mp,
                              minimap=None if minimap is None else minimap.copy())

                reason = self.safety.check(frame, state, self.routine.moving, start)
                if reason:
                    self.keys.release_all()
                    self.running.clear()
                    was_running = False
                    self._publish(phase="paused", reason=reason)
                    self.safety.alert(reason)
                    continue

                self.healer.tick(state, start)
                buff = self.buffs.due(start)
                if buff and (self.routine.stationary or self.buffs.overdue(buff, start)):
                    self.keys.release_all()
                    self.buffs.cast(buff)
                    continue
                if state.pos or self.routine.stand:  # stand mode doesn't need a position
                    self.routine.tick(state.pos, start)
                self._publish(index=self.routine.index, routine=self.routine.state, lap=self.routine.lap,
                              path=list(self.routine.path))

                if start - last_status >= 5:
                    hp = f"{state.hp:.0%}" if state.hp is not None else "n/a"
                    mp = f"{state.mp:.0%}" if state.mp is not None else "n/a"
                    pos = tuple(round(c) for c in state.pos) if state.pos else None
                    log.info("pos %s  wp %d/%d %s  HP %s  MP %s", pos, self.routine.index + 1,
                             len(self.routine.waypoints), self.routine.state, hp, mp)
                    last_status = start
                time.sleep(max(0.0, period - (time.monotonic() - start)))
        finally:
            self.keys.release_all()
            if self.layout is not None and self.layout.dirty:
                self._save_layout(time.monotonic())
                log.info("Saved what it learned about the map to %s.", self.layout_file)
            self._publish(phase="stopped")
            log.info("Stopped.")
