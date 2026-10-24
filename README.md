# MapleStory Worlds (Classic) training bot

A screen-reading bot for a ranged character. It captures the game window, reads the minimap (your yellow dot, other players' red dots) and the HP/MP bars, and presses keys. It walks a route you record, stops at attack points to shoot for a set time, drinks potions, and recasts buffs on timers.

It does not read game memory or get around anti-cheat. It does not solve lie detectors or runes: it **pauses and beeps** so you can handle them yourself.

> Botting breaks Nexon's Terms of Service. The account can be banned. Use one you can afford to lose.

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

Run the game in **windowed mode** and keep the minimap open. If the game runs as administrator, run your terminal as administrator too, or Windows will block both the key presses and the F9/F10 hotkeys.

## Control panel

```bash
python gui.py
```

Or double-click **`Start Bot (Admin).bat`**. It asks Windows for admin rights, then opens the panel. Keep its console window open while you use the panel; closing it closes the panel.

- **Run tab**: Start / Pause / Stop (F9 / F10 still work), a dry-run checkbox, a banner that turns red with the reason when the bot pauses itself, a live minimap with your dot (yellow), other players (red) and the route's waypoints, HP/MP bars, and the log.
- **Settings tab**: window title, route file, key bindings, potion thresholds, buffs, movement and safety settings. **Save settings** writes `settings.yaml`, which overrides `config.yaml`. Changes apply the next time you press Start.
- **Calibrate tab**: buttons for every calibration step below (each opens its own console window), plus your saved popup templates. These buttons are disabled while the bot runs.

Keep the control panel beside the game, not on top of it: the bot reads what's actually on screen.

Everything below can also be done from the command line.

## Auto setup (recommended)

The game runs as administrator, so open the panel with **`Start Bot (Admin).bat`**; otherwise Windows drops the bot's key presses. Then click **Auto setup** (Run or Calibrate tab), or run `python calibrate.py auto`.

1. If the game isn't running, it opens it through your **MapleStory Classic World** shortcut and waits for you to log in. Logging in is always up to you.
2. Once you're in a map with the minimap open, it finds the minimap (by its "MINI MAP" title) and the HP/MP bars (have both full).
3. It walks a step right and back. The yellow dot that moved is you, and it fits the colour range to that dot, so coins on shop signs showing through the minimap don't fool it.
4. It presses jump and checks that the dot went up.
5. It reads the attack key (the slot labelled ATTACK) and the HP/MP potion keys (red and blue potion icons) from the quickslot bar.
6. It saves everything to `calibration.yaml` and `settings.yaml`, writes `debug/overlay.png`, and lists anything left to fix. With no route recorded, it switches Training mode to stand.

Each time you press Start, the bot finds the minimap again, so moving the minimap window is fine. Zooming it with +/- changes its scale and throws off recorded routes.

## One-time calibration by hand (per resolution / UI layout)

1. `python calibrate.py windows` lists the open window titles. Put a unique part of the game's title in `window_title` in `config.yaml`.
2. `python calibrate.py keytest`: click into the game within 5 seconds. Your character should jump 3 times. If it doesn't, the game isn't accepting the bot's key presses (see the admin note above).
3. `python calibrate.py regions`: drag a box around the minimap's map area (leave out the title bar with the map name), then the HP bar and the MP bar. Check `debug/overlay.png`.
4. `python calibrate.py check`: a live, zoomed minimap with what the bot detects. Hover a dot to read its HSV value. If your dot isn't circled, adjust `vision.player_hsv` in `config.yaml`. Check that HP/MP read about right.
5. Set your key bindings, potion thresholds and buffs in `config.yaml`.

## Record a route (per map)

`python calibrate.py route` (or `python calibrate.py route routes/ant_tunnel.yaml`, and point `route:` in `config.yaml` at it). Walk the character around and press:

| Key | Records |
|---|---|
| F5 | attack here, facing left |
| F6 | attack here, facing right |
| F7 | pass-through point |
| F8 | undo last |
| F10 | save and quit |

Add pass points at the bottom and top of each rope, and at the spots where you jump up or drop down. The bot walks straight toward the next point, climbs (up + jump) when the point is directly above, and drops (down + jump) when it's directly below. Set `mode: pingpong` in the route file to walk it back and forth instead of looping. You can edit any waypoint's `seconds` or `face`, or add `tol_x` / `tol_y`, by hand.

## Stand mode (attack in place)

Set **Training mode** to `stand` in the Settings tab (or `mode: stand` in `config.yaml`) to skip the route. The bot holds attack where your character stands and jumps every `jump_every` seconds (default 10; 0 turns jumping off). It lets go of attack just before each jump so the jump isn't swallowed by an attack animation. Set `face` to `left` or `right` to turn that way first. Potions, buffs and safety pauses work the same.

You don't need a route for stand mode, and the minimap region is optional. Without it the bot can't see other players or notice your dot disappearing, so calibrate the minimap anyway if you want those pauses.

## Popups that pause the bot

With the popup on screen, run `python calibrate.py template lie_detector` (or `death`, `rune`...) and box a distinctive part of it. Every PNG in `templates/` pauses the bot when it appears.

## Run

```bash
python main.py --dry-run
```

This runs the full loop but only logs the keys it would press. Walk around by hand and check the log. Then:

```bash
python main.py
```

**F9** start/pause, **F10** quit. The bot only presses keys while the game window is focused, and it releases every key when it pauses or quits.

It pauses and beeps when:
- another player shows up on the minimap
- your dot disappears for a few seconds (map change, disconnect)
- a saved popup template appears
- it's been trying to move without the dot moving (stuck)

Press F9 to resume once you've handled it.
