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

Run the game windowed or fullscreen, at any size, and keep the minimap open. The game draws its UI for a 768-pixel-tall window and scales it up on bigger screens, so the bot scales what it captures back to that height, and the same calibration and routes work on every screen. `settings.yaml` stays on each computer, so run **Auto setup** once on each computer you use. If the game runs as administrator, run your terminal as administrator too, or Windows will block both the key presses and the F9/F10 hotkeys.

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
6. It saves everything to `calibration.yaml` and `settings.yaml`, writes `debug/overlay.png`, and lists anything left to fix. With no route recorded, it switches Training mode to hunt.

From the panel, you follow it in a small box at the top of the game screen, with no pop-up windows, so the game keeps focus and stays fullscreen. The box ignores clicks and the bot's screenshots leave it out. It closes by itself when setup finishes, or when you press F9 to start.

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

Add pass points at the bottom and top of each rope, and at the spots where you jump up or drop down. The bot walks toward the next point, climbs (up + jump) when the point is directly above, and drops (down + jump) when it's directly below. Set `mode: pingpong` in the route file to walk it back and forth instead of looping. You can edit any waypoint's `seconds` or `face`, or add `tol_x` / `tol_y`, by hand.

While you record, the bot also learns the map from how you move (see **Pathfinding** below), so walk every platform and climb every rope it may need, including the way back up from below.

### Waypoints that only run some laps

A lap is one pass through the route (in pingpong mode, there and back). Add `every` to a waypoint in the route file to visit it only every Nth lap, and `skip_first: true` to also leave it out of the first lap:

```yaml
waypoints:
  - {x: 40, y: 80, action: attack, face: left, seconds: 6}
  - {x: 95, y: 30, action: attack, face: right, seconds: 8, every: 3}                 # laps 1, 4, 7...
  - {x: 120, y: 30, action: attack, face: left, seconds: 8, every: 2, skip_first: true}  # laps 3, 5, 7...
```

A skipped waypoint isn't walked to at all, so for a detour (say, a far platform every third lap) put the same `every` on each of its points. The Run tab shows the current lap.

## Pathfinding

The bot keeps a map of where your character has been, saved next to the route as `routes/<name>.layout.json`. It learns the spots you stood or walked on, and which moves you made between them: walks, rope climbs, jumps and drops. Walks and climbs work both ways; drops and falls only go down, so climb each rope up at least once.

With that map it finds the shortest path (A*) to the next waypoint and walks it point by point: to the platform edge, down the drop, to the rope, up the rope, and so on. After a monster knocks the character off a platform, it plans again from where it lands. Where the map has no path yet, it walks straight toward the waypoint as before. It learns while you record a route and, unless you turn it off, while it runs.

The Run tab's minimap shows the learned ground in blue and the planned path in pink. To start over, use **Forget learned map** in the Calibrate tab. Turn pathfinding off in Settings (Movement) to go back to straight lines. The map is tied to the minimap's size: after zooming it with +/-, the bot ignores the old map until you undo the zoom or delete the file.

## Stand mode (attack in place)

Set **Training mode** to `stand` in the Settings tab (or `mode: stand` in `config.yaml`) to skip the route. The bot holds attack where your character stands and jumps every `jump_every` seconds (default 10; 0 turns jumping off). It lets go of attack just before each jump so the jump isn't swallowed by an attack animation. Set `face` to `left` or `right` to turn that way first. Potions, buffs and safety pauses work the same.

You don't need a route for stand mode, and the minimap region is optional. Without it the bot can't see other players or notice your dot disappearing, so calibrate the minimap anyway if you want those pauses.

## Hunt mode (find monsters and throw at them)

Set **Training mode** to `hunt`. The bot finds the monsters on the whole screen, goes for the one that's quickest to get in range, and throws stars once it's in range. When that one dies it goes for the next. It only throws at monsters it can see. There's nothing to teach it, though it gets better on each map as it learns what the monsters there look like.

- **Finding you.** It reads your name off the HUD (bottom left) and finds the same text in the name tag under your character.
- **Finding monsters.** It uses three signs:
  - **Movement.** It stands still for half a second and picks out whatever moves without a player's name tag under it. Monsters walk, bounce or breathe; the background doesn't.
  - **HP bars.** A monster that was hit shows an HP bar above it, and the bot finds that bar every time.
  - **Pictures.** A hit monster stands between its HP bar and its name ("Lv.6 Slime"). The bot cuts that picture out and keeps it in `mobs/<minimap size>/`, one folder per map, so later it recognises those monsters anywhere on screen, without stopping. Monsters that other players hit teach it too. It keeps up to 12 pictures per map (a monster facing or moving differently needs a picture of its own); when it learns a new one, the picture that went longest without finding anything goes. **Forget learned monsters** (Calibrate tab) deletes the pictures.

  Once it knows a map's pictures, it stops to look for movement only after 4 seconds with no monster in sight.
- **Choosing.** It goes for the monster that's quickest to get in range: one in range on your level first, then one further along it, then one on another level. Sure monsters (an HP bar or a picture) come before things that only moved, and a monster with another player right beside it comes last, since they'll likely kill it first. It sticks with its target unless another is much quicker to reach.
- **Other levels.** With no monster on your level, it climbs or drops to the nearest level that has one, along the paths it learned (see **Pathfinding**), or straight there (up a rope above it, down through the platform below). After 8 seconds without getting there it tries elsewhere for 20 seconds.
- **Following.** Each monster it finds is followed from frame to frame by its picture, so the bot can walk up to it. It stays on the list only while an HP bar, a picture match or movement keeps showing it there, so the bot stops throwing within about a second of a monster dying.
- **Never throwing at nothing.** If something that only moved shows no HP bar after a second of throwing, it's written off, and things that look like it are ignored from then on.
- **Range.** Throwing stars fly 200 px, or 400 px with the Assassin's Keen Eyes maxed (set **Hunt: star range**). They hit up to 124 px above and 68 px below your feet. Inside 65 px the claw punches instead of throwing, so the bot steps back first. These are MapleStory Classic's numbers, from [MeowDB](https://meowdb.com/msclassic/skills/thief/lucky-seven).
- **No monster in sight.** It walks on a little (along the recorded route if there is one, otherwise one way until a wall and then back) and looks again.
- **Skills.** To throw with Lucky Seven instead of the basic attack, set **Hunt: attack key** to its key.
- **Bot view.** **Bot view** (Run tab) shows what the bot sees:
  - you, your reach (yellow box) and the too-close zone;
  - monsters: orange = found by movement, pink = by picture, red = confirmed by an HP bar;
  - the target, drawn thicker, with a line from you to it.

  Tick **Record bot view** to save it as a video in `recordings/`.

A quiet spot works best. In a crowded map other players' tags can hide yours for a moment, and their effects move too.

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
- another player shows up on the minimap (only if you turn on *Pause when another player shows up* in Settings; it is off by default)
- your dot disappears for a few seconds (map change, disconnect)
- a saved popup template appears
- it's been trying to move without the dot moving (stuck)

Press F9 to resume once you've handled it.
