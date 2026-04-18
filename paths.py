"""Path resolution and persistent app settings."""
from __future__ import annotations

import json
import os
from pathlib import Path
from tkinter import filedialog, messagebox

APP_DIR = Path(__file__).resolve().parent
SETTINGS_FILE = APP_DIR / "app_settings.json"

APPDATA_RTV = Path(os.path.expandvars(r"%APPDATA%\Road to Vostok"))
MOD_CONFIG_FILE = APPDATA_RTV / "mod_config.cfg"


def load_settings() -> dict:
    if SETTINGS_FILE.exists():
        try:
            return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def save_settings(settings: dict) -> None:
    SETTINGS_FILE.write_text(json.dumps(settings, indent=2), encoding="utf-8")


def get_mods_folder() -> Path | None:
    """Return saved mods folder, prompting on first run. None if user cancels."""
    settings = load_settings()
    saved = settings.get("mods_folder")
    if saved and Path(saved).is_dir():
        return Path(saved)

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
