"""Build conflict graph from scanned mods and produce a recommended load order.

Rules:
- Lower priority value loads first; higher value loads later (sits on top of the chain).
- A mod that overrides function F WITHOUT calling super() breaks the chain — any
  earlier mod's version of F is invisible.
- Therefore: if mod A overrides F without super, and mod B overrides F WITH super,
  B must load AFTER A (B gets higher priority value). Otherwise B is silently lost.
- Two mods both overriding F without super = conflict. Severity depends on how
  much of each mod is broken when it loses (see _consequence below).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from vmz_scanner import ModInfo

PRIORITY_STEP = 5
PRIORITY_START = 5

# A locked mod (one with a declared priority in mod.txt) usually picks a high
# number because the author wants it to load last. If many other mods crowd up
# to that value, the intended separation is lost. When a locked mod is the
# top-priority mod AND another mod's priority gets within LOCKED_BUMP_BUFFER
# of it, bump the locked mod by LOCKED_BUMP_AMOUNT to restore the gap.
LOCKED_BUMP_BUFFER = 20
LOCKED_BUMP_AMOUNT = 100

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


@dataclass
class Recommendation:
    """One mod's recommended state in the proposed load order."""
    filename: str
    display_name: str
    priority: int
    locked: bool          # True if priority came from mod.txt declaration
    reason: str           # human-readable explanation


@dataclass
class AnalysisResult:
    recommendations: list[Recommendation]
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    suggest_disable: list[str] = field(default_factory=list)  # mod filenames


def _build_constraints(
    mods: list[ModInfo],
) -> tuple[dict[str, set[str]], list[str], list[str], list[str]]:
    """Return (edges, warnings, notes, suggest_disable).

    edges[a] = set of mods that must load AFTER a (i.e. a -> b means a loads before b).
    suggest_disable: mod filenames the user should consider disabling (when a
        conflict has no resolvable load order).
    """
    edges: dict[str, set[str]] = {m.filename: set() for m in mods}
    warnings: list[str] = []
    notes: list[str] = []
    suggest_disable: list[str] = []

    name_for = {m.filename: m.display_name for m in mods}
    total_funcs = {
        m.filename: sum(len(ovr.functions) for ovr in m.overrides) for m in mods
    }

    # Group: (base_script, func_name) -> list of (mod_filename, calls_super)
    groups: dict[tuple[str, str], list[tuple[str, bool]]] = {}
    for m in mods:
        for ovr in m.overrides:
            for fn in ovr.functions:
                groups.setdefault((ovr.base_script, fn.name), []).append(
                    (m.filename, fn.calls_super)
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

    free_names = [m.filename for m in free]
    sorted_free, cycle_warnings = _topo_sort(free_names, edges)
    warnings.extend(cycle_warnings)

    locked_values = {m.declared_priority for m in locked}
    locked_priority_by_name = {m.filename: m.declared_priority for m in locked}

    # Assign priorities in steps of PRIORITY_STEP, skipping any value already
    # used by a locked mod to avoid silent collisions.
    recs: list[Recommendation] = []
    for m in locked:
        recs.append(Recommendation(
            filename=m.filename,
            display_name=m.display_name,
            priority=m.declared_priority,
            locked=True,
            reason=f"declared in mod.txt (priority={m.declared_priority})",
        ))

    by_name = {m.filename: m for m in free}
    assigned: dict[str, int] = dict(locked_priority_by_name)
    next_value = PRIORITY_START

    for fname in sorted_free:
        # Bump past any value already used by a locked mod
        while next_value in locked_values:
            next_value += 1

        # If any locked mod must load BEFORE this free mod, ensure our value
        # is greater than the locked mod's value.
        for locked_name, locked_pri in locked_priority_by_name.items():
            if fname in edges.get(locked_name, set()) and next_value <= locked_pri:
                next_value = locked_pri + 1
                while next_value in locked_values:
                    next_value += 1

        m = by_name[fname]
        if not m.overrides:
            reason = "no script overrides — order doesn't matter"
        else:
            touched = sorted({ovr.base_script for ovr in m.overrides})
            reason = f"overrides {', '.join(touched)}"

        recs.append(Recommendation(
            filename=fname,
            display_name=m.display_name,
            priority=next_value,
            locked=False,
            reason=reason,
        ))
        assigned[fname] = next_value
        next_value += PRIORITY_STEP

    # Final sweep: verify every constraint edge is satisfied. Anything still
    # broken (e.g. free mod must load BEFORE a locked mod with a low value) is
    # flagged for manual user fix.
    name_for = {m.filename: m.display_name for m in mods}
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

    # Bump locked mods that are being crowded from below. Process highest-first
    # so a bumped value is reflected in the "others_max" check for the next one.
    locked_recs_desc = sorted(
        (r for r in recs if r.locked), key=lambda r: r.priority, reverse=True,
    )
    for lr in locked_recs_desc:
        others_max = max(
            (r.priority for r in recs if r is not lr), default=0,
        )
        if lr.priority >= others_max and others_max >= lr.priority - LOCKED_BUMP_BUFFER:
            new_priority = max(
                lr.priority + LOCKED_BUMP_AMOUNT,
                others_max + LOCKED_BUMP_AMOUNT,
            )
            notes.append(
                f'"{lr.display_name}" was bumped from {lr.priority} to {new_priority} '
                f'so it stays separated from the other mods (which reach {others_max}) '
                f'and continues to load last.'
            )
            lr.reason = (
                f"{lr.reason}; bumped from {lr.priority} → {new_priority} "
                f"to preserve load-last intent"
            )
            lr.priority = new_priority

    # Sort final list by priority for display
    recs.sort(key=lambda r: (r.priority, r.filename.lower()))

    return AnalysisResult(
        recommendations=recs,
        warnings=warnings,
        notes=notes,
        suggest_disable=suggest_disable,
    )
