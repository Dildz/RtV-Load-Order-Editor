"""Scan .vmz/.zip mod archives and extract metadata + override info."""
from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

MOD_EXTENSIONS = (".vmz", ".zip")

EXTENDS_RE = re.compile(r'^\s*extends\s+"res://Scripts/([^"]+)\.gd"', re.MULTILINE)
FUNC_DEF_RE = re.compile(r'^\s*func\s+([A-Za-z_]\w*)\s*\(', re.MULTILINE)
CLASS_NAME_RE = re.compile(r'^\s*class_name\s+([A-Za-z_]\w*)', re.MULTILINE)
# take_over_path() with a string literal argument — we can pinpoint the target.
#   script.take_over_path("res://Scripts/Interface.gd")
TAKE_OVER_LITERAL_RE = re.compile(
    r'\btake_over_path\s*\(\s*["\'](res://[^"\']+)["\']'
)
# take_over_path() using a derived parent/base resource_path — the target is
# whatever the script extends. We can't resolve the exact target statically,
# so we treat all of the mod's own `extends` bases as takeover targets.
#   script.take_over_path(parentScript.resource_path)
#   script.take_over_path(parent.resource_path)
#   script.take_over_path(script.get_base_script().resource_path)
TAKE_OVER_DYNAMIC_PARENT_RE = re.compile(
    r'\btake_over_path\s*\(\s*[^)]*'
    r'(?:(?:parent|base)\w*\.resource_path|get_base_script\s*\(\s*\)\.resource_path)',
    re.IGNORECASE,
)
# take_over_path() on a variable whose name contains "script" — still a script
# takeover, target unknown. Same fallback as dynamic-parent. This catches the
# "helper function wraps the call with a passed-in vanilla_path" idiom.
#   script.take_over_path(vanilla_path)
#   compat_script.take_over_path(some_path)
TAKE_OVER_SCRIPT_CALLEE_RE = re.compile(
    r'\b\w*[Ss]cript\w*\s*\.\s*take_over_path\s*\('
)
MCM_REF_RE = re.compile(r'res://ModConfigurationMenu/')
SECTION_RE = re.compile(r'^\s*\[([^\]]+)\]\s*$')
KV_RE = re.compile(r'^\s*([^=\s]+)\s*=\s*(.*)$')

# Archive-internal paths we don't treat as "real" conflict surface. The
# .godot/ tree is engine-generated import cache — every mod has its own
# hashed filenames in there, so real collisions are essentially impossible
# and listing them would just be noise.
IGNORED_PATH_PREFIXES = (".godot/",)

# The MCM mod's declared id. Used to detect whether MCM is installed when a
# mod references res://ModConfigurationMenu/.
MCM_MOD_ID = "doinkoink-mcm"


@dataclass
class FunctionOverride:
    name: str
    calls_super: bool


@dataclass
class ScriptOverride:
    base_script: str  # e.g. "Character" (from res://Scripts/Character.gd)
    functions: list[FunctionOverride] = field(default_factory=list)
    takes_over_base: bool = False  # True if the script calls take_over_path()


@dataclass
class ModInfo:
    filename: str                  # e.g. "HoldBreath.vmz"
    display_name: str              # from mod.txt name=, fallback to filename
    declared_priority: int | None  # from mod.txt priority=, None if absent
    mod_id: str | None = None
    autoloads: dict[str, str] = field(default_factory=dict)   # name -> res:// path
    restart_autoloads: list[str] = field(default_factory=list)  # autoload names with '!' prefix
    file_paths: list[str] = field(default_factory=list)        # res:// paths shipped by archive
    uses_mcm: bool = False          # references res://ModConfigurationMenu/ in any script
    modworkshop_id: str | None = None  # from [updates] modworkshop=<id>, None if section/key absent
    overrides: list[ScriptOverride] = field(default_factory=list)
    parse_errors: list[str] = field(default_factory=list)
    class_names: list[str] = field(default_factory=list)           # `class_name X` declarations
    takeover_targets: set[str] = field(default_factory=set)        # base script names (e.g. "Character") this mod replaces


def _strip_quotes(s: str) -> str:
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ('"', "'"):
        return s[1:-1]
    return s


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


def _parse_mod_txt(text: str) -> dict[str, dict[str, str]]:
    """Return a section-keyed dict of raw key/value pairs from mod.txt.

    Example: {"mod": {"name": "Hold Breath", "id": "hold-breath", ...},
              "autoload": {"Main": "res://HoldBreath/Main.gd"}}

    Keys keep their original case (autoload node names are case-sensitive).
    Values have quotes stripped.
    """
    sections: dict[str, dict[str, str]] = {}
    current: str | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue
        m = SECTION_RE.match(line)
        if m:
            current = m.group(1).strip().lower()
            sections.setdefault(current, {})
            continue
        if current is None:
            continue
        kv = KV_RE.match(line)
        if not kv:
            continue
        key = kv.group(1).strip()
        val = _strip_quotes(kv.group(2))
        sections[current][key] = val
    return sections


def _extract_mod_meta(sections: dict[str, dict[str, str]]) -> tuple[
    str | None, int | None, str | None
]:
    """Pull (display_name, declared_priority, mod_id) from parsed mod.txt.

    A declared priority of 0 is treated as unset — many mod authors include
    `priority=0` as a placeholder rather than an intentional value, so we let
    the analyzer place these mods freely.
    """
    mod = sections.get("mod", {})
    name = mod.get("name") or None
    mod_id = mod.get("id") or None

    pri: int | None = None
    if "priority" in mod:
        try:
            pri = int(mod["priority"])
        except ValueError:
            pri = None
        if pri == 0:
            pri = None
    return name, pri, mod_id


def _extract_updates_id(sections: dict[str, dict[str, str]]) -> str | None:
    """Pull modworkshop id from [updates] section; None if absent/empty."""
    val = sections.get("updates", {}).get("modworkshop")
    return val.strip() if val and val.strip() else None


def _extract_autoloads(
    sections: dict[str, dict[str, str]],
) -> tuple[dict[str, str], list[str]]:
    """Return (autoloads, restart_autoloads).

    Metro Mod Loader supports a '!' prefix on autoload names to request a
    pre-game-autoload restart pass. We strip the prefix in the returned dict
    and record the original names separately so callers can warn about it.
    """
    raw = sections.get("autoload", {})
    autoloads: dict[str, str] = {}
    restart: list[str] = []
    for name, path in raw.items():
        clean = name.lstrip("!")
        if clean != name:
            restart.append(clean)
        autoloads[clean] = path
    return autoloads, restart


def _archive_to_res_path(name: str) -> str:
    """Convert an archive-internal path to its res:// form.

    Mod archives are mounted with their root as res://, so
    "HoldBreath/Main.gd" -> "res://HoldBreath/Main.gd".
    """
    return f"res://{name.replace(chr(92), '/')}"


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
                    sections = _parse_mod_txt(text)
                    name, pri, mod_id = _extract_mod_meta(sections)
                    if name:
                        info.display_name = name
                    info.declared_priority = pri
                    info.mod_id = mod_id
                    info.autoloads, info.restart_autoloads = _extract_autoloads(sections)
                    info.modworkshop_id = _extract_updates_id(sections)
                except Exception as e:
                    info.parse_errors.append(f"mod.txt: {e}")
            else:
                info.parse_errors.append("no mod.txt found")

            # File manifest — everything the archive ships, minus engine cache.
            # Directories end with "/" in zipfile.namelist(); skip them.
            for n in names:
                if n.endswith("/"):
                    continue
                if any(n.startswith(p) for p in IGNORED_PATH_PREFIXES):
                    continue
                # mod.txt is per-mod metadata, not a conflict surface
                if n.lower().endswith("mod.txt"):
                    continue
                info.file_paths.append(_archive_to_res_path(n))

            # .gd files — scan each for overrides + MCM refs + take_over_path + class_name
            literal_targets: set[str] = set()         # exact Scripts/X base names
            script_takeover_detected = False          # any script-targeted take_over_path
            class_names: set[str] = set()
            for n in names:
                if not n.lower().endswith(".gd"):
                    continue
                try:
                    src = zf.read(n).decode("utf-8", errors="replace")
                    override = _parse_gd_file(src)
                    if override:
                        info.overrides.append(override)
                    if not info.uses_mcm and MCM_REF_RE.search(src):
                        info.uses_mcm = True
                    for lm in TAKE_OVER_LITERAL_RE.finditer(src):
                        target = lm.group(1)
                        if target.startswith("res://Scripts/") and target.endswith(".gd"):
                            literal_targets.add(target[len("res://Scripts/"):-len(".gd")])
                    if not script_takeover_detected and (
                        TAKE_OVER_DYNAMIC_PARENT_RE.search(src)
                        or TAKE_OVER_SCRIPT_CALLEE_RE.search(src)
                    ):
                        script_takeover_detected = True
                    for cm in CLASS_NAME_RE.finditer(src):
                        class_names.add(cm.group(1))
                except Exception as e:
                    info.parse_errors.append(f"{n}: {e}")

            info.class_names = sorted(class_names)

            # Build the exact set of base scripts this mod takes over:
            #   - Literal "res://Scripts/X.gd" args → X is pinpoint-targeted.
            #   - Any script-targeted take_over_path with a dynamic arg →
            #     can't pinpoint; assume every base this mod extends is a
            #     target (matches the common Main.gd bootstrap idiom that
            #     iterates over the mod's extending scripts).
            # Non-script take_over_path calls (e.g. icon.take_over_path on a
            # texture path) no longer false-positive.
            info.takeover_targets = set(literal_targets)
            if script_takeover_detected:
                info.takeover_targets.update(ovr.base_script for ovr in info.overrides)
            for ovr in info.overrides:
                ovr.takes_over_base = ovr.base_script in info.takeover_targets
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
