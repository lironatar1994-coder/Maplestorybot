"""Decides when the bot must stop and get your attention."""
import logging
import threading
import winsound

log = logging.getLogger(__name__)


class Safety:
    def __init__(self, cfg, watcher):
        self.s = cfg["safety"]
        self.has_minimap = bool(cfg["regions"].get("minimap"))  # stand mode can run without one
        self.watcher = watcher
        self.reset()

    def reset(self):
        self.other_frames = 0
        self.lost_since = None
        self.last_template_check = float("-inf")
        self.stuck_ref = None
        self.stuck_since = None

    def check(self, frame, state, moving, now):
        """Returns why the bot must pause, or None."""
        s = self.s
        if s["pause_on_other_player"] and state.others:
            self.other_frames += 1
            if self.other_frames >= s["other_player_frames"]:
                return f"another player on the minimap ({len(state.others)})"
        else:
            self.other_frames = 0

        if state.pos is None and self.has_minimap:
            if self.lost_since is None:
                self.lost_since = now
            if now - self.lost_since >= s["lost_player_seconds"]:
                return "player dot not found (map change, disconnect or minimap hidden)"
        else:
            self.lost_since = None

        if now - self.last_template_check >= s["template_check_interval"]:
            self.last_template_check = now
            hit = self.watcher.match(frame)
            if hit:
                return f"popup on screen: {hit[0]} (match {hit[1]:.2f})"

        if moving and state.pos:
            if self.stuck_ref is None or max(abs(state.pos[0] - self.stuck_ref[0]),
                                             abs(state.pos[1] - self.stuck_ref[1])) > 2:
                self.stuck_ref, self.stuck_since = state.pos, now
            elif now - self.stuck_since >= s["stuck_seconds"]:
                return f"stuck at {tuple(round(c) for c in state.pos)} for {s['stuck_seconds']}s"
        else:
            self.stuck_ref = None
        return None

    def alert(self, reason):
        log.warning("PAUSED: %s. Handle it, then press the toggle hotkey to resume.", reason)
        threading.Thread(target=self._beep, daemon=True).start()

    def _beep(self):
        for _ in range(self.s["beeps"]):
            winsound.Beep(1200, 300)
            winsound.Beep(800, 300)
