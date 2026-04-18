"""Read/write Godot's mod_config.cfg with quoted-key preservation + rolling backups."""
from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

MAX_BACKUPS = 10
SECTION_RE = re.compile(r'^\s*\[([^\]]+)\]\s*$')
ENTRY_RE = re.compile(r'^\s*("?)([^"=]+)\1\s*=\s*(.+?)\s*$')


@dataclass
class ModConfig:
    """In-memory representation of mod_config.cfg.

    Order of mod entries is preserved. Quoting is auto-applied on write for
    keys containing spaces or other shell-unfriendly chars (matches Godot's
    own behavior).
    """
    enabled: dict[str, bool] = field(default_factory=dict)
    priority: dict[str, int] = field(default_factory=dict)
    order: list[str] = field(default_factory=list)  # insertion order of mod filenames


def _needs_quoting(name: str) -> bool:
    return any(c in name for c in ' \t"=[]')


def _format_key(name: str) -> str:
    return f'"{name}"' if _needs_quoting(name) else name


def _parse_value(raw: str) -> bool | int | str:
    raw = raw.strip()
    if raw.lower() == "true":
        return True
    if raw.lower() == "false":
        return False
    try:
        return int(raw)
    except ValueError:
        return raw


def read_config(path: Path) -> ModConfig:
    cfg = ModConfig()
    if not path.exists():
        return cfg

    section: str | None = None
    seen_filenames: set[str] = set()

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue

        m = SECTION_RE.match(line)
        if m:
            section = m.group(1).strip().lower()
            continue

        e = ENTRY_RE.match(line)
        if not e:
            continue

        key = e.group(2).strip()
        value = _parse_value(e.group(3))

        if section == "enabled" and isinstance(value, bool):
            cfg.enabled[key] = value
        elif section == "priority" and isinstance(value, int):
            cfg.priority[key] = value

        if key not in seen_filenames:
            seen_filenames.add(key)
            cfg.order.append(key)

    return cfg


def _rotate_backups(path: Path) -> None:
    """Shift .bak.N files up by one, drop the oldest, copy current to .bak.1."""
    if not path.exists():
        return

    # Drop the oldest if it exists
    oldest = path.with_suffix(path.suffix + f".bak.{MAX_BACKUPS}")
    if oldest.exists():
        oldest.unlink()

    # Shift .bak.N -> .bak.(N+1) from highest to lowest
    for i in range(MAX_BACKUPS - 1, 0, -1):
        src = path.with_suffix(path.suffix + f".bak.{i}")
        dst = path.with_suffix(path.suffix + f".bak.{i + 1}")
        if src.exists():
            src.rename(dst)

    # Copy current to .bak.1
    shutil.copy2(path, path.with_suffix(path.suffix + ".bak.1"))


def write_config(path: Path, cfg: ModConfig) -> None:
    """Atomically write mod_config.cfg, rotating backups first."""
    _rotate_backups(path)

    lines: list[str] = ["[enabled]", ""]
    for name in cfg.order:
        if name in cfg.enabled:
            lines.append(f"{_format_key(name)}={'true' if cfg.enabled[name] else 'false'}")
    lines.extend(["", "[priority]", ""])
    for name in cfg.order:
        if name in cfg.priority:
            lines.append(f"{_format_key(name)}={cfg.priority[name]}")
    lines.append("")  # trailing newline

    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("\n".join(lines), encoding="utf-8")
    tmp.replace(path)


def sync_with_mods(cfg: ModConfig, mod_filenames: list[str]) -> ModConfig:
    """Ensure cfg has an entry for every mod found on disk.

    - New mods get enabled=True, priority=0 by default.
    - Mods present in cfg but no longer on disk are kept (user might re-add).
    - The order list is updated: existing first (preserved order), new appended.
    """
    for name in mod_filenames:
        if name not in cfg.enabled:
            cfg.enabled[name] = True
        if name not in cfg.priority:
            cfg.priority[name] = 0
        if name not in cfg.order:
            cfg.order.append(name)
    return cfg
