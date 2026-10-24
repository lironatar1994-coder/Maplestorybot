"""Setup tool for the bot.

  python calibrate.py auto             open the game if needed, then find and test everything by itself
                                       (`auto nolaunch` to skip opening the game)
  python calibrate.py windows          list window titles (to set window_title in config.yaml)
  python calibrate.py keytest          press the jump key 3 times in the game, to check it accepts input
  python calibrate.py regions          drag boxes around the minimap and the HP/MP bars
  python calibrate.py check            live view of what the bot sees (hover the minimap for HSV values)
  python calibrate.py route [file]     walk your character around and record waypoints
  python calibrate.py template NAME    save a popup crop (lie detector, death dialog...) that pauses the bot
"""
import argparse
import logging
import sys
import time
import traceback
from pathlib import Path

import cv2
import keyboard

from bot import capture, config, vision
from bot.autosetup import AutoSetup
from bot.keys import Keys

DEBUG = Path("debug")


def grab_or_exit(game):
    frame = game.grab()
    if frame is None:
        sys.exit(f"Game window containing {game.title_part!r} not found (or minimized). "
                 "Run `python calibrate.py windows` and fix window_title in config.yaml.")
    return frame


def countdown(seconds, what):
    for i in range(seconds, 0, -1):
        print(f"{what} in {i}...")
        time.sleep(1)


def cmd_windows(cfg, args):
    for hwnd, title in capture.list_windows():
        print(f"{hwnd:>10}  {title}")


def cmd_keytest(cfg, args):
    game = capture.game_window(cfg)
    if not game.locate():
        sys.exit("Game window not found.")
    print("Click into the game window now.")
    countdown(5, "Pressing jump")
    if not game.is_foreground():
        sys.exit("The game wasn't the focused window, so nothing was pressed.")
    keys = Keys()
    for _ in range(3):
        keys.press(cfg["keys"]["jump"], hold=0.1)
        time.sleep(0.6)
    print("Done. If the character didn't jump, the game ignored the input: try running this terminal "
          "as administrator (Windows blocks input to apps running at a higher privilege level).")


def select(frame, label):
    print(f"Drag a box around the {label}, then press ENTER (or C to skip).")
    x, y, w, h = cv2.selectROI(f"Select {label}", frame, showCrosshair=False)
    cv2.destroyAllWindows()
    return [int(x), int(y), int(w), int(h)] if w and h else None


def cmd_regions(cfg, args):
    game = capture.game_window(cfg)
    print("Click into the game window now (it must be uncovered).")
    countdown(3, "Capturing")
    frame = grab_or_exit(game)
    if not game.is_foreground():
        print("Warning: the game wasn't in front, so the screenshot may show another window.")
    print(f"Captured {game.title!r} ({frame.shape[1]}x{frame.shape[0]}).")
    print("Tip: for the minimap, box only the map area, not the title bar with the map name.")
    cal = config.read_yaml(cfg["calibration_file"], {}) or {}
    regions = cal.get("regions", {})
    for name, label in (("minimap", "minimap"), ("hp_bar", "HP bar (whole bar, full width)"),
                        ("mp_bar", "MP bar (whole bar, full width)")):
        region = select(frame, label)
        if region:
            regions[name] = region
    cal["regions"] = regions
    config.save_yaml(cfg["calibration_file"], cal)
    print(f"Saved to {cfg['calibration_file']}.")
    cfg["regions"] = regions
    write_overlay(cfg, frame)


def write_overlay(cfg, frame):
    state = vision.Vision(cfg).read(frame)
    DEBUG.mkdir(exist_ok=True)
    cv2.imwrite(str(DEBUG / "screenshot.png"), frame)
    cv2.imwrite(str(DEBUG / "overlay.png"), vision.draw_overlay(frame, cfg["regions"], state))
    print(f"Player dot: {state.pos}  others: {len(state.others)}  HP: {fmt(state.hp)}  MP: {fmt(state.mp)}")
    print(f"Overlay written to {DEBUG / 'overlay.png'}")


def fmt(value):
    return "n/a" if value is None else f"{value:.0%}"


def cmd_check(cfg, args):
    if not cfg["regions"].get("minimap"):
        sys.exit("Run `python calibrate.py regions` first.")
    game = capture.game_window(cfg)
    vis = vision.Vision(cfg)
    zoom = 4
    hover = {"pt": None}

    def on_mouse(event, x, y, flags, param):
        hover["pt"] = (x // zoom, y // zoom)

    cv2.namedWindow("minimap")
    cv2.setMouseCallback("minimap", on_mouse)
    print("Q/ESC to quit, S to save debug/overlay.png.")
    while True:
        frame = grab_or_exit(game)
        state = vis.read(frame)
        mm = vision.crop(frame, cfg["regions"]["minimap"])
        view = cv2.resize(mm, None, fx=zoom, fy=zoom, interpolation=cv2.INTER_NEAREST)
        if state.pos:
            cv2.circle(view, (int(state.pos[0] * zoom), int(state.pos[1] * zoom)), 3 * zoom, (0, 255, 255), 2)
        for ox, oy, _ in state.others:
            cv2.circle(view, (int(ox * zoom), int(oy * zoom)), 3 * zoom, (0, 0, 255), 2)
        lines = [f"pos {tuple(round(c) for c in state.pos) if state.pos else None}  others {len(state.others)}",
                 f"HP {fmt(state.hp)}  MP {fmt(state.mp)}"]
        pt = hover["pt"]
        if pt and pt[0] < mm.shape[1] and pt[1] < mm.shape[0]:
            h, s, v = cv2.cvtColor(mm[pt[1]:pt[1] + 1, pt[0]:pt[0] + 1], cv2.COLOR_BGR2HSV)[0, 0]
            lines.append(f"({pt[0]},{pt[1]}) HSV {h},{s},{v}")
        for i, line in enumerate(lines):
            cv2.putText(view, line, (4, 14 + 16 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1)
        cv2.imshow("minimap", view)
        key = cv2.waitKey(50) & 0xFF
        if key in (ord("q"), 27):
            break
        if key == ord("s"):
            write_overlay(cfg, frame)
    cv2.destroyAllWindows()


def cmd_route(cfg, args):
    if not cfg["regions"].get("minimap"):
        sys.exit("Run `python calibrate.py regions` first.")
    path = args.arg or cfg["route"]
    game = capture.game_window(cfg)
    vis = vision.Vision(cfg)
    route = config.read_yaml(path, None) or {"mode": "loop", "waypoints": []}
    if route["waypoints"] and input(f"{path} has {len(route['waypoints'])} waypoints. Start over? [y/N] ").lower() == "y":
        route["waypoints"] = []
    seconds = float(input("Seconds to attack at each attack waypoint [6]: ") or 6)
    waypoints = route["waypoints"]

    def record(action, face=None):
        state = vis.read(grab_or_exit(game))
        if not state.pos:
            print("  player dot not found, nothing recorded")
            return
        wp = {"x": round(state.pos[0]), "y": round(state.pos[1]), "action": action}
        if action == "attack":
            wp.update(face=face, seconds=seconds)
        waypoints.append(wp)
        print(f"  #{len(waypoints)}: {wp}")

    def undo():
        if waypoints:
            print(f"  removed {waypoints.pop()}")

    keyboard.add_hotkey("f5", record, args=("attack", "left"))
    keyboard.add_hotkey("f6", record, args=("attack", "right"))
    keyboard.add_hotkey("f7", record, args=("pass",))
    keyboard.add_hotkey("f8", undo)
    print("Walk your character to each spot, in order, and press:\n"
          "  F5  attack here facing LEFT\n  F6  attack here facing RIGHT\n"
          "  F7  pass-through point (rope bottom/top, ledge edge)\n  F8  undo last\n  F10 save and quit\n"
          "Tip: add pass points at the bottom and top of each rope and where you jump or drop between platforms.")
    keyboard.wait("f10")
    keyboard.unhook_all()
    route["mode"] = route.get("mode", "loop")
    config.save_yaml(path, route)
    print(f"Saved {len(waypoints)} waypoints to {path}. Set mode: pingpong there to walk the route back and forth.")


def cmd_template(cfg, args):
    if not args.arg:
        sys.exit("Usage: python calibrate.py template NAME")
    game = capture.game_window(cfg)
    print("Click into the game and make sure the popup is on screen.")
    countdown(3, "Capturing")
    frame = grab_or_exit(game)
    region = select(frame, f"'{args.arg}' popup (pick a distinctive part, e.g. its title)")
    if not region:
        sys.exit("Nothing selected.")
    folder = Path(cfg["safety"]["template_dir"])
    folder.mkdir(exist_ok=True)
    out = folder / f"{args.arg}.png"
    cv2.imwrite(str(out), vision.crop(frame, region))
    print(f"Saved {out}.")


def cmd_auto(cfg, args):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    results = AutoSetup(cfg, launch=args.arg != "nolaunch").run()
    problems = [msg for ok, msg in results if not ok]
    print("\nAuto setup finished." + (" Things to fix:" if problems else " Everything checked out."))
    for msg in problems:
        print(f"  - {msg}")


COMMANDS = {"auto": cmd_auto, "windows": cmd_windows, "keytest": cmd_keytest, "regions": cmd_regions,
            "check": cmd_check, "route": cmd_route, "template": cmd_template}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=COMMANDS)
    parser.add_argument("arg", nargs="?")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--pause", action="store_true", help="wait for Enter before exiting (used by the control panel)")
    args = parser.parse_args()
    capture.set_dpi_aware()
    try:
        COMMANDS[args.command](config.load(args.config), args)
    except SystemExit as e:
        if not args.pause:
            raise
        if e.code not in (None, 0):
            print(e.code)
    except Exception:
        if not args.pause:
            raise
        traceback.print_exc()
    if args.pause:
        input("\nPress Enter to close this window.")


if __name__ == "__main__":
    main()
