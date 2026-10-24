"""Walks the recorded waypoint route and attacks at each attack waypoint.

Minimap y grows downwards, so a target with a smaller y is higher up.
Moving to a waypoint:
  - far away horizontally: walk toward it (jumping now and then if it is also higher)
  - directly above: hold up and jump, to grab a rope/ladder and climb it
  - directly below: hold down and jump, to drop through the platform

In stand mode (config `mode: stand`) it ignores the route: it holds attack where the character
stands and jumps every `stand.jump_every` seconds.
"""
import logging

log = logging.getLogger(__name__)


class Routine:
    def __init__(self, cfg, keys):
        route = cfg["route_data"] or {}
        self.waypoints = route.get("waypoints") or []
        self.order = route.get("mode", "loop")
        self.k = cfg["keys"]
        m = cfg["movement"]
        self.tol_x, self.tol_y, self.jump_interval = m["tolerance_x"], m["tolerance_y"], m["jump_interval"]
        self.stand = cfg.get("mode", "route") == "stand"
        stand = cfg.get("stand") or {}
        self.stand_face = stand.get("face")
        self.stand_jump_every = stand.get("jump_every") or 0
        self.keys = keys
        self.index = 0
        self.step = 1
        self.state = "attacking" if self.stand else "moving"
        self.attack_until = 0.0
        self.last_jump = 0.0
        self.faced = False

    @property
    def moving(self):
        return self.state == "moving" and bool(self.waypoints)

    @property
    def stationary(self):
        return self.state == "attacking"

    @property
    def target(self):
        return self.waypoints[self.index] if self.waypoints else None

    def resume(self):
        """Called after a pause: head for the current waypoint again (or face the set way again in stand mode)."""
        self.state = "attacking" if self.stand else "moving"
        self.faced = False

    def tick(self, pos, now):
        if self.stand:
            self._stand_tick(now)
            return
        wp = self.target
        if wp is None:
            return
        if self.state == "attacking":
            if now >= self.attack_until:
                self.keys.up(self.k["attack"])
                self._advance()
            else:
                self.keys.down(self.k["attack"])  # re-held after a potion/buff released it
            return

        dx, dy = wp["x"] - pos[0], wp["y"] - pos[1]
        if abs(dx) <= wp.get("tol_x", self.tol_x) and abs(dy) <= wp.get("tol_y", self.tol_y):
            self._arrive(wp, now)
        elif abs(dx) > wp.get("tol_x", self.tol_x):
            toward, away = ("right", "left") if dx > 0 else ("left", "right")
            self._release(away, "up", "down")
            self.keys.down(self.k[toward])
            if dy < -self.tol_y:
                self._jump(now)
        elif dy < 0:
            self._release("left", "right", "down")
            self.keys.down(self.k["up"])
            self._jump(now)
        else:
            self._release("left", "right", "up")
            self.keys.down(self.k["down"])
            self._jump(now)

    def _stand_tick(self, now):
        if not self.faced:
            if self.stand_face in ("left", "right"):
                self.keys.up(self.k["attack"])
                self.keys.press(self.k[self.stand_face], hold=0.04)
            self.faced = True
            self.last_jump = now
        if self.stand_jump_every and now - self.last_jump >= self.stand_jump_every:
            # Let go of attack first; the game won't start a jump in the middle of an attack.
            self.keys.up(self.k["attack"])
            self.keys.press(self.k["jump"], hold=0.15)
            self.last_jump = now
        self.keys.down(self.k["attack"])

    def _jump(self, now):
        if now - self.last_jump >= self.jump_interval:
            self.keys.press(self.k["jump"])
            self.last_jump = now

    def _release(self, *names):
        for name in names:
            self.keys.up(self.k[name])

    def _arrive(self, wp, now):
        self._release("left", "right", "up", "down")
        if wp.get("action") == "attack":
            face = wp.get("face")
            if face in ("left", "right"):
                self.keys.press(self.k[face], hold=0.04)  # turn around without walking far
            log.info("waypoint %d: attacking %s for %.0fs", self.index + 1, face or "", wp.get("seconds", 6))
            self.state = "attacking"
            self.attack_until = now + wp.get("seconds", 6)
            self.keys.down(self.k["attack"])
        else:
            self._advance()

    def _advance(self):
        self.state = "moving"
        n = len(self.waypoints)
        if n <= 1:
            return
        if self.order == "pingpong":
            if not 0 <= self.index + self.step < n:
                self.step = -self.step
            self.index += self.step
        else:
            self.index = (self.index + 1) % n
        log.info("-> waypoint %d %s", self.index + 1, self.target)
