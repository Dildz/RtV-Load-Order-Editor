"""Patch a mod archive's mod.txt to add a [updates] / modworkshop=<id> block.

The in-game mod loader uses this block to check for updates on modworkshop.net.
Some mod authors forget to include it; this module lets the user paste the
mod's ModWorkshop URL and rewrite the archive with the missing lines.
"""
from __future__ import annotations

import re
import zipfile
from pathlib import Path

MODWORKSHOP_URL_RE = re.compile(r'modworkshop\.net/mod/(\d+)', re.IGNORECASE)


def extract_modworkshop_id(url: str) -> str | None:
    """Extract the numeric mod ID from a ModWorkshop URL.

    Accepts variants like:
      https://modworkshop.net/mod/55966
      http://modworkshop.net/mod/55966/
      modworkshop.net/mod/55966?tab=description
      www.modworkshop.net/mod/55966/some-slug

    Returns None if no match.
    """
    if not url:
        return None
    m = MODWORKSHOP_URL_RE.search(url.strip())
    return m.group(1) if m else None


def _apply_updates_patch(text: str, mod_id: str) -> str:
    """Return the mod.txt text with [updates]/modworkshop=<id> ensured.

    - If the [updates] section is absent, append it at the end.
    - If the section exists but the key is missing, insert the key right after
      the section header.
    - Preserves the original line ending style (\\r\\n vs \\n).
    - Assumes caller has verified modworkshop key is not already present.
    """
    eol = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines()

    section_idx = -1
    for i, line in enumerate(lines):
        if line.strip().lower() == "[updates]":
            section_idx = i
            break

    if section_idx == -1:
        # Separate from preceding content with a blank line, matching the
        # formatting convention most mod.txt files already use between sections.
        if lines and lines[-1].strip() != "":
            lines.append("")
        lines.append("[updates]")
        lines.append(f"modworkshop={mod_id}")
    else:
        lines.insert(section_idx + 1, f"modworkshop={mod_id}")

    return eol.join(lines) + eol


def patch_mod_archive(archive: Path, mod_id: str) -> Path:
    """Rewrite the archive with mod.txt patched to include modworkshop=<id>.

    Returns the path to the backup file (original archive renamed to .bak).
    Raises ValueError if the archive has no mod.txt.

    Sequence:
      1. Read all entries into memory (preserves ZipInfo per entry).
      2. Replace mod.txt bytes with the patched version.
      3. Write a new zip to <archive>.tmp.
      4. Move original → <archive>.bak (single backup; prior .bak overwritten).
      5. Rename .tmp → original path.
    """
    with zipfile.ZipFile(archive, "r") as zf_in:
        entries: list[tuple[zipfile.ZipInfo, bytes]] = []
        mod_txt_idx = -1
        for i, info in enumerate(zf_in.infolist()):
            data = b"" if info.is_dir() else zf_in.read(info.filename)
            entries.append((info, data))
            if info.filename.lower().endswith("mod.txt") and mod_txt_idx == -1:
                mod_txt_idx = i

    if mod_txt_idx == -1:
        raise ValueError(f"{archive.name} has no mod.txt")

    info, data = entries[mod_txt_idx]
    original_text = data.decode("utf-8", errors="replace")
    patched_text = _apply_updates_patch(original_text, mod_id)
    entries[mod_txt_idx] = (info, patched_text.encode("utf-8"))

    tmp = archive.with_suffix(archive.suffix + ".tmp")
    if tmp.exists():
        tmp.unlink()
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf_out:
        for info, data in entries:
            # writestr with a ZipInfo preserves compress_type, date_time, and
            # external attributes from the original entry.
            zf_out.writestr(info, data)

    backup = archive.with_suffix(archive.suffix + ".bak")
    if backup.exists():
        backup.unlink()
    archive.rename(backup)
    tmp.rename(archive)
    return backup
