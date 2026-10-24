"""MapleStory Worlds (Classic) training bot, command-line version (see gui.py for the control panel).

  python main.py              run (F9 start/pause, F10 quit)
  python main.py --dry-run    same loop, but only logs the keys it would press
"""
import argparse
import logging
import sys

import keyboard

from bot import capture, config
from bot.engine import Engine


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--dry-run", action="store_true", help="log key presses instead of sending them")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")

    capture.set_dpi_aware()
    cfg = config.load(args.config)
    if cfg.get("mode") != "stand" and not cfg["regions"].get("minimap"):
        sys.exit("No minimap region yet. Run `python calibrate.py regions` first (or set mode: stand).")

    engine = Engine(cfg, dry_run=args.dry_run)
    keyboard.add_hotkey(cfg["hotkeys"]["toggle"], engine.toggle)
    keyboard.add_hotkey(cfg["hotkeys"]["quit"], engine.stop)
    logging.getLogger("bot").info("Press %s to start/pause, %s to quit.", cfg["hotkeys"]["toggle"].upper(),
                                  cfg["hotkeys"]["quit"].upper())
    try:
        engine.run()
    except KeyboardInterrupt:
        engine.stop()
    finally:
        keyboard.unhook_all()


if __name__ == "__main__":
    main()
