"""Drinks HP/MP potions when the bars drop below their thresholds."""
import logging

log = logging.getLogger(__name__)


class Healer:
    def __init__(self, cfg, keys):
        h, k = cfg["healer"], cfg["keys"]
        self.keys = keys
        self.cooldown = h["cooldown"]
        self.potions = [("hp", h["hp_threshold"], k.get("hp_potion")),
                        ("mp", h["mp_threshold"], k.get("mp_potion"))]
        self.last = {"hp": float("-inf"), "mp": float("-inf")}

    def tick(self, state, now):
        for kind, threshold, key in self.potions:
            value = getattr(state, kind)
            if key is None or value is None:
                continue
            if value < threshold and now - self.last[kind] >= self.cooldown:
                log.info("%s %.0f%% < %.0f%%: potion (%s)", kind.upper(), value * 100, threshold * 100, key)
                self.keys.press(key)
                self.last[kind] = now
