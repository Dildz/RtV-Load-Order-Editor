# RtV Load Order Editor

Standalone Python app that scans your installed Road to Vostok mods and writes
an optimal load order into the Mod Configuration Menu config file.

Must have [Metro Mod Loader](https://modworkshop.net/mod/55623) installed.

WIP - once issues have been ironed out, exe will be released.

## Setup

1. Install Python 3.11+ via https://www.python.org/downloads/ (make sure "Add to PATH" is ticked) or the MS Store (adds to PATH silently)
2. Open a terminal in this folder (.../RtV_LoadOrder_Editor/) and install dependencies:
   ```
   pip install -r requirements.txt
   ```
3. Launch:
   ```
   python main.py
   ```

On first launch the app asks for your `mods` folder (e.g.
`C:\Program Files (x86)\Steam\steamapps\common\Road to Vostok\mods`).
The choice is saved to `app_settings.json` — you won't be asked again unless
the path becomes invalid.

## How it works

1. **Scan** — opens each `.vmz`/`.zip` in the mods folder, reads `mod.txt` for
   metadata, parses every `.gd` script for `extends "res://Scripts/X.gd"` blocks.
2. **Analyze** — for each overridden function, detects whether the mod calls
   `super()`. Builds a dependency graph: any mod that overrides a function
   *with* `super()` must load AFTER any mod that overrides the same function
   *without* `super()` — otherwise the second mod is silently lost.
3. **Recommend** — assigns priority values in steps of 5 to mods without a
   declared priority. Mods that declare `priority=N` in their `mod.txt` are
   locked at that value.
4. **Edit** — manually adjust enabled state, priority value, or order.
5. **Save** — writes back to `%APPDATA%\Road to Vostok\mod_config.cfg`. The
   previous file is rotated into `mod_config.cfg.bak.1` (up to 10 backups
   kept).

## Files

| File | Purpose |
|------|---------|
| `main.py` | Entry point |
| `gui.py` | customtkinter window |
| `paths.py` | Settings, AppData paths, mods-folder dialog |
| `vmz_scanner.py` | Read archives + parse `.gd` overrides |
| `analyzer.py` | Conflict graph + topological sort |
| `config_io.py` | `mod_config.cfg` read/write + rolling backups |
| `_validate.py` | Standalone CLI dump of scan + analyze results |
| `app_settings.json` | Auto-created on first run |

## To Do

- save button needs to save & close - if it's left open & the game opens, & the 'Compatibilty' feature in Mod Loader is used, then the game crashes.
- make the Notes / Warnings txt window bigger by default
- test with more mods, fix bugs & release as an exe
- if 'Clothes to Rags' is installed, & if mod numbers get close to or could exceed 200 - add 100 so that it always loads last
