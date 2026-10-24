"""Recasts buffs on fixed timers."""
import logging
import time

log = logging.getLogger(__name__)


class Buffs:
    def __init__(self, buff_cfgs, keys, overdue_grace):
        self.keys = keys
        self.overdue_grace = overdue_grace
        # last=None means "never cast", so every buff goes up when the bot starts.
        self.buffs = [{"key": b["key"], "interval": b["interval"], "cast_time": b.get("cast_time", 1.0),
                       "last": None} for b in buff_cfgs or []]

    def due(self, now):
        """The most overdue buff, or None."""
        due = [b for b in self.buffs if b["last"] is None or now - b["last"] >= b["interval"]]
        return min(due, key=lambda b: b["last"] or float("-inf")) if due else None

    def overdue(self, buff, now):
        return buff["last"] is None or now - buff["last"] >= buff["interval"] + self.overdue_grace

    def cast(self, buff):
        log.info("buff (%s)", buff["key"])
        self.keys.press(buff["key"])
        time.sleep(buff["cast_time"])
        buff["last"] = time.monotonic()
