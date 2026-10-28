"""Learns where the character can go on a map, and finds paths between waypoints (A*).

The route recorder and the running bot feed it every position the player's dot is seen at.
From that trace it learns, in minimap pixels:
  - ground: spots the character stood or walked on (at one height for a third of a second;
    a jump doesn't stay at its top that long)
  - moves: which spot it got to next. A move that ends level or higher can also be made
    backwards (walk back, climb back down a rope, fall back down a jump). Drops and falls
    only go one way, so climb each rope up at least once for the bot to know it can.

A path is cut down to the points the routine's simple moves can reach one after another:
the ends and corners of each stretch of ground, so the bot walks to a rope's bottom, climbs to
its top, walks to a ledge, drops, and so on. It never aims at a point in mid-air.
"""
import heapq
import json
import logging
import math
from pathlib import Path

log = logging.getLogger(__name__)

GROUND_SECONDS = 0.3  # this long at one height counts as standing or walking
LEVEL = 0.75         # px the dot's height may wobble and still count as one height
MAX_STEP = 8       # minimap px between two sightings; more is a teleport, map change or misread dot
MAX_GAP = 0.5      # seconds between sightings before the trace counts as broken
SNAP = 4           # how far (px) a position may be from what's been learned and still use it
CORNER = 1.5       # px a stretch of ground may bend before the bend becomes a point of its own


def layout_path(route_path):
    """routes/ant_tunnel.yaml -> routes/ant_tunnel.layout.json"""
    route_path = Path(route_path)
    return route_path.with_name(route_path.stem + ".layout.json")


def _dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _cell(pos):
    return int(round(pos[0])), int(round(pos[1]))


def _snap(pos, cells):
    """The learned cell nearest to pos (within SNAP px), or None."""
    cx, cy = _cell(pos)
    best = None
    for dx in range(-SNAP, SNAP + 1):
        for dy in range(-SNAP, SNAP + 1):
            cell = (cx + dx, cy + dy)
            if cell in cells and (best is None or dx * dx + dy * dy < best[0]):
                best = (dx * dx + dy * dy, cell)
    return best[1] if best else None


def _simplify(points, eps=CORNER):
    """Ramer-Douglas-Peucker: keeps the ends and the real corners of a stretch of points."""
    if len(points) < 3:
        return list(points)
    (x1, y1), (x2, y2) = points[0], points[-1]
    dx, dy = x2 - x1, y2 - y1
    norm = math.hypot(dx, dy)
    far_i, far_d = 0, -1.0
    for i, (x, y) in enumerate(points[1:-1], 1):
        d = abs(dy * (x - x1) - dx * (y - y1)) / norm if norm else math.hypot(x - x1, y - y1)
        if d > far_d:
            far_i, far_d = i, d
    if far_d <= eps:
        return [points[0], points[-1]]
    return _simplify(points[:far_i + 1], eps)[:-1] + _simplify(points[far_i:], eps)


class Layout:
    def __init__(self, size=None):
        self.size = size   # minimap [w, h] it was learned at (zooming the minimap changes every position)
        self.ground = set()
        self.moves = {}    # cell -> cells reachable from it in one step
        self.dirty = False
        self.break_trace()

    @classmethod
    def load(cls, path):
        layout = cls()
        path = Path(path)
        if not path.exists():
            return layout
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            layout.size = data.get("size")
            layout.ground = {tuple(c) for c in data.get("ground", [])}
            for x1, y1, x2, y2 in data.get("moves", []):
                layout.moves.setdefault((x1, y1), set()).add((x2, y2))
        except (OSError, ValueError, TypeError) as e:
            log.warning("Couldn't read %s (%s): learning the map from scratch.", path, e)
            return cls()
        return layout

    def save(self, path):
        path = Path(path)
        data = {"size": self.size, "ground": sorted(self.ground),
                "moves": sorted([*a, *b] for a, targets in self.moves.items() for b in targets)}
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")  # write then swap, so a crash never leaves half a file
        tmp.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
        tmp.replace(path)
        self.dirty = False

    @property
    def empty(self):
        return not self.moves

    # Learning
    def break_trace(self):
        """The next sighting doesn't follow on from the last one (dot lost, paused, buff cast)."""
        self._prev = self._prev_t = None
        self._run, self._run_y, self._run_t0 = [], None, None  # cells seen in a row at one height, since when

    def observe(self, pos, now):
        cell = _cell(pos)
        prev = self._prev
        if prev is None or now - self._prev_t > MAX_GAP or _dist(prev, cell) > MAX_STEP:
            self._run = []
        elif cell != prev:
            self._link(prev, cell)
        if self._run and abs(pos[1] - self._run_y) <= LEVEL:
            if cell != self._run[-1]:
                self._run.append(cell)
        else:
            self._run, self._run_y, self._run_t0 = [cell], pos[1], now
        if now - self._run_t0 >= GROUND_SECONDS:
            new = [c for c in self._run if c not in self.ground]
            if new:
                self.ground.update(new)
                self.dirty = True
            self._run = self._run[-1:]  # the rest is marked
        self._prev, self._prev_t = cell, now

    def _link(self, a, b):
        self._add(a, b)
        if b[1] <= a[1]:  # ends level or higher, so going back the same way works too
            self._add(b, a)

    def _add(self, a, b):
        targets = self.moves.setdefault(a, set())
        if b not in targets:
            targets.add(b)
            self.dirty = True

    # Planning
    def on_ground(self, pos):
        return _cell(pos) in self.ground

    def plan(self, start, goal):
        """Points to pass through from start to goal (neither included), or None if no learned path joins them."""
        s, g = _snap(start, self.moves), _snap(goal, self.ground)
        if s is None or g is None:
            return None
        if s == g:
            return []
        came, cost = {s: None}, {s: 0.0}
        frontier = [(_dist(s, g), 0.0, s)]
        while frontier:
            _, c, cell = heapq.heappop(frontier)
            if cell == g:
                break
            if c > cost[cell]:
                continue
            for nxt in self.moves.get(cell, ()):
                nc = c + _dist(cell, nxt)
                if nc < cost.get(nxt, math.inf):
                    cost[nxt], came[nxt] = nc, cell
                    heapq.heappush(frontier, (nc + _dist(nxt, g), nc, nxt))
        else:
            return None
        path = [g]
        while came[path[-1]] is not None:
            path.append(came[path[-1]])
        path.reverse()
        return self._key_points(path)

    def _key_points(self, path):
        """The corners of each stretch of ground along the path; the rope, jump or drop between two
        stretches is left to the routine's moves."""
        runs, run = [], []
        for cell in path:
            if cell in self.ground:
                run.append(cell)
            elif run:
                runs.append(run)
                run = []
        if run:
            runs.append(run)
        ends = (path[0], path[-1])
        return [p for run in runs for p in _simplify(run) if p not in ends]
