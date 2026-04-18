"""Scan .vmz/.zip mod archives and extract metadata + override info."""
from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

MOD_EXTENSIONS = (".vmz", ".zip")

EXTENDS_RE = re.compile(r'^\s*extends\s+"res://Scripts/([^"]+)\.gd"', re.MULTILINE)
FUNC_DEF_RE = re.compile(r'^\s*func\s+([A-Za-z_]\w*)\s*\(', re.MULTILINE)
PRIORITY_RE = re.compile(r'^\s*priority\s*=\s*(-?\d+)', re.MULTILINE)
NAME_RE = re.compile(r'^\s*name\s*=\s*"([^"]+)"', re.MULTILINE)


@dataclass
class FunctionOverride:
    name: str
    calls_super: bool


@dataclass
class ScriptOverride:
    base_script: str  # e.g. "Character" (from res://Scripts/Character.gd)
    functions: list[FunctionOverride] = field(default_factory=list)


@dataclass
class ModInfo:
    filename: str           # e.g. "HoldBreath.vmz"
    display_name: str       # from mod.txt name=, fallback to filename
    declared_priority: int | None  # from mod.txt priority=, None if absent
    overrides: list[ScriptOverride] = field(default_factory=list)
    parse_errors: list[str] = field(default_factory=list)


def _split_function_bodies(source: str) -> list[tuple[str, str]]:
    """Return list of (func_name, body_text) for each function in source.

    Body extends from after the def line up to the next func def at same/lower
    indent or EOF. We don't try to be perfect — we just need to know if super()
    is called inside.
    """
    matches = list(FUNC_DEF_RE.finditer(source))
    out: list[tuple[str, str]] = []
    for i, m in enumerate(matches):
        name = m.group(1)
        body_start = m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(source)
        out.append((name, source[body_start:body_end]))
    return out


def _has_super_call(body: str, func_name: str) -> bool:
    """Detect a super-call for func_name inside body.

    Godot 4 forms:
      super(args)            — bare, calls same-named parent
      super.FuncName(args)   — explicit
    """
    if re.search(r'\bsuper\s*\(', body):
        return True
    if re.search(rf'\bsuper\s*\.\s*{re.escape(func_name)}\s*\(', body):
        return True
    return False


def _parse_gd_file(source: str) -> ScriptOverride | None:
    """Parse a .gd file. Returns None if it doesn't override a base Scripts/*.gd."""
    extends_match = EXTENDS_RE.search(source)
    if not extends_match:
        return None

    base = extends_match.group(1)  # e.g. "Character"
    funcs: list[FunctionOverride] = []
    for fname, body in _split_function_bodies(source):
        funcs.append(FunctionOverride(name=fname, calls_super=_has_super_call(body, fname)))

    return ScriptOverride(base_script=base, functions=funcs)


def _parse_mod_txt(text: str) -> tuple[str | None, int | None]:
    """Return (display_name, declared_priority) from mod.txt content.

    A declared priority of 0 is treated as unset — many mod authors include
    `priority=0` as a placeholder rather than an intentional value, so we let
    the analyzer place these mods freely.
    """
    name_match = NAME_RE.search(text)
    pri_match = PRIORITY_RE.search(text)
    name = name_match.group(1) if name_match else None
    pri = int(pri_match.group(1)) if pri_match else None
    if pri == 0:
        pri = None
    return name, pri


def scan_archive(path: Path) -> ModInfo:
    """Open one .vmz/.zip and extract mod metadata + script overrides."""
    info = ModInfo(filename=path.name, display_name=path.name, declared_priority=None)

    try:
        with zipfile.ZipFile(path, "r") as zf:
            names = zf.namelist()

            # mod.txt — find it anywhere in the archive (usually at root or one level deep)
            mod_txt_name = next((n for n in names if n.lower().endswith("mod.txt")), None)
            if mod_txt_name:
                try:
                    text = zf.read(mod_txt_name).decode("utf-8", errors="replace")
                    name, pri = _parse_mod_txt(text)
                    if name:
                        info.display_name = name
                    info.declared_priority = pri
                except Exception as e:
                    info.parse_errors.append(f"mod.txt: {e}")
            else:
                info.parse_errors.append("no mod.txt found")

            # .gd files — scan each
            for n in names:
                if not n.lower().endswith(".gd"):
                    continue
                try:
                    src = zf.read(n).decode("utf-8", errors="replace")
                    override = _parse_gd_file(src)
                    if override:
                        info.overrides.append(override)
                except Exception as e:
                    info.parse_errors.append(f"{n}: {e}")
    except zipfile.BadZipFile:
        info.parse_errors.append("not a valid zip archive")
    except Exception as e:
        info.parse_errors.append(f"open failed: {e}")

    return info


def scan_mods_folder(folder: Path) -> list[ModInfo]:
    """Scan all .vmz/.zip files in the given folder. Returns sorted by filename."""
    archives: list[Path] = []
    for ext in MOD_EXTENSIONS:
        archives.extend(folder.glob(f"*{ext}"))
    archives.sort(key=lambda p: p.name.lower())
    return [scan_archive(p) for p in archives]
