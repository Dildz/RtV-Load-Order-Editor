# RtV Load Order Editor

Standalone Python app that scans your installed Road to Vostok mods, detects
load-order conflicts, and writes an optimal order into Metro Mod Loader's
`mod_config.cfg`.

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

On first launch the app tries to locate the `mods` folder automatically via
Steam's library data (checks all configured Steam library paths, so
multi-drive setups are covered). If it can't find the game it falls back to
a folder picker. Either way the path is saved and you won't be asked again
unless it becomes invalid.

On every launch a small loading window shows the scan progress; the main
window appears fully painted when ready, instead of building piece by piece.

Typical flow: **Refresh** to scan → **Analyze** to get a recommended order →
adjust enabled state / priority as needed → **Save** to write
`mod_config.cfg`. Analyze shows a short progress overlay, then applies the
result. Use **Missing Update Links** if any mods are missing the `[updates]`
block needed for in-game update checks. Use **Rename .zip → .vmz** to
bulk-convert legacy `.zip` archives to the newer `.vmz` extension.

The **?** button by the title opens a **Help** window (usage guide + links);
it also pops up automatically the first time you run a new version.

Conflicts and load-order notes appear in a collapsible **Notes & Warnings**
panel — it opens automatically after Analyze, and the **Notes** button at the
bottom-right toggles it (drag its top edge to resize; the size is remembered).
Each note leads with a severity icon (⛔ won't boot · ⚠ silent loss · 🔢 load
order · ℹ info). When several big mods heavily overlap (e.g. three AI
overhauls that can't coexist), a **"keep one" card** lists them with a Keep
button each — pick one and the rest are disabled for you.

Stale cfg entries — left behind when a mod is updated or removed outside
the editor (e.g. via the in-game loader, which leaves an old
`mod-id@<old-version>` key pointing at a file that's no longer there) —
are dropped automatically on load. Click Save to persist the cleaned cfg.

## How it works

1. **Scan** — opens each `.vmz`/`.zip` in the mods folder and extracts:
   - `mod.txt` metadata: `name`, `id`, `version`, `priority`, `[autoload]`
     entries, `[updates]`/`modworkshop=<id>`, and `[dependencies] required=`
     if present. The opt-in sections `[registry]`, `[hooks]`, and
     `[script_extend]` are detected too
   - Every `.gd` script's `extends "res://Scripts/X.gd"` base, `class_name`
     declaration, and per-function `super()` usage
   - `take_over_path()` targets — resolved three ways: literal string args,
     `parent.resource_path` patterns, and script-named callees (fallback).
     Only scripts that actually call `take_over_path` are flagged, not every
     `.gd` in a mod that happens to contain one somewhere
   - RTVModLib **hook** registrations (`.hook("stem-method", …)`) and
     **registry** writes (`lib.register`/`override`/`patch` on
     `lib.Registry.<KIND>`, plus the `register_weapon`/etc. aggregators)
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
   - **`class_name` conflicts**: two mods declaring the same `class_name X`,
     or a mod declaring one the base game already uses, is a hard conflict
     (Godot refuses to load the project). A mod that `take_over_path`s a
     vanilla `class_name` script is flagged as a crash risk (Godot bug #83542)
   - **Replace-hook collisions**: two mods registering the same bare hook
     (no `-pre`/`-post`/`-callback` suffix) — only the first to load wins,
     the rest are silently rejected. `-pre`/`-post` hooks compose and are left
     alone
   - **Registry conflicts**: two mods `register`/`override`ing the same
     `(registry, id)` — or the same AI zone — and two mods `patch`ing the
     **same field** of the same entry (xEdit-style; different fields compose).
     Also warns when registry calls need a `[registry]` opt-in that's missing
   - **Declared dependencies**: a mod's `[dependencies] required=` entries must
     be installed and load first — a missing dependency is flagged, and the
     load order is constrained so each dependent sits above its dependency
   - **MCM soft dependency**: mods referencing MCM must load after MCM
   - **Heavy-overlap clusters**: when three or more mods all `take_over_path`
     the same base script and collide on many of its functions (e.g. several
     full AI overhauls), they're grouped into one "keep one" recommendation
     instead of a wall of per-function warnings. AI/spawn conflicts are also
     phrased in gameplay terms ("enemy AI", "enemy spawns") rather than raw
     script names
   - Also detects: duplicate mod IDs, duplicate autoload names, shared file
     paths across archives (higher-priority archive wins at mount), and
     archives that fail to scan (corrupt zip / unreadable `mod.txt`)
3. **Recommend** — topologically sorts the graph and assigns priority values
   in steps of 5 to mods without a declared priority. Mods that declare
   `priority=N` in their `mod.txt` are locked at that value. A final pass
   guarantees every mod ends on a **unique** load number (MML breaks ties by
   name, which is unstable across renames), spreading them across the full
   valid range if needed. Only **enabled** mods are analysed — a disabled mod
   isn't loaded, so it can't conflict; mods it would clash with are *flagged*
   for you to disable rather than switched off automatically.
4. **Edit** — manually adjust enabled state, priority value, or order. Each row
   has a **lock chip** (left of the number) to pin its priority so Analyze
   won't move it: blue = click to lock, gold = locked, greyed = locked by the
   mod's author (a declared `priority=`). Lock state is saved to
   `settings.json`.
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
| `rtv_editor/__init__.py` | Package marker + `__version__` |
| `rtv_editor/gui.py` | customtkinter window, Help/overlay windows |
| `rtv_editor/paths.py` | Settings, AppData paths, mods-folder dialog |
| `rtv_editor/vmz_scanner.py` | Read archives + parse `.gd` overrides |
| `rtv_editor/analyzer.py` | Conflict graph + topological sort |
| `rtv_editor/mod_patcher.py` | Extract ModWorkshop ID + rewrite `.vmz` with patched `mod.txt` |
| `rtv_editor/config_io.py` | `mod_config.cfg` read/write + rolling backups |
| `assets/RtV_LoE.ico` | App/exe icon (used by the PyInstaller build) |
| `settings.json` | Auto-created on first run (in AppData) |

## Build

Releases are built in CI from `.github/workflows/release.yml` — every
`v*` tag pushed to GitHub triggers a Windows build and publishes the exe +
SHA256 to the Releases page.

## Roadmap

- **Check for updates** — a button in the Help window that checks the GitHub
  [Releases](https://github.com/Dildz/RtV-Load-Order-Editor/releases) page and
  tells you when a newer version is available.
- **Post-run log analysis** — read the game's `godot.log` after a session to
  show the *real* load order, which overrides actually won, and any script
  errors — the one thing static analysis can't catch.
- Cross-reference vanilla scripts inside `RTV.pck` to catch version-mismatch
  overrides.
- Test with more mods, fix any remaining bugs.