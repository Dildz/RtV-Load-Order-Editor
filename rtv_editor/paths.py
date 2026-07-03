"""Path resolution and persistent app settings."""
from __future__ import annotations

import json
import os
from pathlib import Path
from tkinter import filedialog, messagebox

APPDATA_RTV = Path(os.path.expandvars(r"%APPDATA%\Road to Vostok"))
MOD_CONFIG_FILE = APPDATA_RTV / "mod_config.cfg"

APPDATA_APP = Path(os.path.expandvars(r"%APPDATA%\RtV-Load-Order-Editor"))
SETTINGS_FILE = APPDATA_APP / "settings.json"


def load_settings() -> dict:
    if SETTINGS_FILE.exists():
        try:
            return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def save_settings(settings: dict) -> None:
    APPDATA_APP.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(settings, indent=2), encoding="utf-8")


def load_manual_locks() -> set[str]:
    return set(load_settings().get("manual_locks", []))


def save_manual_locks(locks: set[str]) -> None:
    settings = load_settings()
    settings["manual_locks"] = sorted(locks)
    save_settings(settings)


def load_help_seen_version() -> str | None:
    """App version whose Help window the user has already seen, or None."""
    return load_settings().get("help_seen_version")


def save_help_seen_version(version: str) -> None:
    settings = load_settings()
    settings["help_seen_version"] = version
    save_settings(settings)


def _detect_steam_mods_folder() -> Path | None:
    """Search every Steam library folder for Road to Vostok's mods directory."""
    import re
    import winreg

    steam_path: Path | None = None
    for hive, subkey in [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam"),
        (winreg.HKEY_CURRENT_USER,  r"SOFTWARE\Valve\Steam"),
    ]:
        try:
            with winreg.OpenKey(hive, subkey) as key:
                val, _ = winreg.QueryValueEx(key, "InstallPath")
                p = Path(val)
                if p.is_dir():
                    steam_path = p
                    break
        except OSError:
            continue

    if not steam_path:
        return None

    vdf = steam_path / "steamapps" / "libraryfolders.vdf"
    if not vdf.exists():
        return None

    try:
        text = vdf.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    # Collect all library paths; Steam's own dir is always a library too.
    libraries: list[Path] = [steam_path]
    for m in re.finditer(r'"path"\s+"([^"]+)"', text):
        p = Path(m.group(1).replace("\\\\", "\\"))
        if p.is_dir() and p not in libraries:
            libraries.append(p)

    game_mods = Path("steamapps") / "common" / "Road to Vostok" / "mods"
    for lib in libraries:
        candidate = lib / game_mods
        if candidate.is_dir():
            return candidate

    return None


def get_mods_folder() -> Path | None:
    """Return saved mods folder. On first run, tries Steam auto-detection before
    falling back to a folder picker dialog. None if nothing is found/chosen."""
    settings = load_settings()
    saved = settings.get("mods_folder")
    if saved and Path(saved).is_dir():
        return Path(saved)

    try:
        detected = _detect_steam_mods_folder()
    except Exception:
        detected = None

    if detected:
        settings["mods_folder"] = str(detected)
        save_settings(settings)
        return detected

    chosen = filedialog.askdirectory(
        title="Select your Road to Vostok 'mods' folder",
        mustexist=True,
    )
    if not chosen:
        return None

    chosen_path = Path(chosen)
    settings["mods_folder"] = str(chosen_path)
    save_settings(settings)
    return chosen_path


def verify_mod_config_exists() -> bool:
    if not MOD_CONFIG_FILE.exists():
        messagebox.showerror(
            "mod_config.cfg not found",
            f"Expected file not found:\n{MOD_CONFIG_FILE}\n\n"
            "Launch Road to Vostok at least once with mods installed so the game can create it.",
        )
        return False
    return True
