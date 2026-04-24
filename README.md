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

1. **Scan** — opens each `.vmz`/`.zip` in the mods folder and extracts:
   - `mod.txt` metadata: `name`, `id`, `version`, `priority`, `[autoload]`
     entries (including `!`-prefix restart-pass autoloads)
   - Every `.gd` script's `extends "res://Scripts/X.gd"` base and per-function
     `super()` usage
   - Any use of `take_over_path()` (treated as a full replacement of the
     extended base script)
   - Any reference to `res://ModConfigurationMenu/` (soft dependency on MCM)
   - The full list of files shipped by each archive (for path-collision
     detection)
2. **Analyze** — builds a constraint graph from:
   - **Function chains**: a mod that overrides F *with* `super()` must load
     AFTER any mod that overrides F *without* `super()`, otherwise the second
     mod is silently lost
   - **Takeover replacements**: a mod calling `take_over_path()` on
     `res://Scripts/B.gd` fully replaces B, so any mod that `extends` B must
     load AFTER the takeover; two takeovers on the same base is a conflict —
     only the highest-priority one sticks
   - **MCM soft dependency**: mods referencing MCM must load after MCM
   - Also detects: duplicate mod IDs, duplicate autoload names, shared file
     paths across archives (higher-priority archive wins at mount)
3. **Recommend** — topologically sorts the graph and assigns priority values
   in steps of 5 to mods without a declared priority. Mods that declare
   `priority=N` in their `mod.txt` are locked at that value.
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

- test with more mods, fix bugs & release as an exe