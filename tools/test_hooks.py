"""Phase 1 self-check: replace-hook collision detection.

Run: python tools/test_hooks.py   (no framework — plain asserts)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rtv_editor.analyzer import analyze
from rtv_editor.vmz_scanner import ModInfo


def _mod(name: str, hooks: list[str]) -> ModInfo:
    m = ModInfo(filename=f"{name}.vmz", display_name=name, declared_priority=None,
                mod_id=name.lower(), mod_version="1.0.0")
    m.hook_names = set(hooks)
    return m


def _warns(mods: list[ModInfo]) -> str:
    return "\n".join(analyze(mods).warnings)


# Two mods replacing the same bare hook -> conflict flagged.
w = _warns([_mod("AIOverhaul", ["ai-death"]), _mod("FactionWarfare", ["ai-death"])])
assert "REPLACE" in w and "ai-death" in w, w

# -pre/-post/-callback compose -> never a conflict, even same name.
w = _warns([_mod("A", ["ai-death-post"]), _mod("B", ["ai-death-post"])])
assert "REPLACE" not in w, w

# Only one mod owns the replace hook -> nothing to collide with.
w = _warns([_mod("A", ["ai-death"]), _mod("B", ["controller-jump"])])
assert "REPLACE" not in w, w

# One replace + one post on the same base -> only one replacer, no collision.
w = _warns([_mod("A", ["ai-death"]), _mod("B", ["ai-death-post"])])
assert "REPLACE" not in w, w

# Three-way replace collision still fires and names all three.
w = _warns([_mod("A", ["lootcontainer-generateloot"]),
            _mod("B", ["lootcontainer-generateloot"]),
            _mod("C", ["lootcontainer-generateloot"])])
assert w.count('"A"') and w.count('"B"') and w.count('"C"'), w

print("phase1 hook checks passed")
