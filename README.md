# RtV Load Order Editor

Standalone Python app that scans your installed Road to Vostok mods and writes
an optimal load order into the Mod Configuration Menu config file.

Must have [Metro Mod Loader](https://modworkshop.net/mod/55623) installed.
[Mod Configuration Menu](https://modworkshop.net/mod/53713) recommended.

## Update Note

> **v1.1.0+** targets Metro Mod Loader's new profile-based `mod_config.cfg` format (v3.1.0). > If you're still on MML v2, use [v1.0.0](https://github.com/Dildz/RtV-Load-Order-Editor/releases/tag/v1.0.0) instead
> The two formats aren't compatible and this version won't read the old one.

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
`[updates]` block needed for in-game update checks. Use **Rename .zip →
.vmz** to bulk-convert legacy `.zip` archives to the newer `.vmz` extension.

Stale cfg entries — left behind when a mod is updated or removed outside
the editor (e.g. via the in-game loader, which leaves an old
`mod-id@<old-version>` key pointing at a file that's no longer there) —
are dropped automatically on load. Click Save to persist the cleaned cfg.

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
6. **Rename .zip → .vmz** — opens a checklist of every `.zip` mod in the
   folder. Tick the ones to convert and click Rename — originals are copied
   to a `renamed mods` subfolder as backup, then the `.zip` files are
   renamed to `.vmz` in place.
7. **Save** — writes back to `%APPDATA%\Road to Vostok\mod_config.cfg` using
   MML's profile format (`[profile.<active>.enabled]` /
   `[profile.<active>.priority]` keyed by `mod-id@version`, falling back to
   `zip:<filename>` for mods missing either field). Only the active profile
   is read and written — multi-profile editing is out of scope, so entries
   under other profile names are dropped on save. The previous file is
   rotated into `mod_config.cfg.bak.1` (up to 10 backups kept).

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

Releases are built in CI from `.github/workflows/release.yml` — every
`v*` tag pushed to GitHub triggers a Windows build and publishes the exe +
SHA256 to the Releases page.

## To Do

- test with more mods, fix any remaining bugs
- cross-reference vanilla scripts inside `RTV.pck` to catch version-mismatch
  overrides