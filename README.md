# RtV Load Order Editor

Standalone Python app that scans your installed Road to Vostok mods and writes
an optimal load order into the Mod Configuration Menu config file.

Must have [Metro Mod Loader](https://modworkshop.net/mod/55623) installed.

## Usage

Three ways to run it, pick whichever suits you:

- **Prebuilt exe** — download `RtV_LoadOrder_Editor.exe` from the
  [Releases](https://github.com/Dildz/RtV-Load-Order-Editor/releases) page and
  double-click. No Python needed. First launch is a bit slow while the onefile
  bundle unpacks to a temp dir.
- **From source** — install Python 3.11+ (tick "Add to PATH" or use the MS
  Store), then from the project folder:
  ```
  pip install -r requirements.txt
  python main.py
  ```
- **Build your own exe** — see [Build](#build) below.

On first launch the app asks for your `mods` folder (e.g.
`C:\Program Files (x86)\Steam\steamapps\common\Road to Vostok\mods`). The
choice is saved to `app_settings.json` — you won't be asked again unless the
path becomes invalid.

Typical flow: **Refresh** to scan → **Analyze** to get a recommended order →
adjust enabled state / priority as needed → **Save** to write
`mod_config.cfg`. Use **Missing Update Links** if any mods are missing the
`[updates]` block needed for in-game update checks.

## How it works

1. **Scan** — opens each `.vmz`/`.zip` in the mods folder and extracts:
   - `mod.txt` metadata: `name`, `id`, `version`, `priority`, `[autoload]`
     entries (including `!`-prefix restart-pass autoloads), and
     `[updates]`/`modworkshop=<id>` if present
   - Every `.gd` script's `extends "res://Scripts/X.gd"` base, `class_name`
     declaration, and per-function `super()` usage
   - `take_over_path()` targets — resolved three ways: literal string args,
     `parent.resource_path` patterns, and script-named callees (fallback).
     Only scripts that actually call `take_over_path` are flagged, not every
     `.gd` in a mod that happens to contain one somewhere
   - Any reference to `res://ModConfigurationMenu/` (soft dependency on MCM)
   - The full list of files shipped by each archive (for path-collision
     detection)
2. **Analyze** — builds a constraint graph from:
   - **Function chains**: a mod that overrides F *with* `super()` must load
     AFTER any mod that overrides F *without* `super()`, otherwise the second
     mod is silently lost. Takeovers participate in this check too — Godot
     still walks the `extends` chain through a `take_over_path`d script
   - **Takeover ordering**: a mod calling `take_over_path()` on
     `res://Scripts/B.gd` replaces B at that path, so any mod that `extends` B
     must load AFTER the takeover. Multiple mods taking over the same base
     are **not** a hard conflict — they chain through inheritance in load
     order; this is surfaced as an informational note, not a warning
   - **`class_name` collisions**: two mods declaring the same `class_name X`
     is a hard conflict (Godot refuses to load the project). The losing mod
     is suggested for disable
   - **MCM soft dependency**: mods referencing MCM must load after MCM
   - Also detects: duplicate mod IDs, duplicate autoload names, shared file
     paths across archives (higher-priority archive wins at mount)
3. **Recommend** — topologically sorts the graph and assigns priority values
   in steps of 5 to mods without a declared priority. Mods that declare
   `priority=N` in their `mod.txt` are locked at that value.
4. **Edit** — manually adjust enabled state, priority value, or order.
5. **Missing Update Links** — lists mods whose `mod.txt` has no
   `[updates]`/`modworkshop=<id>` block (needed for the in-game loader's
   update check). Paste the mod's ModWorkshop URL per row; the numeric ID is
   extracted, `mod.txt` is patched, and the `.vmz` is rewritten. The original
   archive is kept as `.vmz.bak`.
6. **Save** — writes back to `%APPDATA%\Road to Vostok\mod_config.cfg`. The
   previous file is rotated into `mod_config.cfg.bak.1` (up to 10 backups
   kept).

### Known limits

Detection is static — it can't catch runtime or version-mismatch breakage
(e.g. a mod that targets an older RtV release and crashes regardless of load
order). Scripts packed inside `RTV.pck` aren't cross-referenced yet, so
overrides of removed/renamed engine scripts may slip through.

## Files

| File | Purpose |
|------|---------|
| `main.py` | Entry point |
| `gui.py` | customtkinter window |
| `paths.py` | Settings, AppData paths, mods-folder dialog |
| `vmz_scanner.py` | Read archives + parse `.gd` overrides |
| `analyzer.py` | Conflict graph + topological sort |
| `mod_patcher.py` | Extract ModWorkshop ID + rewrite `.vmz` with patched `mod.txt` |
| `config_io.py` | `mod_config.cfg` read/write + rolling backups |
| `_validate.py` | Standalone CLI dump of scan + analyze results |
| `app_settings.json` | Auto-created on first run |

## Build

Produces `dist/RtV_LoadOrder_Editor.exe` (single-file, windowed, ~18 MB).

```
pip install pyinstaller pillow
python -c "from PIL import Image; Image.open('RtV_LoE.png').save('RtV_LoE.ico', sizes=[(16,16),(32,32),(48,48),(64,64),(128,128),(256,256)])"
pyinstaller --noconfirm --onefile --windowed --name "RtV_LoadOrder_Editor" --icon RtV_LoE.ico --collect-all customtkinter main.py
```

`--collect-all customtkinter` is required — customtkinter ships JSON theme
assets that PyInstaller won't pick up otherwise.

## To Do

- test with more mods, fix remaining bugs
- cross-reference vanilla scripts inside `RTV.pck` to catch version-mismatch
  overrides