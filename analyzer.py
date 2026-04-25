"""Build conflict graph from scanned mods and produce a recommended load order.

Rules:
- Lower priority value loads first; higher value loads later (sits on top of the chain).
- A mod that overrides function F WITHOUT calling super() breaks the chain — any
  earlier mod's version of F is invisible.
- Therefore: if mod A overrides F without super, and mod B overrides F WITH super,
  B must load AFTER A (B gets higher priority value). Otherwise B is silently lost.
- Two mods both overriding F without super = conflict. Severity depends on how
  much of each mod is broken when it loses (see _consequence below).
- A mod using take_over_path() on res://Scripts/X.gd fully replaces that script
  at runtime. Any mod extending X via `extends` must load AFTER the takeover mod
  or it will inherit from the wrong (vanilla) version. Two takeovers on the same
  base is a hard conflict — only the highest-priority one sticks.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field

from vmz_scanner import MCM_MOD_ID, ModInfo

PRIORITY_STEP = 5
PRIORITY_START = 5

# Every positive-declared locked mod is placed at least this far above the
# next-lower mod, rounded up to a clean multiple, so "load last" locked mods
# don't get crowded as the mod count grows.
LOCKED_BUMP_AMOUNT = 100


def _round_up(n: int, step: int) -> int:
    """Smallest multiple of `step` that is >= n."""
    return ((n + step - 1) // step) * step

# File extensions that are documentation / repo metadata rather than game
# content. Overlaps on these paths don't affect gameplay and would just add
# noise to the warnings list.
NONGAMEPLAY_SUFFIXES = (
    ".md", ".txt", ".rst", ".yml", ".yaml",
    ".gitignore", ".gitattributes", ".license",
    "license", "changelog", "readme",
)

# Plain-English descriptions for Godot lifecycle functions
LIFECYCLE_DESCRIPTIONS = {
    "_ready": "startup code (runs when the character spawns)",
    "_init": "object creation",
    "_process": "per-frame update logic",
    "_physics_process": "per-frame physics update",
    "_input": "input handling",
    "_unhandled_input": "input handling",
}


def _humanize_function(base_script: str, func_name: str) -> str:
    """Convert e.g. ('Character', 'FireAccuracy') -> 'character fire accuracy'."""
    if func_name in LIFECYCLE_DESCRIPTIONS:
        return f"{base_script.lower()} {LIFECYCLE_DESCRIPTIONS[func_name]}"
    # Split CamelCase / snake_case into spaced lowercase
    spaced = re.sub(r'(?<!^)(?=[A-Z])', ' ', func_name).replace('_', ' ').strip()
    return f"{base_script.lower()} {spaced.lower()}"


def _consequence(mod_display_name: str, severity: str) -> str:
    """One-line description of what happens to a mod when it 'loses' a conflict."""
    if severity == "init":
        return (f'"{mod_display_name}" becomes FULLY INACTIVE '
                f'(it needs its startup code to set things up)')
    if severity == "only_feature":
        return (f'"{mod_display_name}" becomes FULLY INACTIVE '
                f'(this is its only feature)')
    return f'"{mod_display_name}" only loses this one feature; everything else still works'


def _severity(func_name: str, total_overrides: int) -> str:
    if func_name in ("_ready", "_init"):
        return "init"
    if total_overrides <= 1:
        return "only_feature"
    return "minor"


def _is_gameplay_path(res_path: str) -> bool:
    """True if the path is a file that actually affects the game.

    Archives often ship README.md / CHANGELOG.md / LICENSE at the root; those
    collisions are real but harmless and would otherwise flood warnings.
    """
    lower = res_path.lower()
    for suf in NONGAMEPLAY_SUFFIXES:
        if lower.endswith(suf):
            return False
    return True


@dataclass
class Recommendation:
    """One mod's recommended state in the proposed load order."""
    cfg_key: str          # mod-id@version (or zip:filename fallback)
    display_name: str
    priority: int
    locked: bool          # True if priority came from mod.txt declaration
    reason: str           # human-readable explanation


@dataclass
class AnalysisResult:
    recommendations: list[Recommendation]
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    suggest_disable: list[str] = field(default_factory=list)  # cfg keys


def _build_constraints(
    mods: list[ModInfo],
) -> tuple[dict[str, set[str]], list[str], list[str], list[str]]:
    """Return (edges, warnings, notes, suggest_disable).

    edges[a] = set of mods that must load AFTER a (i.e. a -> b means a loads before b).
    suggest_disable: cfg keys the user should consider disabling (when a
        conflict has no resolvable load order).
    """
    edges: dict[str, set[str]] = {m.cfg_key: set() for m in mods}
    warnings: list[str] = []
    notes: list[str] = []
    suggest_disable: list[str] = []

    name_for = {m.cfg_key: m.display_name for m in mods}
    total_funcs = {
        m.cfg_key: sum(len(ovr.functions) for ovr in m.overrides) for m in mods
    }

    # ── Duplicate mod IDs ──────────────────────────────────────────────
    # Metro Mod Loader silently drops duplicates; the user must disable one.
    by_id: dict[str, list[str]] = defaultdict(list)
    for m in mods:
        if m.mod_id:
            by_id[m.mod_id].append(m.cfg_key)
    for mid, owners in by_id.items():
        if len(owners) >= 2:
            listed = ", ".join(f'"{name_for[o]}"' for o in owners)
            warnings.append(
                f'Duplicate mod id "{mid}" is used by {listed}. '
                f'The mod loader will only load one — disable the duplicates to choose which one.'
            )
            # All but the first are candidates for disable
            suggest_disable.extend(owners[1:])

    # ── Duplicate class_name declarations ──────────────────────────────
    # In Godot, two scripts sharing `class_name X` cause a project-load
    # error on boot — the game will not launch at all. Treat as hard
    # conflict: keep the biggest mod, suggest disabling the rest.
    by_class: dict[str, list[str]] = defaultdict(list)
    for m in mods:
        for cn in m.class_names:
            by_class[cn].append(m.cfg_key)
    for cn, owners in by_class.items():
        uniq = list(dict.fromkeys(owners))  # preserve order, drop repeats
        if len(uniq) >= 2:
            listed = ", ".join(f'"{name_for[o]}"' for o in uniq)
            keeper = max(uniq, key=lambda n: total_funcs.get(n, 0))
            losers = [o for o in uniq if o != keeper]
            disable_names = ", ".join(f'"{name_for[o]}"' for o in losers)
            warnings.append(
                f'Multiple mods declare `class_name {cn}`: {listed}. '
                f'Godot refuses to load a project with duplicate class names — '
                f'the game will not boot with all of these enabled.\n'
                f'  -> Recommended fix: keep "{name_for[keeper]}" enabled and '
                f'disable {disable_names}.'
            )
            suggest_disable.extend(losers)

    # ── Duplicate autoload names ───────────────────────────────────────
    # If two mods declare the same [autoload] entry (e.g. Main=... or Config=...),
    # only one actually loads. The other's entry point never runs.
    by_autoload: dict[str, list[str]] = defaultdict(list)
    for m in mods:
        for autoload_name in m.autoloads:
            by_autoload[autoload_name].append(m.cfg_key)
    for autoload_name, owners in by_autoload.items():
        if len(owners) >= 2:
            listed = ", ".join(f'"{name_for[o]}"' for o in owners)
            warnings.append(
                f'Multiple mods declare the same autoload name "{autoload_name}": {listed}. '
                f'Only one will actually load — the others\' entry points will silently fail. '
                f'The mod authors should rename to something more specific.'
            )

    # ── File-path overlaps ─────────────────────────────────────────────
    # If two archives ship the same res:// path (e.g. both have their own
    # Character.gd at res://Scripts/Character.gd), the higher-priority one wins
    # at mount time and the other is dropped silently.
    by_path: dict[str, list[str]] = defaultdict(list)
    for m in mods:
        for p in m.file_paths:
            if _is_gameplay_path(p):
                by_path[p].append(m.cfg_key)
    # Collapse per-file overlaps into per-mod-pair overlaps to keep warnings tidy.
    pair_to_paths: dict[tuple[str, ...], list[str]] = defaultdict(list)
    for p, owners in by_path.items():
        if len(owners) >= 2:
            key = tuple(sorted(owners))
            pair_to_paths[key].append(p)
    for owners, paths in pair_to_paths.items():
        listed = ", ".join(f'"{name_for[o]}"' for o in owners)
        if len(paths) == 1:
            detail = paths[0]
        else:
            detail = f"{len(paths)} shared paths (first: {paths[0]})"
        warnings.append(
            f'{listed} ship the same file path: {detail}. '
            f'The highest-priority mod wins; the others\' copy of that file is dropped.'
        )

    # ── Function-level override constraints ────────────────────────────
    # Group: (base_script, func_name) -> list of (mod_filename, calls_super)
    #
    # Takeover overrides participate here too: when multiple mods take over the
    # same base, they form an inheritance chain via their own `extends`, and
    # each mod's function overrides are subject to the same super() resolution
    # rules as any other extender.
    groups: dict[tuple[str, str], list[tuple[str, bool]]] = {}
    for m in mods:
        for ovr in m.overrides:
            for fn in ovr.functions:
                groups.setdefault((ovr.base_script, fn.name), []).append(
                    (m.cfg_key, fn.calls_super)
                )

    for (base, func), members in groups.items():
        if len(members) < 2:
            continue

        nosuper = [name for name, sup in members if not sup]
        withsuper = [name for name, sup in members if sup]

        # Hard constraint: every nosuper mod must load before every super-calling mod
        for ns in nosuper:
            for ws in withsuper:
                if ns != ws:
                    edges[ns].add(ws)
                    notes.append(
                        f'"{name_for[ws]}" must have a HIGHER load order number than '
                        f'"{name_for[ns]}", or "{name_for[ws]}" will stop working in-game.  '
                        f'[technical: both touch {base}.{func}()]'
                    )

        # Conflict: multiple nosuper mods on same function. Strategy:
        #   - If at least one mod can survive losing this (severity=minor),
        #     pick the smallest "would die" mod as the winner and add edges
        #     so it loads last. The survivors only lose this one feature.
        #   - If ALL mods would die when losing, no load order saves them.
        #     Recommend disabling all but the largest mod.
        if len(nosuper) >= 2:
            feature = _humanize_function(base, func)
            severities = {n: _severity(func, total_funcs[n]) for n in nosuper}
            dying = [n for n in nosuper if severities[n] != "minor"]
            survivors = [n for n in nosuper if severities[n] == "minor"]

            recommendation = ""

            if dying and survivors:
                # Asymmetric — pick a winner and enforce it via edges
                winner = min(dying, key=lambda n: total_funcs[n])
                for other in nosuper:
                    if other != winner:
                        edges[other].add(winner)
                recommendation = (
                    f'\n  -> Recommended fix: load "{name_for[winner]}" with the '
                    f'HIGHEST number of these mods, so it wins this conflict. '
                    f'The others only lose this one feature and keep working.'
                )
            elif len(dying) >= 2:
                # All would die — no load order saves them. Suggest disabling.
                keep = max(dying, key=lambda n: total_funcs[n])
                to_disable = [n for n in dying if n != keep]
                suggest_disable.extend(to_disable)
                disable_names = ", ".join(f'"{name_for[n]}"' for n in to_disable)
                recommendation = (
                    f'\n  -> Recommended fix: NO load order will save all of these '
                    f'mods — only one can be active. Suggest disabling {disable_names} '
                    f'and keeping "{name_for[keep]}" enabled (it has the most features).'
                )

            if len(nosuper) == 2:
                header = (f'"{name_for[nosuper[0]]}" and "{name_for[nosuper[1]]}" '
                          f'both change {feature}.')
            else:
                listed = ", ".join(f'"{name_for[n]}"' for n in nosuper)
                header = f'{listed} all change {feature}.'

            consequences = [
                f'    - {_consequence(name_for[n], severities[n])}'
                for n in nosuper
            ]

            warnings.append(
                f'{header} The mod with the highest load order number wins. '
                f'What each mod loses if it has a lower number:\n'
                + "\n".join(consequences)
                + recommendation
                + f'\n  [technical: {base}.{func}()]'
            )

    # ── take_over_path() constraints ───────────────────────────────────
    # A mod T that calls take_over_path on res://Scripts/B.gd fully replaces
    # B at runtime. Any mod E that does `extends "res://Scripts/B.gd"` must
    # load AFTER T, or E's parent class will be resolved against the vanilla
    # (pre-takeover) version and E will silently inherit the wrong thing.
    takeover_mods_by_base: dict[str, list[str]] = defaultdict(list)
    extender_mods_by_base: dict[str, set[str]] = defaultdict(set)
    for m in mods:
        for ovr in m.overrides:
            if ovr.takes_over_base:
                takeover_mods_by_base[ovr.base_script].append(m.cfg_key)
            else:
                extender_mods_by_base[ovr.base_script].add(m.cfg_key)

    for base, tmods in takeover_mods_by_base.items():
        extenders = extender_mods_by_base.get(base, set())
        for t in tmods:
            for e in extenders:
                if e == t:
                    continue
                edges[t].add(e)
                notes.append(
                    f'"{name_for[e]}" must have a HIGHER load order number than '
                    f'"{name_for[t]}", or "{name_for[e]}" will inherit from the wrong '
                    f'(vanilla) version of {base}.gd.  '
                    f'[technical: "{name_for[t]}" replaces res://Scripts/{base}.gd via take_over_path()]'
                )

        # Multiple takeovers on the same base are NOT automatically a conflict.
        # Each mod's script extends res://Scripts/<base>.gd, and when loaded in
        # order they form an inheritance chain through whichever mod's script
        # currently occupies that path. All of them coexist as long as any
        # function they share resolves cleanly via super() — which the
        # function-level analysis above has already emitted edges/warnings for.
        #
        # So: no forced keeper, no "one wins" warning — just an info note so
        # the user knows a chain is forming.
        if len(tmods) >= 2:
            listed = ", ".join(f'"{name_for[t]}"' for t in tmods)
            notes.append(
                f'{listed} all replace res://Scripts/{base}.gd via take_over_path. '
                f'They stack via inheritance — each mod inherits from the one loaded '
                f'before it, so all of their features remain active. '
                f'Any function that multiple of them override without super() is listed '
                f'above as a separate conflict.'
            )

    # ── Mod Configuration Menu soft dependency ─────────────────────────
    # Mods that reference res://ModConfigurationMenu/... need MCM to load
    # before them, otherwise their config UI never appears. MCM ships with
    # priority=-100 so this is usually automatic, but we surface the edge so
    # the final-sweep check can catch unusual user configurations.
    mcm_mod = next((m for m in mods if m.mod_id == MCM_MOD_ID), None)
    if mcm_mod:
        for m in mods:
            if m.uses_mcm and m.cfg_key != mcm_mod.cfg_key:
                edges[mcm_mod.cfg_key].add(m.cfg_key)
    else:
        mcm_users = [m for m in mods if m.uses_mcm]
        if mcm_users:
            listed = ", ".join(f'"{m.display_name}"' for m in mcm_users[:8])
            more = "" if len(mcm_users) <= 8 else f" (+{len(mcm_users) - 8} more)"
            warnings.append(
                f'{len(mcm_users)} mod(s) reference Mod Configuration Menu but MCM is not '
                f'installed: {listed}{more}. Their in-game settings UIs will not appear. '
                f'Install "Mod Configuration Menu" from ModWorkshop to enable them.'
            )

    return edges, warnings, notes, suggest_disable


def _topo_sort(nodes: list[str], edges: dict[str, set[str]]) -> tuple[list[str], list[str]]:
    """Kahn's algorithm. Returns (sorted_nodes, cycle_warnings).

    Tie-breaks alphabetically so output is stable.
    """
    incoming: dict[str, int] = {n: 0 for n in nodes}
    for src, dsts in edges.items():
        if src not in incoming:
            continue
        for d in dsts:
            if d in incoming:
                incoming[d] += 1

    ready = sorted([n for n, c in incoming.items() if c == 0])
    out: list[str] = []
    warnings: list[str] = []

    while ready:
        n = ready.pop(0)
        out.append(n)
        for d in sorted(edges.get(n, set())):
            if d not in incoming:
                continue
            incoming[d] -= 1
            if incoming[d] == 0:
                # insert sorted
                ready.append(d)
                ready.sort()

    if len(out) != len(nodes):
        leftover = [n for n in nodes if n not in out]
        warnings.append(
            f"Conflict cycle detected involving: {', '.join(leftover)}. "
            f"Falling back to alphabetical order for these."
        )
        out.extend(sorted(leftover))

    return out, warnings


def analyze(mods: list[ModInfo]) -> AnalysisResult:
    """Produce a full recommendation set for the given mods."""
    locked: list[ModInfo] = [m for m in mods if m.declared_priority is not None]
    free: list[ModInfo] = [m for m in mods if m.declared_priority is None]

    edges, warnings, notes, suggest_disable = _build_constraints(mods)

    free_keys = [m.cfg_key for m in free]
    sorted_free, cycle_warnings = _topo_sort(free_keys, edges)
    warnings.extend(cycle_warnings)

    # Pre-compute effective priorities for locked mods. Positive-declared
    # locked mods are bumped to LOCKED_BUMP_AMOUNT above the projected free-mod
    # ceiling, rounded up to a clean multiple, cascading upward so each locked
    # mod stays clear of the ones below it. Negative/zero values signal
    # "load early" intent and are left alone.
    estimated_free_max = PRIORITY_START + PRIORITY_STEP * max(len(free) - 1, 0)
    effective_priority: dict[str, int] = {
        m.cfg_key: m.declared_priority for m in locked
    }
    bump_info: dict[str, tuple[int, int]] = {}  # filename -> (original, new)

    positive_locked = sorted(
        (m for m in locked if m.declared_priority > 0),
        key=lambda m: m.declared_priority,
    )
    floor = estimated_free_max
    for m in positive_locked:
        target = _round_up(floor + LOCKED_BUMP_AMOUNT, LOCKED_BUMP_AMOUNT)
        original = effective_priority[m.cfg_key]
        if original < target:
            bump_info[m.cfg_key] = (original, target)
            effective_priority[m.cfg_key] = target
        floor = max(floor, effective_priority[m.cfg_key])

    locked_values = set(effective_priority.values())

    # Build locked recommendations using their effective (possibly bumped) values.
    recs: list[Recommendation] = []
    for m in locked:
        pri = effective_priority[m.cfg_key]
        if m.cfg_key in bump_info:
            original, _ = bump_info[m.cfg_key]
            reason = (
                f"declared in mod.txt (priority={original}); bumped to {pri} "
                f"so it stays above the other mods and continues to load last"
            )
            notes.append(
                f'"{m.display_name}" was bumped from {original} to {pri} '
                f'so it stays separated from the other mods and continues to load last.'
            )
        else:
            reason = f"declared in mod.txt (priority={pri})"
        recs.append(Recommendation(
            cfg_key=m.cfg_key,
            display_name=m.display_name,
            priority=pri,
            locked=True,
            reason=reason,
        ))

    # Assign free-mod priorities in steps of PRIORITY_STEP, skipping any value
    # already used by a locked mod to avoid silent collisions.
    by_name = {m.cfg_key: m for m in free}
    assigned: dict[str, int] = dict(effective_priority)
    next_value = PRIORITY_START

    for key in sorted_free:
        # Bump past any value already used by a locked mod
        while next_value in locked_values:
            next_value += 1

        # If any locked mod must load BEFORE this free mod, ensure our value
        # is greater than the locked mod's effective value. Round up to the
        # next clean PRIORITY_STEP multiple so the free-mod grid stays tidy.
        for locked_key, locked_pri in effective_priority.items():
            if key in edges.get(locked_key, set()) and next_value <= locked_pri:
                next_value = _round_up(locked_pri + 1, PRIORITY_STEP)
                while next_value in locked_values:
                    next_value += 1

        m = by_name[key]
        if not m.overrides:
            reason = "no script overrides — order doesn't matter"
        else:
            touched = sorted({ovr.base_script for ovr in m.overrides})
            reason = f"overrides {', '.join(touched)}"

        recs.append(Recommendation(
            cfg_key=key,
            display_name=m.display_name,
            priority=next_value,
            locked=False,
            reason=reason,
        ))
        assigned[key] = next_value
        next_value += PRIORITY_STEP

    # Final sweep: verify every constraint edge is satisfied. Anything still
    # broken (e.g. free mod must load BEFORE a locked mod with a low value) is
    # flagged for manual user fix.
    name_for = {m.cfg_key: m.display_name for m in mods}
    for src, dsts in edges.items():
        if src not in assigned:
            continue
        for dst in dsts:
            if dst not in assigned:
                continue
            if assigned[src] >= assigned[dst]:
                warnings.append(
                    f'Load order problem: "{name_for[dst]}" (load order {assigned[dst]}) '
                    f'needs a HIGHER number than "{name_for[src]}" (load order {assigned[src]}). '
                    f'Manually change "{name_for[dst]}" to a number greater than {assigned[src]}.'
                )

    # Sort final list by priority (low to high) for display
    recs.sort(key=lambda r: (r.priority, r.cfg_key.lower()))

    return AnalysisResult(
        recommendations=recs,
        warnings=warnings,
        notes=notes,
        suggest_disable=suggest_disable,
    )
