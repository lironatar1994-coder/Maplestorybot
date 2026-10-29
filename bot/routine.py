"""Walks the recorded waypoint route and attacks at each attack waypoint.

Minimap y grows downwards, so a target with a smaller y is higher up.
Moving to a point:
  - far away horizontally: walk toward it (jumping now and then if it is also higher)
  - directly above: hold up and jump, to grab a rope/ladder and climb it
  - directly below: hold down and jump, to drop through the platform
With a learned map (bot/pathing.py) it heads for the next waypoint through the points of a path
it has walked before (platform edges, rope bottoms and tops), and finds the way back after being
knocked off a platform. With no known path it heads straight for the waypoint.

A waypoint with `every: N` is only visited on every Nth lap of the route; `skip_first: true`
also leaves it out of the first lap. A lap is one pass from the first waypoint to the last
(and, in pingpong mode, back again).

In stand mode (config `mode: stand`) it ignores the route: it holds attack where the character
stands and jumps every `stand.jump_every` seconds.
"""
import logging

log = logging.getLogger(__name__)


class Routine:
    def __init__(self, cfg, keys, layout=None):
        route = cfg["route_data"] or {}
        self.waypoints = route.get("waypoints") or []
        self.order = route.get("mode", "loop")
        self.k = cfg["keys"]
        m = cfg["movement"]
        self.tol_x, self.tol_y, self.jump_interval = m["tolerance_x"], m["tolerance_y"], m["jump_interval"]
        self.layout = layout  # bot.pathing.Layout to find paths with, or None to head straight for each waypoint
        self.replan_interval = (cfg.get("pathing") or {}).get("replan_interval", 0.5)
        self.stand = cfg.get("mode", "route") == "stand"
        stand = cfg.get("stand") or {}
        self.stand_face = stand.get("face")
        self.stand_jump_every = stand.get("jump_every") or 0
        self.keys = keys
        self.index = 0
        self.step = 1
        self.lap = 0
        self.path = []          # points to pass on the way to the current waypoint
        self.path_for = None    # index of the waypoint that path leads to
        self.goal = None        # or the point off the route it leads to (see go_to)
        self.last_plan = float("-inf")
        self.state = "attacking" if self.stand else "moving"
        self.attack_until = 0.0
        self.last_jump = 0.0
        self.faced = False
        if self.waypoints and not self._due(0):
            self._advance()

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
        self.path_for = None  # you may have moved the character while it was paused

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

        tol_x, tol_y = wp.get("tol_x", self.tol_x), wp.get("tol_y", self.tol_y)
        if abs(wp["x"] - pos[0]) <= tol_x and abs(wp["y"] - pos[1]) <= tol_y:
            self._arrive(wp, now)
            return
        point = self._next_point(pos, now)
        if point:
            self._move(point[0] - pos[0], point[1] - pos[1], self.tol_x, now)
        else:
            self._move(wp["x"] - pos[0], wp["y"] - pos[1], tol_x, now)

    def _next_point(self, pos, now):
        """The next point of the learned path to the current waypoint, or None to head straight for it."""
        if self.layout is None:
            return None
        new_target = self.path_for != self.index
        # Plan again now and then while on solid ground (after a knockback, say), never in mid-jump or mid-climb.
        if new_target or (now - self.last_plan >= self.replan_interval and self.layout.on_ground(pos)):
            wp = self.target
            path = self.layout.plan(pos, (wp["x"], wp["y"]))
            if path is None and new_target:
                log.info("No learned path to waypoint %d yet: heading straight for it.", self.index + 1)
            self.path = path or []
            self.path_for, self.goal, self.last_plan = self.index, None, now
        return self._path_point(pos)

    def _path_point(self, pos):
        while self.path and abs(self.path[0][0] - pos[0]) <= self.tol_x and abs(self.path[0][1] - pos[1]) <= self.tol_y:
            self.path.pop(0)
        return self.path[0] if self.path else None

    def go_to(self, pos, goal, now):
        """Heads for a point off the route (a monster on another platform), through the learned map's points if it
        knows a way there, else straight for it."""
        if self.layout is None:
            self.path, self.path_for = [], None
        else:
            moved = self.goal is None or max(abs(goal[0] - self.goal[0]), abs(goal[1] - self.goal[1])) > 3
            if moved or (now - self.last_plan >= self.replan_interval and self.layout.on_ground(pos)):
                self.path = self.layout.plan(pos, goal) or []
                self.goal, self.path_for, self.last_plan = goal, None, now
        point = self._path_point(pos) or goal
        dx, dy = point[0] - pos[0], point[1] - pos[1]
        if abs(dx) <= self.tol_x and abs(dy) <= self.tol_y:
            self._release("left", "right", "up", "down")  # there (it's the hunter's call what to do next)
        else:
            self._move(dx, dy, self.tol_x, now)

    def step_toward(self, dx, dy, now):
        """One step toward a point dx, dy minimap px away, with no position to plan from."""
        self._move(dx, dy, self.tol_x, now)

    def _move(self, dx, dy, tol_x, now):
        if abs(dx) > tol_x:
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

    def _due(self, i):
        """Whether waypoint i is visited on this lap."""
        wp = self.waypoints[i]
        return self.lap % wp.get("every", 1) == 0 and not (wp.get("skip_first") and self.lap == 0)

    def _advance(self):
        self.state = "moving"
        n = len(self.waypoints)
        if n <= 1:
            return
        # Some other waypoint is due within max(every) laps, so this always finds one well before the limit.
        # In pingpong mode it turns around at the last waypoint due this lap instead of revisiting it.
        start, longest = self.index, max(wp.get("every", 1) for wp in self.waypoints)
        for _ in range(2 * n * (longest + 1)):
            self._step()
            if self._due(self.index) and (self.order != "pingpong" or self.index != start):
                break
        log.info("-> waypoint %d %s", self.index + 1, self.target)

    def _step(self):
        n = len(self.waypoints)
        if self.order == "pingpong":
            if not 0 <= self.index + self.step < n:
                self.step = -self.step
            self.index += self.step
        else:
            self.index = (self.index + 1) % n
        if self.index == 0:
            self.lap += 1
