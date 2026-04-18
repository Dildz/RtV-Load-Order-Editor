"""Standalone validation: scan the real mods folder and dump scanner+analyzer results.

Run this directly to sanity-check the brain before launching the GUI.
"""
from pathlib import Path

from analyzer import analyze
from config_io import read_config
from paths import MOD_CONFIG_FILE
from vmz_scanner import scan_mods_folder

MODS_FOLDER = Path(r"C:\Program Files (x86)\Steam\steamapps\common\Road to Vostok\mods")


def main() -> None:
    print(f"Scanning: {MODS_FOLDER}\n")
    mods = scan_mods_folder(MODS_FOLDER)
    print(f"Found {len(mods)} mod(s).\n")

    print("=" * 70)
    print("RAW SCAN RESULTS")
    print("=" * 70)
    for m in mods:
        pri = m.declared_priority if m.declared_priority is not None else "(none)"
        print(f"\n  {m.filename}")
        print(f"    name: {m.display_name}")
        print(f"    declared priority: {pri}")
        if m.parse_errors:
            print(f"    errors: {m.parse_errors}")
        for ovr in m.overrides:
            funcs = ", ".join(
                f"{f.name}{'(super)' if f.calls_super else '(NO super)'}"
                for f in ovr.functions
            )
            print(f"    overrides {ovr.base_script}.gd: {funcs}")

    print("\n" + "=" * 70)
    print("ANALYSIS — RECOMMENDED LOAD ORDER")
    print("=" * 70)
    result = analyze(mods)
    for r in result.recommendations:
        lock = " [LOCKED]" if r.locked else ""
        print(f"  {r.priority:>5}  {r.filename}{lock}")
        print(f"          -> {r.reason}")

    if result.warnings:
        print("\n" + "=" * 70)
        print("WARNINGS")
        print("=" * 70)
        for w in result.warnings:
            print(f"  ! {w}")

    if result.notes:
        print("\n" + "=" * 70)
        print("NOTES (ordering constraints)")
        print("=" * 70)
        for n in result.notes:
            print(f"  - {n}")

    print("\n" + "=" * 70)
    print(f"CURRENT mod_config.cfg ({MOD_CONFIG_FILE})")
    print("=" * 70)
    if MOD_CONFIG_FILE.exists():
        cfg = read_config(MOD_CONFIG_FILE)
        for name in cfg.order:
            en = cfg.enabled.get(name, "?")
            pr = cfg.priority.get(name, "?")
            print(f"  {pr:>5}  {name}  enabled={en}")
    else:
        print("  (file not found)")


if __name__ == "__main__":
    main()
