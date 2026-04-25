"""Read/write Metro Mod Loader's mod_config.cfg (v1.1+ profile format).

Format:
    [settings]
    developer_mode=false
    active_profile="Default"

    [profile.Default.enabled]
    mod-id@1.0.0=true
    "zip:Some Mod.vmz"=true

    [profile.Default.priority]
    mod-id@1.0.0=0

Mod keys are MML's cfg_key form: `<mod-id>@<version>` for mods declaring
both, or `zip:<filename>` for mods missing either field. Keys with spaces
or special chars get double-quoted on write.
"""
from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

MAX_BACKUPS = 10
SECTION_RE = re.compile(r'^\s*\[([^\]]+)\]\s*$')
ENTRY_RE = re.compile(r'^\s*("?)([^"=]+)\1\s*=\s*(.+?)\s*$')
PROFILE_SECTION_RE = re.compile(r'^profile\.(.+)\.(enabled|priority)$', re.IGNORECASE)

DEFAULT_PROFILE = "Default"


@dataclass
class ModConfig:
    """In-memory representation of mod_config.cfg.

    Only the active profile is loaded; entries from other profiles are
    ignored on read and not written back. Multi-profile editing is out of
    scope.
    """
    enabled: dict[str, bool] = field(default_factory=dict)
    priority: dict[str, int] = field(default_factory=dict)
    order: list[str] = field(default_factory=list)
    developer_mode: bool = False
    active_profile: str = DEFAULT_PROFILE


def _needs_quoting(name: str) -> bool:
    return any(c in name for c in ' \t"=[]')


def _format_key(name: str) -> str:
    return f'"{name}"' if _needs_quoting(name) else name


def _format_value(value: bool | int | str) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    return f'"{value}"'


def _parse_value(raw: str) -> bool | int | str:
    raw = raw.strip()
    if raw.lower() == "true":
        return True
    if raw.lower() == "false":
        return False
    try:
        return int(raw)
    except ValueError:
        if len(raw) >= 2 and raw[0] == raw[-1] == '"':
            return raw[1:-1]
        return raw


def read_config(path: Path) -> ModConfig:
    cfg = ModConfig()
    if not path.exists():
        return cfg

    # Two-pass: settings first to discover active_profile, then load that
    # profile's enabled/priority sections.
    raw_lines = path.read_text(encoding="utf-8").splitlines()

    section: str | None = None
    for raw_line in raw_lines:
        line = raw_line.strip()
        if not line:
            continue
        m = SECTION_RE.match(line)
        if m:
            section = m.group(1).strip()
            continue
        if section and section.lower() == "settings":
            e = ENTRY_RE.match(line)
            if not e:
                continue
            key = e.group(2).strip()
            value = _parse_value(e.group(3))
            if key == "developer_mode" and isinstance(value, bool):
                cfg.developer_mode = value
            elif key == "active_profile" and isinstance(value, str):
                cfg.active_profile = value

    target_enabled = f"profile.{cfg.active_profile}.enabled"
    target_priority = f"profile.{cfg.active_profile}.priority"
    seen_keys: set[str] = set()

    section = None
    for raw_line in raw_lines:
        line = raw_line.strip()
        if not line:
            continue
        m = SECTION_RE.match(line)
        if m:
            section = m.group(1).strip()
            continue
        if section is None:
            continue

        e = ENTRY_RE.match(line)
        if not e:
            continue
        key = e.group(2).strip()
        value = _parse_value(e.group(3))

        section_lower = section.lower()
        if section_lower == target_enabled.lower() and isinstance(value, bool):
            cfg.enabled[key] = value
            if key not in seen_keys:
                seen_keys.add(key)
                cfg.order.append(key)
        elif section_lower == target_priority.lower() and isinstance(value, int):
            cfg.priority[key] = value
            if key not in seen_keys:
                seen_keys.add(key)
                cfg.order.append(key)

    return cfg


def _rotate_backups(path: Path) -> None:
    """Shift .bak.N files up by one, drop the oldest, copy current to .bak.1."""
    if not path.exists():
        return

    oldest = path.with_suffix(path.suffix + f".bak.{MAX_BACKUPS}")
    if oldest.exists():
        oldest.unlink()

    for i in range(MAX_BACKUPS - 1, 0, -1):
        src = path.with_suffix(path.suffix + f".bak.{i}")
        dst = path.with_suffix(path.suffix + f".bak.{i + 1}")
        if src.exists():
            src.rename(dst)

    shutil.copy2(path, path.with_suffix(path.suffix + ".bak.1"))


def write_config(path: Path, cfg: ModConfig) -> None:
    """Atomically write mod_config.cfg, rotating backups first."""
    _rotate_backups(path)

    profile = cfg.active_profile or DEFAULT_PROFILE

    lines: list[str] = [
        "[settings]",
        "",
        f"developer_mode={_format_value(cfg.developer_mode)}",
        f"active_profile={_format_value(profile)}",
        "",
        f"[profile.{profile}.enabled]",
        "",
    ]
    for name in cfg.order:
        if name in cfg.enabled:
            lines.append(f"{_format_key(name)}={_format_value(cfg.enabled[name])}")
    lines.extend(["", f"[profile.{profile}.priority]", ""])
    for name in cfg.order:
        if name in cfg.priority:
            lines.append(f"{_format_key(name)}={_format_value(cfg.priority[name])}")
    lines.append("")

    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("\n".join(lines), encoding="utf-8")
    tmp.replace(path)


def sync_with_mods(cfg: ModConfig, mod_keys: list[str]) -> ModConfig:
    """Ensure cfg has an entry for every mod found on disk.

    - New mods get enabled=True, priority=0.
    - Mods present in cfg but no longer on disk are kept (user might re-add).
    - Order: existing entries preserve position, new ones appended.
    """
    for key in mod_keys:
        if key not in cfg.enabled:
            cfg.enabled[key] = True
        if key not in cfg.priority:
            cfg.priority[key] = 0
        if key not in cfg.order:
            cfg.order.append(key)
    return cfg
