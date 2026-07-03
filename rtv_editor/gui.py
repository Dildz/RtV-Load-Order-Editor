"""customtkinter GUI for RtV load order editor."""
from __future__ import annotations

import shutil
import tkinter as tk
from collections import Counter, defaultdict
from pathlib import Path
from tkinter import messagebox, ttk

import customtkinter as ctk

from .analyzer import (
    ICON_CAUTION, ICON_INFO, ICON_ORDER, ICON_SEVERE,
    MAX_PRIORITY, PRIORITY_START, PRIORITY_STEP, AnalysisResult, analyze,
)
from .config_io import ModConfig, read_config, sync_with_mods, write_config
from .mod_patcher import extract_modworkshop_id, patch_mod_archive
from .paths import (MOD_CONFIG_FILE, get_mods_folder, load_manual_locks,
                    save_manual_locks, verify_mod_config_exists)
from .vmz_scanner import ModInfo, scan_mods_folder

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")

# ── Color palette ──────────────────────────────────────────────────────────
COLOR_BG          = ("#f5f5f7", "#1a1a1a")
COLOR_CARD        = ("#ffffff", "#252525")
COLOR_CARD_HOVER  = ("#f0f0f5", "#2e2e2e")
COLOR_BORDER      = ("#dcdcdc", "#333333")
COLOR_TEXT        = ("#1a1a1a", "#f0f0f0")
COLOR_TEXT_MUTED  = ("#888888", "#888888")
COLOR_TEXT_DIM    = ("gray55", "gray45")
COLOR_WARNING     = ("#b58900", "#e0a000")
COLOR_LOCK        = ("#7a6500", "#c9a227")
COLOR_PRIMARY     = "#2d8f47"  # green for commit/confirm actions (save, rename, update)
COLOR_PRIMARY_HV  = "#3aa055"
COLOR_ACCENT      = "#1f6feb"  # blue for analyze
COLOR_ACCENT_HV   = "#2d7df0"
COLOR_NEUTRAL     = "#3a3a3a"  # grey for secondary/utility buttons
COLOR_NEUTRAL_HV  = "#4a4a4a"
COLOR_DUPE        = "#c94040"  # red — duplicate priority warning
COLOR_DRAG        = "#1f6feb"  # blue border — row being dragged
COLOR_DROP        = "#2d8f47"  # green border — drag drop target

# Dark-mode resolved colors. Plain tk widgets inside ModRow can't accept
# (light, dark) tuples, so we pre-resolve to the dark variant since the app
# forces dark appearance mode. Keep in sync with the tuples above.
_CARD_BG       = COLOR_CARD[1]
_CARD_HOVER_BG = COLOR_CARD_HOVER[1]
_BORDER_BG     = COLOR_BORDER[1]
_TEXT_FG       = COLOR_TEXT[1]
_TEXT_MUTED_FG = COLOR_TEXT_MUTED[1]
_TEXT_DIM_FG   = COLOR_TEXT_DIM[1]
_WARNING_FG    = COLOR_WARNING[1]
_LOCK_FG       = COLOR_LOCK[1]
_ENTRY_BG      = "#1a1a1a"

# ── Fonts ──────────────────────────────────────────────────────────────────
FONT_TITLE   = ("Segoe UI", 18, "bold")
FONT_SECTION = ("Segoe UI", 13, "bold")
FONT_BODY    = ("Segoe UI", 12)
FONT_SMALL   = ("Segoe UI", 11)
FONT_MONO    = ("Consolas", 11)

_INTERACTIVE = (ctk.CTkCheckBox, ctk.CTkButton, ctk.CTkEntry)


class ModRow(tk.Frame):
    """A single mod card — checkbox, name, priority field, up/down arrows.

    Built from plain tk widgets (one CTkCheckBox aside) so the scroll canvas
    only has ~1 nested canvas per row instead of ~5. This is what keeps fast
    scrolling from blanking out — every CTk widget redraws itself on each
    canvas scroll, plain tk widgets don't.
    """

    def __init__(
        self,
        master,
        cfg_key: str,
        display_name: str,
        priority: int,
        enabled: bool,
        locked: bool,
        suggest_disable: bool,
        on_change,
        on_move,
        can_toggle_lock: bool = False,
        on_toggle_lock=None,
    ):
        super().__init__(
            master,
            bg=_CARD_BG,
            highlightthickness=1,
            highlightbackground=_BORDER_BG,
            highlightcolor=_BORDER_BG,
            bd=0,
        )
        self.cfg_key = cfg_key
        self._display_name = display_name
        self.locked = locked
        self.suggest_disable = suggest_disable
        self.on_change = on_change
        self.on_move = on_move
        self._can_toggle_lock = can_toggle_lock
        self._on_toggle_lock = on_toggle_lock
        self._dupe = False

        self.enabled_var = ctk.BooleanVar(value=enabled)
        self.check = ctk.CTkCheckBox(
            self, text="", width=22,
            variable=self.enabled_var,
            command=self._enabled_changed,
        )
        self.check.grid(row=0, column=0, padx=(12, 8), pady=10)

        # Text/colour are set by _apply_visual_state() at the end of __init__ and
        # on every refresh — the lock chip is the sole lock indicator, so the name
        # only carries the ⚠ suggest-disable marker.
        self.label = tk.Label(
            self, text=display_name, anchor="w",
            font=FONT_BODY, fg=_TEXT_FG, bg=_CARD_BG,
        )
        self.label.grid(row=0, column=1, sticky="w", padx=(0, 4))

        self.subtitle = tk.Label(
            self, text=cfg_key, anchor="w",
            font=FONT_SMALL, fg=_TEXT_MUTED_FG, bg=_CARD_BG,
        )
        self.subtitle.grid(row=0, column=2, sticky="w", padx=(0, 8))

        # Lock chip sits just left of the number. Present on every row so the
        # controls line up; greyed and non-interactive on author-declared rows
        # (those can't be user-changed). Discoverable — right-click still works.
        self.lock_btn = self._make_lock_btn(interactive=can_toggle_lock)
        self.lock_btn.grid(row=0, column=3, padx=(8, 6), pady=8)

        self.priority_var = ctk.StringVar(value=str(priority))
        self.priority_entry = tk.Entry(
            self, textvariable=self.priority_var, width=5,
            justify="center", font=FONT_BODY,
            bg=_ENTRY_BG, fg=_TEXT_FG,
            insertbackground=_TEXT_FG,
            relief="flat", bd=0,
            highlightthickness=1,
            highlightbackground=_BORDER_BG,
            highlightcolor=COLOR_ACCENT,
        )
        self.priority_entry.grid(row=0, column=4, padx=(0, 4), pady=8, ipady=5)
        self.priority_entry.bind("<FocusOut>", lambda e: self._priority_changed())
        self.priority_entry.bind("<Return>", lambda e: self._priority_changed())

        self.up_btn = self._make_arrow_btn("▲", lambda: self.on_move(self.cfg_key, -1))
        self.up_btn.grid(row=0, column=5, padx=2, pady=8)
        self.down_btn = self._make_arrow_btn("▼", lambda: self.on_move(self.cfg_key, +1))
        self.down_btn.grid(row=0, column=6, padx=(2, 12), pady=8)

        self.grid_columnconfigure(2, weight=1)

        # Hover effect — subtle lighten on the card
        for w in (self, self.label, self.subtitle):
            w.bind("<Enter>", self._on_hover_in)
            w.bind("<Leave>", self._on_hover_out)

        self._apply_visual_state()  # set name colour/prefix + lock chip

    def _make_arrow_btn(self, text: str, command):
        btn = tk.Label(
            self, text=text, font=FONT_BODY,
            bg=COLOR_NEUTRAL, fg=_TEXT_FG,
            cursor="hand2", padx=8, pady=2,
        )
        btn.bind("<Button-1>", lambda e: command())
        btn.bind("<Enter>", lambda e: btn.configure(bg=COLOR_NEUTRAL_HV))
        btn.bind("<Leave>", lambda e: btn.configure(bg=COLOR_NEUTRAL))
        return btn

    def _make_lock_btn(self, interactive: bool):
        btn = tk.Label(self, font=FONT_BODY, bg=COLOR_NEUTRAL, padx=8, pady=2)
        self._style_lock_btn(btn)
        if interactive:
            btn.configure(cursor="hand2")
            btn.bind("<Button-1>", lambda e: self._on_toggle_lock())
            btn.bind("<Enter>", lambda e: btn.configure(bg=COLOR_NEUTRAL_HV))
            btn.bind("<Leave>", lambda e: btn.configure(bg=COLOR_NEUTRAL))
        return btn

    def _style_lock_btn(self, btn=None):
        """Reflect lock state on the chip. Toggleable rows get colour so they
        read as clickable — blue 🔓 when open, gold 🔒 when locked. Author-declared
        rows show a greyed 🔒 that can't be changed."""
        btn = btn if btn is not None else self.lock_btn
        icon = "🔒" if self.locked else "🔓"
        if not self._can_toggle_lock:
            fg = _TEXT_DIM_FG            # author-declared — greyed, not interactive
        elif self.locked:
            fg = _LOCK_FG               # gold — locked
        else:
            fg = COLOR_ACCENT           # blue — clickable, unlocked
        btn.configure(text=icon, fg=fg)

    def _on_hover_in(self, _):
        self.configure(bg=_CARD_HOVER_BG)
        self.label.configure(bg=_CARD_HOVER_BG)
        self.subtitle.configure(bg=_CARD_HOVER_BG)

    def _on_hover_out(self, _):
        self.configure(bg=_CARD_BG)
        self.label.configure(bg=_CARD_BG)
        self.subtitle.configure(bg=_CARD_BG)

    def _apply_visual_state(self):
        """Set the name colour/prefix and lock chip from the current
        enabled/locked/suggest_disable state. Single source of truth for both
        the first build and every refresh."""
        if not self.enabled_var.get():
            color = _TEXT_DIM_FG
        elif self.suggest_disable:
            color = _WARNING_FG
        elif self.locked:
            color = _LOCK_FG
        else:
            color = _TEXT_FG
        prefix = "⚠ " if self.suggest_disable else ""
        self.label.configure(text=f"{prefix}{self._display_name}", fg=color)
        self._style_lock_btn()

    def refresh(self, priority, enabled, locked, suggest_disable):
        """Update a reused row in place — no widget recreation. Setting the vars
        does not fire the widget callbacks (those fire only on user action)."""
        self.locked = locked
        self.suggest_disable = suggest_disable
        self.enabled_var.set(enabled)
        self.priority_var.set(str(priority))
        self._apply_visual_state()

    def update_lock_state(self, locked: bool):
        self.locked = locked
        self._apply_visual_state()

    def _enabled_changed(self):
        self.on_change(self.cfg_key, "enabled", self.enabled_var.get())

    def _priority_changed(self):
        try:
            v = int(self.priority_var.get())
        except ValueError:
            v = 0
        v = min(v, MAX_PRIORITY)
        self.priority_var.set(str(v))
        self.on_change(self.cfg_key, "priority", v)

    def get_priority(self) -> int:
        try:
            return int(self.priority_var.get())
        except ValueError:
            return 0

    def get_enabled(self) -> bool:
        return self.enabled_var.get()

    def set_priority_dupe(self, is_dupe: bool):
        if is_dupe == self._dupe:
            return
        self._dupe = is_dupe
        if is_dupe:
            self.priority_entry.configure(
                highlightbackground=COLOR_DUPE,
                highlightcolor=COLOR_DUPE,
                highlightthickness=2,
            )
        else:
            self.priority_entry.configure(
                highlightbackground=_BORDER_BG,
                highlightcolor=COLOR_ACCENT,
                highlightthickness=1,
            )


class MissingUpdatesDialog(ctk.CTkToplevel):
    """Modal-ish dialog listing mods missing [updates]/modworkshop id, with
    a URL entry per mod. On Update, extracts the numeric mod id from each URL
    and rewrites the corresponding .vmz with the added lines.
    """

    def __init__(self, master, missing_mods, mods_folder, on_complete):
        super().__init__(master)
        self.title("Missing Update Links")
        self.geometry("760x560")
        self.minsize(620, 360)
        self.configure(fg_color=COLOR_BG)

        self.missing_mods = missing_mods
        self.mods_folder = mods_folder
        self.on_complete = on_complete

        self.url_vars: dict[str, ctk.StringVar] = {}
        self.status_labels: dict[str, ctk.CTkLabel] = {}

        self._build_ui()

        # Focus + stay on top of the main window
        self.after(80, self._grab_focus)

    def _grab_focus(self):
        try:
            self.transient(self.master)
            self.grab_set()
        except Exception:
            pass
        self.lift()
        self.focus_force()

    def _build_ui(self):
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=18, pady=(16, 4))
        ctk.CTkLabel(
            header, text="Missing Update Links",
            font=FONT_TITLE, text_color=COLOR_TEXT, anchor="w",
        ).pack(anchor="w")
        ctk.CTkLabel(
            header,
            text=(
                f"{len(self.missing_mods)} mod(s) have no [updates]/modworkshop line in mod.txt. "
                "Paste each mod's ModWorkshop URL and press Update — the .vmz will be rewritten "
                "with a .bak backup of the original."
            ),
            font=FONT_SMALL, text_color=COLOR_TEXT_MUTED,
            anchor="w", justify="left", wraplength=700,
        ).pack(anchor="w", pady=(2, 0))

        list_frame = ctk.CTkScrollableFrame(self, fg_color="transparent")
        list_frame.pack(fill="both", expand=True, padx=18, pady=(8, 8))

        for mod in self.missing_mods:
            row = ctk.CTkFrame(
                list_frame, fg_color=COLOR_CARD, corner_radius=8,
                border_width=1, border_color=COLOR_BORDER,
            )
            row.pack(fill="x", pady=4)

            name_block = ctk.CTkFrame(row, fg_color="transparent")
            name_block.pack(fill="x", padx=12, pady=(8, 2))
            ctk.CTkLabel(
                name_block, text=mod.display_name, anchor="w",
                font=FONT_BODY, text_color=COLOR_TEXT,
            ).pack(side="left")
            ctk.CTkLabel(
                name_block, text=mod.filename, anchor="w",
                font=FONT_SMALL, text_color=COLOR_TEXT_MUTED,
            ).pack(side="left", padx=(8, 0))

            entry_block = ctk.CTkFrame(row, fg_color="transparent")
            entry_block.pack(fill="x", padx=12, pady=(0, 4))
            var = ctk.StringVar(value="")
            self.url_vars[mod.filename] = var
            entry = ctk.CTkEntry(
                entry_block, textvariable=var,
                placeholder_text="https://modworkshop.net/mod/...",
                height=30, font=FONT_BODY, corner_radius=6,
            )
            entry.pack(fill="x")
            entry.bind("<FocusOut>", lambda e, fn=mod.filename: self._validate_row(fn))

            status = ctk.CTkLabel(
                row, text="", anchor="w",
                font=FONT_SMALL, text_color=COLOR_TEXT_MUTED,
            )
            status.pack(fill="x", padx=12, pady=(0, 8))
            self.status_labels[mod.filename] = status

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.pack(fill="x", padx=18, pady=(0, 14))
        ctk.CTkButton(
            footer, text="Cancel", width=110, height=34,
            corner_radius=8, font=FONT_BODY,
            fg_color=COLOR_NEUTRAL, hover_color=COLOR_NEUTRAL_HV,
            command=self.destroy,
        ).pack(side="right", padx=(4, 0))
        ctk.CTkButton(
            footer, text="Update", width=130, height=34,
            corner_radius=8, font=FONT_BODY,
            fg_color=COLOR_PRIMARY, hover_color=COLOR_PRIMARY_HV,
            command=self._on_update,
        ).pack(side="right")

    def _validate_row(self, filename: str) -> str | None:
        """Show inline status for one row; return extracted id or None."""
        url = self.url_vars[filename].get().strip()
        status = self.status_labels[filename]
        if not url:
            status.configure(text="", text_color=COLOR_TEXT_MUTED)
            return None
        mod_id = extract_modworkshop_id(url)
        if mod_id:
            status.configure(text=f"Detected mod id: {mod_id}", text_color=COLOR_PRIMARY)
            return mod_id
        status.configure(
            text="Could not find a mod id in this URL (expected modworkshop.net/mod/<number>)",
            text_color=COLOR_WARNING,
        )
        return None

    def _on_update(self):
        to_patch: list[tuple[str, str]] = []   # (filename, mod_id)
        has_error = False

        for mod in self.missing_mods:
            url = self.url_vars[mod.filename].get().strip()
            if not url:
                self.status_labels[mod.filename].configure(text="", text_color=COLOR_TEXT_MUTED)
                continue
            mod_id = extract_modworkshop_id(url)
            if not mod_id:
                has_error = True
                self.status_labels[mod.filename].configure(
                    text="Invalid URL — skipped.", text_color=COLOR_WARNING,
                )
                continue
            to_patch.append((mod.filename, mod_id))

        if not to_patch:
            messagebox.showwarning(
                "Nothing to update",
                "No valid ModWorkshop URLs were provided.",
                parent=self,
            )
            return

        success: list[str] = []
        failures: list[tuple[str, str]] = []  # (filename, error)
        for filename, mod_id in to_patch:
            archive = self.mods_folder / filename
            try:
                patch_mod_archive(archive, mod_id)
                success.append(filename)
                self.status_labels[filename].configure(
                    text=f"Patched with modworkshop={mod_id} (backup: {filename}.bak)",
                    text_color=COLOR_PRIMARY,
                )
            except Exception as e:
                failures.append((filename, str(e)))
                self.status_labels[filename].configure(
                    text=f"Failed: {e}", text_color=COLOR_WARNING,
                )

        summary_lines = []
        if success:
            summary_lines.append(f"Patched {len(success)} mod(s).")
        if failures:
            summary_lines.append(f"{len(failures)} failed:")
            summary_lines.extend(f"  - {fn}: {err}" for fn, err in failures)

        messagebox.showinfo(
            "Missing Update Links",
            "\n".join(summary_lines) if summary_lines else "Nothing changed.",
            parent=self,
        )

        if success and not failures and not has_error:
            # Clean exit — refresh main window and close
            self.on_complete()
            self.destroy()
        elif success:
            # Partial — refresh main but leave dialog open so user can see
            # remaining entries
            self.on_complete()


class RenameZipsDialog(ctk.CTkToplevel):
    """Dialog listing .zip mods with per-row checkboxes + select-all. On
    Rename, copies the selected originals to a 'renamed mods' subfolder as
    backup, then renames the .zip files in place to .vmz.
    """

    def __init__(self, master, zip_paths, mods_folder, on_complete):
        super().__init__(master)
        self.title("Rename .zip → .vmz")
        self.geometry("620x520")
        self.minsize(520, 320)
        self.configure(fg_color=COLOR_BG)

        self.zip_paths = zip_paths
        self.mods_folder = mods_folder
        self.on_complete = on_complete
        self.checkbox_vars: dict[str, ctk.BooleanVar] = {}

        self._build_ui()
        self.after(80, self._grab_focus)

    def _grab_focus(self):
        try:
            self.transient(self.master)
            self.grab_set()
        except Exception:
            pass
        self.lift()
        self.focus_force()

    def _build_ui(self):
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=18, pady=(16, 4))
        ctk.CTkLabel(
            header, text="Rename .zip → .vmz",
            font=FONT_TITLE, text_color=COLOR_TEXT, anchor="w",
        ).pack(anchor="w")
        ctk.CTkLabel(
            header,
            text=(
                f"{len(self.zip_paths)} .zip mod(s) found. Tick the ones to "
                "rename, then click Rename. Originals are copied to a "
                "'renamed mods' folder inside your mods folder as backup."
            ),
            font=FONT_SMALL, text_color=COLOR_TEXT_MUTED,
            anchor="w", justify="left", wraplength=560,
        ).pack(anchor="w", pady=(2, 0))

        toggle_bar = ctk.CTkFrame(self, fg_color="transparent")
        toggle_bar.pack(fill="x", padx=18, pady=(8, 0))
        self.select_all_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            toggle_bar, text="Select all",
            variable=self.select_all_var,
            command=self._toggle_all,
            font=FONT_BODY,
        ).pack(anchor="w")

        list_frame = ctk.CTkScrollableFrame(self, fg_color="transparent")
        list_frame.pack(fill="both", expand=True, padx=18, pady=(8, 8))

        for path in self.zip_paths:
            row = ctk.CTkFrame(
                list_frame, fg_color=COLOR_CARD, corner_radius=8,
                border_width=1, border_color=COLOR_BORDER,
            )
            row.pack(fill="x", pady=3)
            var = ctk.BooleanVar(value=True)
            self.checkbox_vars[path.name] = var
            ctk.CTkCheckBox(
                row, text=path.name,
                variable=var, font=FONT_BODY,
                text_color=COLOR_TEXT,
            ).pack(anchor="w", padx=12, pady=8)

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.pack(fill="x", padx=18, pady=(0, 14))
        ctk.CTkButton(
            footer, text="Cancel", width=110, height=34,
            corner_radius=8, font=FONT_BODY,
            fg_color=COLOR_NEUTRAL, hover_color=COLOR_NEUTRAL_HV,
            command=self.destroy,
        ).pack(side="right", padx=(4, 0))
        ctk.CTkButton(
            footer, text="Rename", width=130, height=34,
            corner_radius=8, font=FONT_BODY,
            fg_color=COLOR_PRIMARY, hover_color=COLOR_PRIMARY_HV,
            command=self._on_rename,
        ).pack(side="right")

    def _toggle_all(self):
        value = self.select_all_var.get()
        for var in self.checkbox_vars.values():
            var.set(value)

    def _on_rename(self):
        selected = [p for p in self.zip_paths if self.checkbox_vars[p.name].get()]
        if not selected:
            messagebox.showwarning(
                "Nothing selected",
                "Tick at least one mod to rename.",
                parent=self,
            )
            return

        backup_dir = self.mods_folder / "renamed mods"
        try:
            backup_dir.mkdir(exist_ok=True)
        except Exception as e:
            messagebox.showerror(
                "Could not create backup folder",
                f"{backup_dir}\n\n{e}",
                parent=self,
            )
            return

        success: list[str] = []
        failures: list[tuple[str, str]] = []
        for src in selected:
            try:
                shutil.copy2(src, backup_dir / src.name)
                src.rename(src.with_suffix(".vmz"))
                success.append(src.name)
            except Exception as e:
                failures.append((src.name, str(e)))

        summary = [f"Renamed {len(success)} of {len(selected)} mod(s)."]
        if success:
            summary.append(f"\nOriginals backed up to:\n  {backup_dir}")
        if failures:
            summary.append("\nFailures:")
            summary.extend(f"  - {fn}: {err}" for fn, err in failures)
        messagebox.showinfo("Rename .zip → .vmz", "\n".join(summary), parent=self)

        if success:
            self.on_complete()
            self.destroy()


def _primary_workarea():
    """(left, top, width, height) of the primary monitor's work area, physical
    px — the same source the main window uses, so overlays line up with it
    instead of miscentering via winfo_screenwidth() on multi-monitor/DPI setups."""
    import ctypes

    class _R(ctypes.Structure):
        _fields_ = [("l", ctypes.c_long), ("t", ctypes.c_long),
                    ("r", ctypes.c_long), ("b", ctypes.c_long)]

    r = _R()
    if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(r), 0):
        return r.l, r.t, r.r - r.l, r.b - r.t
    return 0, 0, 1920, 1080


class SplashWindow(tk.Toplevel):
    """Loading window shown while the main window builds.

    Plain tk + ttk.Progressbar — no CTk widgets — so it appears instantly
    without paying CTk's draw-engine setup cost. Stays on top, undecorated,
    centered on screen. Driven by `set_progress(current, total, message)`.
    """

    WIDTH = 420
    HEIGHT = 150

    def __init__(self, master):
        super().__init__(master)
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(bg=_BORDER_BG)  # acts as the 1px outer border

        inner = tk.Frame(self, bg=_CARD_BG)
        inner.pack(fill="both", expand=True, padx=1, pady=1)

        tk.Label(
            inner, text="RtV Load Order Editor",
            font=FONT_TITLE, fg=_TEXT_FG, bg=_CARD_BG,
        ).pack(pady=(22, 4), padx=24)

        self._status = tk.Label(
            inner, text="Starting…",
            font=FONT_SMALL, fg=_TEXT_MUTED_FG, bg=_CARD_BG,
        )
        self._status.pack(pady=(0, 14), padx=24)

        # Style the ttk progressbar to fit the dark theme. The "default" theme
        # honors all of these options; the native "vista"/"xpnative" themes
        # ignore most colors, so we explicitly switch to "default".
        style = ttk.Style(self)
        try:
            style.theme_use("default")
        except tk.TclError:
            pass
        style.configure(
            "Splash.Horizontal.TProgressbar",
            troughcolor=_BORDER_BG,
            background=COLOR_ACCENT,
            bordercolor=_BORDER_BG,
            lightcolor=COLOR_ACCENT,
            darkcolor=COLOR_ACCENT,
            thickness=10,
        )
        self._bar = ttk.Progressbar(
            inner, style="Splash.Horizontal.TProgressbar",
            mode="determinate", length=self.WIDTH - 60, maximum=100,
        )
        self._bar.pack(pady=(0, 22), padx=24)

        # Center on the primary work area (winfo_screenwidth misreports on
        # multi-monitor / scaled displays).
        self.update_idletasks()
        wl, wt, ww, wh = _primary_workarea()
        x = wl + (ww - self.WIDTH) // 2
        y = wt + (wh - self.HEIGHT) // 2
        self.geometry(f"{self.WIDTH}x{self.HEIGHT}+{x}+{y}")

    def set_progress(self, current: int, total: int, message: str = ""):
        if total > 0:
            self._bar["value"] = (current / total) * 100
        if message:
            self._status.configure(text=message)
        self.update()  # force immediate paint while the caller is busy


class BusyOverlay(tk.Toplevel):
    """Small 'working…' overlay centered over the app window. Uses a fixed
    message and an indeterminate bar animated by the event loop — no per-phase
    text swapping (which ghosts on an overrideredirect window) and no fake
    percentages."""

    W, H = 300, 112

    def __init__(self, master, message="Working…"):
        super().__init__(master)
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(bg=_BORDER_BG)  # 1px outer border

        inner = tk.Frame(self, bg=_CARD_BG)
        inner.pack(fill="both", expand=True, padx=1, pady=1)
        tk.Label(
            inner, text=message, font=FONT_BODY, fg=_TEXT_FG, bg=_CARD_BG,
        ).pack(pady=(22, 12), padx=24)

        style = ttk.Style(self)
        try:
            style.theme_use("default")
        except tk.TclError:
            pass
        style.configure(
            "Busy.Horizontal.TProgressbar",
            troughcolor=_BORDER_BG, background=COLOR_ACCENT,
            bordercolor=_BORDER_BG, lightcolor=COLOR_ACCENT,
            darkcolor=COLOR_ACCENT, thickness=8,
        )
        self._bar = ttk.Progressbar(
            inner, style="Busy.Horizontal.TProgressbar",
            mode="indeterminate", length=self.W - 56,
        )
        self._bar.pack(pady=(0, 22), padx=24)
        self._bar.start(12)  # animate via the event loop

        # Center over the app window (not the screen) so it lands correctly
        # regardless of monitor / DPI.
        master.update_idletasks()
        x = master.winfo_rootx() + (master.winfo_width() - self.W) // 2
        y = master.winfo_rooty() + (master.winfo_height() - self.H) // 2
        self.geometry(f"{self.W}x{self.H}+{x}+{y}")

    def close(self):
        try:
            self._bar.stop()
            self.destroy()
        except tk.TclError:
            pass


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("RtV Load Order Editor")
        self.geometry("1000x780")
        self.minsize(820, 560)
        self.configure(fg_color=COLOR_BG)
        self.withdraw()

        self.mods_folder: Path | None = None
        self.scanned_mods: list[ModInfo] = []
        self.cfg: ModConfig = ModConfig()
        self.rows: list[ModRow] = []
        self.rows_by_key: dict[str, ModRow] = {}  # reuse rows across re-analyses
        self.suggest_disable: set[str] = set()
        self.manual_locks: set[str] = set()
        self.dirty = False
        self._drag: dict | None = None
        self._drag_pending: dict | None = None
        self.paned: tk.PanedWindow | None = None
        self._splash: SplashWindow | None = None

        self._build_layout()
        self.after(0, self._initial_load)

    def _build_layout(self):
        # ── Top toolbar ──────────────────────────────────────────────────
        self._toolbar = top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=18, pady=(16, 8))

        title_block = ctk.CTkFrame(top, fg_color="transparent")
        title_block.pack(side="left", fill="y")

        ctk.CTkLabel(
            title_block, text="RtV Load Order Editor",
            font=FONT_TITLE, text_color=COLOR_TEXT, anchor="w",
        ).pack(anchor="w")

        self.status_label = ctk.CTkLabel(
            title_block, text="", font=FONT_SMALL,
            text_color=COLOR_TEXT_MUTED, anchor="w",
        )
        self.status_label.pack(anchor="w", pady=(2, 0))

        button_block = ctk.CTkFrame(top, fg_color="transparent")
        button_block.pack(side="right")

        self.refresh_btn = ctk.CTkButton(
            button_block, text="Refresh", width=80, height=34,
            corner_radius=8, font=FONT_BODY,
            fg_color=COLOR_NEUTRAL, hover_color=COLOR_NEUTRAL_HV,
            command=self._on_refresh,
        )
        self.refresh_btn.pack(side="left", padx=4)

        self.rename_btn = ctk.CTkButton(
            button_block, text="Rename .zip → .vmz", width=140, height=34,
            corner_radius=8, font=FONT_BODY,
            fg_color=COLOR_NEUTRAL, hover_color=COLOR_NEUTRAL_HV,
            command=self._on_rename_zips,
        )
        self.rename_btn.pack(side="left", padx=4)

        self.missing_updates_btn = ctk.CTkButton(
            button_block, text="Missing Update Links", width=150, height=34,
            corner_radius=8, font=FONT_BODY,
            fg_color=COLOR_NEUTRAL, hover_color=COLOR_NEUTRAL_HV,
            command=self._on_missing_updates,
        )
        self.missing_updates_btn.pack(side="left", padx=4)

        self.analyze_btn = ctk.CTkButton(
            button_block, text="Analyze Mods", width=110, height=34,
            corner_radius=8, font=FONT_BODY,
            fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HV,
            command=self._on_analyze,
        )
        self.analyze_btn.pack(side="left", padx=4)

        self.apply_btn = ctk.CTkButton(
            button_block, text="Save & Apply", width=110, height=34,
            corner_radius=8, font=FONT_BODY,
            fg_color=COLOR_PRIMARY, hover_color=COLOR_PRIMARY_HV,
            command=self._on_apply,
        )
        self.apply_btn.pack(side="left", padx=(4, 0))

        # ── Section header above mod list ────────────────────────────────
        list_header = ctk.CTkFrame(self, fg_color="transparent")
        list_header.pack(fill="x", padx=18, pady=(4, 2))
        ctk.CTkLabel(
            list_header, text="Installed Mods",
            font=FONT_SECTION, text_color=COLOR_TEXT, anchor="w",
        ).pack(side="left")
        ctk.CTkLabel(
            list_header,
            text="check = enabled   |   number = load priority (lower loads first)",
            font=FONT_SMALL, text_color=COLOR_TEXT_MUTED, anchor="e",
        ).pack(side="right")

        # ── Bottom status bar ────────────────────────────────────────────
        # Packed FIRST with side="bottom" so it always reserves its slot. If it
        # were packed after the expanding paned window it'd get clipped off the
        # bottom (taking the Notes button with it) whenever the list/notes grow.
        # pack_propagate(False) fixes the height so the button can't be squeezed.
        footer = ctk.CTkFrame(self, fg_color="transparent", height=28)
        footer.pack(side="bottom", fill="x", padx=18, pady=(0, 10))
        footer.pack_propagate(False)
        # Pack the button BEFORE the label: an expand=True label packed first
        # eats the whole footer width and squeezes the button to nothing.
        self.notes_btn = ctk.CTkButton(
            footer, text="Notes ▲", width=64, height=22,
            corner_radius=6, font=FONT_SMALL,
            fg_color=COLOR_NEUTRAL, hover_color=COLOR_NEUTRAL_HV,
            command=self._toggle_notes,
        )
        self.notes_btn.pack(side="right")
        self.footer_label = ctk.CTkLabel(
            footer, text="", font=FONT_SMALL,
            text_color=COLOR_TEXT_MUTED, anchor="w",
        )
        self.footer_label.pack(side="left", fill="x", expand=True)

        # ── Resizable split: mod list / notes ────────────────────────────
        self.paned = tk.PanedWindow(
            self, orient="vertical",
            sashwidth=8, sashrelief="flat",
            bg=COLOR_BG[1], bd=0,
        )
        self.paned.pack(fill="both", expand=True, padx=18, pady=(4, 8))
        # Remember the notes height only when the user drags the sash — NOT on
        # every collapse. Capturing on hide clobbers the saved size during rapid
        # toggling (the pane hasn't re-expanded yet, so it reads the default).
        self.paned.bind("<ButtonRelease-1>", self._on_sash_release)

        # Wrap the scrollable list in a plain frame (PanedWindow can't host
        # a CTkScrollableFrame directly — its internal canvas confuses it).
        list_wrapper = ctk.CTkFrame(self.paned, fg_color="transparent")
        self.list_frame = ctk.CTkScrollableFrame(
            list_wrapper, label_text="", fg_color="transparent",
        )
        self.list_frame.pack(fill="both", expand=True)
        self._setup_smooth_scroll()
        self.paned.add(list_wrapper, minsize=140, stretch="always")

        # Built now but NOT added to the paned window — the notes pane starts
        # collapsed and is shown via the Notes toggle / after Analyze.
        self.notes_container = ctk.CTkFrame(
            self.paned, fg_color=COLOR_CARD,
            corner_radius=10, border_width=1, border_color=COLOR_BORDER,
        )
        ctk.CTkLabel(
            self.notes_container, text="Notes & Warnings",
            font=FONT_SECTION, text_color=COLOR_TEXT, anchor="w",
        ).pack(fill="x", padx=14, pady=(10, 4))
        # Interactive "keep one" cards for heavy-overlap clusters are packed
        # directly above the text notes (CTkTextbox can't host buttons), and
        # tracked so each render can clear the previous set. Packing them into a
        # persistent wrapper is avoided — an empty CTkFrame reserves 200px.
        self.cluster_cards: list = []
        self.notes_box = ctk.CTkTextbox(
            self.notes_container, wrap="word", font=FONT_BODY,
            fg_color="transparent", border_width=0,
        )
        self.notes_box.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self._configure_note_tags()
        self.notes_box.configure(state="disabled")
        self.notes_visible = False
        self.notes_height: int | None = None  # last dragged pane height, px

    # ── lifecycle ────────────────────────────────────────────────────────────

    def _initial_load(self):
        self.mods_folder = get_mods_folder()
        if not self.mods_folder:
            messagebox.showerror("No mods folder", "A mods folder is required. Exiting.")
            self.destroy()
            return

        if not verify_mod_config_exists():
            self.destroy()
            return

        self.manual_locks = load_manual_locks()

        # Splash appears now (after any folder/config prompts) and drives
        # progress through the scan + initial UI build.
        self._splash = SplashWindow(self)
        self._splash.set_progress(0, 1, "Scanning mods…")
        self._load_from_disk(progress_cb=self._splash.set_progress)

    def _load_from_disk(self, progress_cb=None):
        self.scanned_mods = scan_mods_folder(self.mods_folder, progress_cb=progress_cb)
        if progress_cb is not None:
            progress_cb(1, 1, "Building UI…")
        self.cfg = read_config(MOD_CONFIG_FILE)
        sync_with_mods(self.cfg, [m.cfg_key for m in self.scanned_mods])

        # Drop orphan cfg entries (no matching file on disk). Happens when a
        # mod is updated in-game — the old version's cfg_key lingers but the
        # .vmz it referred to has been replaced by the new version, which
        # sync_with_mods adds as a separate entry. The cfg backup (.bak.1)
        # preserves the original on Save in case anything was important.
        on_disk_keys = {m.cfg_key for m in self.scanned_mods}
        orphans = [k for k in self.cfg.order if k not in on_disk_keys]
        for key in orphans:
            self.cfg.enabled.pop(key, None)
            self.cfg.priority.pop(key, None)
        if orphans:
            self.cfg.order = [k for k in self.cfg.order if k not in set(orphans)]

        # Reorder cfg.order so it matches priority value (low → high) for display
        self.cfg.order.sort(key=lambda k: (self.cfg.priority.get(k, 0), k.lower()))

        first_show = not self.winfo_ismapped()
        self._rebuild_rows()
        status = f"{len(self.scanned_mods)} mods loaded"
        if orphans:
            status += f"  |  {len(orphans)} stale entry(s) removed — Save to persist"
        self._set_status(status)
        self.footer_label.configure(text=f"Mods folder:  {self.mods_folder}")
        self.dirty = bool(orphans)

        if first_show:
            # Flush all queued CTk draw callbacks while still withdrawn and
            # hidden behind the splash. CTk widgets schedule their _draw()
            # via after(), so without this the toolbar/buttons would pop in
            # piece by piece after deiconify.
            self.update()
            self.deiconify()
            self.update_idletasks()
            # Size the window to exactly fit the toolbar (title + buttons), so
            # there's no wasted horizontal space beside the button row.
            # Fit the window to the toolbar (title + buttons) with no wasted
            # horizontal space. winfo_reqwidth() is in real (scaled) pixels,
            # but CTk's geometry() re-applies the display scaling — so convert
            # back to logical units first, or it double-scales and grows.
            scaling = self._get_window_scaling()
            # +40 buffer so there's a small gap between the title and the
            # button row instead of them butting right up against each other.
            want_w = int(self._toolbar.winfo_reqwidth() / scaling) + 2 * 18 + 40
            want_w_phys = round(want_w * scaling)
            # Open filling the vertical work area (screen minus taskbar) — the mod
            # list is long and users otherwise drag the window taller every time.
            # Tk's geometry() sets the *client* size, and Windows adds a caption
            # plus ~7px invisible DWM resize borders (DPI-unadjusted), so no
            # client-size math lands cleanly on the work area. Instead ask Windows
            # for the work-area rect (SPI_GETWORKAREA) and size the *outer* window
            # to it via MoveWindow — the invisible border then sits outside the
            # visible frame (tiny bottom gap) rather than clipping past the taskbar.
            import ctypes

            class _RECT(ctypes.Structure):
                _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                            ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

            wa = _RECT()
            ok = ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(wa), 0)
            work_h = wa.bottom - wa.top
            if ok and work_h > 400:
                work_w = wa.right - wa.left
                hwnd = ctypes.windll.user32.GetParent(self.winfo_id())
                x = wa.left + max(0, (work_w - want_w_phys) // 2)
                ctypes.windll.user32.MoveWindow(hwnd, x, wa.top, want_w_phys, work_h, True)
            else:
                # Fallback if the work-area query fails: fit width, leave a margin.
                cur_h = int(self.winfo_screenheight() / scaling) - 80
                self.geometry(f"{want_w}x{cur_h}")
            self.minsize(want_w, 480)
            if self._splash is not None:
                self._splash.destroy()
                self._splash = None

    def _rebuild_rows(self):
        # Reuse existing row widgets across re-analyses — building ~8 CTk widgets
        # per row is the expensive part, so refresh in place and only create /
        # destroy when mods are actually added or removed.
        mods_by_key = {m.cfg_key: m for m in self.scanned_mods}
        new_rows: list[ModRow] = []
        seen: set[str] = set()

        for key in self.cfg.order:
            mod_info = mods_by_key.get(key)
            display_name = mod_info.display_name if mod_info else key
            declared_locked = mod_info.declared_priority is not None if mod_info else False
            can_toggle = not declared_locked
            locked = declared_locked or key in self.manual_locks
            enabled = self.cfg.enabled.get(key, True)
            priority = self.cfg.priority.get(key, 0)
            sug = key in self.suggest_disable

            row = self.rows_by_key.get(key)
            # Reuse only if the structure is unchanged — display name and
            # lock-ability only change on a re-scan, not on Analyze.
            if row is not None and row._display_name == display_name \
                    and row._can_toggle_lock == can_toggle:
                row.refresh(priority, enabled, locked, sug)
            else:
                if row is not None:
                    row.destroy()
                row = ModRow(
                    self.list_frame, cfg_key=key, display_name=display_name,
                    priority=priority, enabled=enabled, locked=locked,
                    suggest_disable=sug, on_change=self._on_row_change,
                    on_move=self._on_row_move, can_toggle_lock=can_toggle,
                    on_toggle_lock=lambda k=key: self._toggle_manual_lock(k),
                )
                self._bind_drag(row)
            new_rows.append(row)
            seen.add(key)

        # Drop rows for mods no longer present.
        for key, row in self.rows_by_key.items():
            if key not in seen:
                row.destroy()

        self.rows = new_rows
        self.rows_by_key = {r.cfg_key: r for r in new_rows}
        self._repack_rows()
        self._check_dupe_priorities()

    def _repack_rows(self):
        """Reorder rows in the scroll frame (pack only — drag bindings are set on creation)."""
        for row in self.rows:
            row.pack_forget()
        for row in self.rows:
            row.pack(fill="x", pady=4)

    # ── actions ──────────────────────────────────────────────────────────────

    def _on_row_change(self, cfg_key: str, field: str, value):
        if field == "enabled":
            self.cfg.enabled[cfg_key] = bool(value)
        elif field == "priority":
            self.cfg.priority[cfg_key] = int(value)
            self._check_dupe_priorities()
        self.dirty = True
        self._set_status("Unsaved changes")

    def _on_row_move(self, cfg_key: str, delta: int):
        try:
            idx = self.cfg.order.index(cfg_key)
        except ValueError:
            return
        new_idx = idx + delta
        if new_idx < 0 or new_idx >= len(self.cfg.order):
            return

        other = self.cfg.order[new_idx]
        p1 = self.cfg.priority.get(cfg_key, 0)
        p2 = self.cfg.priority.get(other, 0)
        self.cfg.priority[cfg_key] = p2
        self.cfg.priority[other] = p1
        self.cfg.order[idx], self.cfg.order[new_idx] = self.cfg.order[new_idx], self.cfg.order[idx]

        # Swap priority displays and row references without a full widget rebuild
        row_a = self.rows[idx]
        row_b = self.rows[new_idx]
        row_a.priority_var.set(str(p2))
        row_b.priority_var.set(str(p1))
        self.rows[idx], self.rows[new_idx] = self.rows[new_idx], self.rows[idx]
        self._repack_rows()

        self.dirty = True
        self._set_status("Unsaved changes")

    def _toggle_manual_lock(self, cfg_key: str):
        if cfg_key in self.manual_locks:
            self.manual_locks.discard(cfg_key)
            locked = False
        else:
            self.manual_locks.add(cfg_key)
            locked = True
        save_manual_locks(self.manual_locks)
        for row in self.rows:
            if row.cfg_key == cfg_key:
                row.update_lock_state(locked)
                break

    # Minimum time the "Analyzing…" overlay stays up, so a near-instant analysis
    # still reads as a deliberate action instead of a flicker. The analyzer runs
    # at full speed — this only holds the overlay, it does NOT slow the work.
    _ANALYZE_MIN_MS = 500

    def _on_analyze(self):
        if not self.scanned_mods:
            messagebox.showwarning("No mods", "Nothing to analyze.")
            return

        overlay = BusyOverlay(self, "Analyzing mods…")
        # Let the bar animate for the min display time with the event loop free,
        # THEN run the (blocking) analysis + UI rebuild and close. The analyser
        # isn't slowed — it just runs after the spinner, so the bar animates
        # smoothly instead of freezing under the synchronous work.
        self.after(self._ANALYZE_MIN_MS, lambda: self._run_analysis(overlay))

    def _run_analysis(self, overlay):
        # Only enabled mods are actually loaded, so only they can conflict —
        # analyse the enabled subset (disabled mods are carried through below).
        enabled = [m for m in self.scanned_mods if self.cfg.enabled.get(m.cfg_key, True)]
        result = analyze(enabled)
        self._apply_recommendation(result)
        overlay.close()

    def _apply_recommendation(self, result: AnalysisResult):
        enabled_order = [r.cfg_key for r in result.recommendations]
        # Disabled mods weren't analysed; keep them in the list (greyed, at 0).
        disabled_keys = [m.cfg_key for m in self.scanned_mods if m.cfg_key not in enabled_order]
        self.cfg.order = enabled_order + disabled_keys
        self.suggest_disable = set(result.suggest_disable)
        self.clusters = result.clusters

        # Snapshot priorities for manually locked mods before renumbering
        preserved = {k: self.cfg.priority[k] for k in self.manual_locks if k in self.cfg.priority}

        # Mods flagged as dead are shown with a ⚠ marker but left enabled — the
        # user disables them (via a cluster card or the checkbox). Auto-disabling
        # here changed the enabled set between analyses, so re-analysing gave a
        # different result and silently dropped cluster cards.

        # Renumber priorities: locked mods keep their declared value, disabled
        # mods get 0 (so they don't waste a number that an enabled mod could use),
        # and enabled mods get sequential values starting at PRIORITY_START.
        locked_values = {r.priority for r in result.recommendations if r.locked}
        next_value = PRIORITY_START
        for r in result.recommendations:
            if r.locked:
                self.cfg.priority[r.cfg_key] = r.priority
                continue
            if not self.cfg.enabled.get(r.cfg_key, True):
                self.cfg.priority[r.cfg_key] = 0
                continue
            while next_value in locked_values:
                next_value += 1
            self.cfg.priority[r.cfg_key] = next_value
            next_value += PRIORITY_STEP

        # Disabled mods aren't in the recommendation list — park them at 0.
        for key in disabled_keys:
            self.cfg.priority[key] = 0

        # Restore manually locked priorities (overrides whatever the analyzer assigned)
        for key, pri in preserved.items():
            self.cfg.priority[key] = pri

        # Re-sort cfg.order to reflect the new priority values
        self.cfg.order.sort(key=lambda k: (self.cfg.priority.get(k, 0), k.lower()))

        # Carry over any cfg-only mods (in cfg but not on disk) at the end
        on_disk = {m.cfg_key for m in self.scanned_mods}
        for k in list(self.cfg.priority.keys()):
            if k not in on_disk and k not in self.cfg.order:
                self.cfg.order.append(k)

        self._rebuild_rows()
        self._show_notes(result)
        self._show_notes_pane(maximize=True)  # auto-open as large as it'll go
        self.dirty = True
        self._set_status("Analysis applied — review and Save")

    def _on_apply(self):
        dupes = self._find_dupe_priorities()
        if dupes:
            lines = "\n".join(
                f"  Priority {p}: {', '.join(keys)}"
                for p, keys in sorted(dupes.items())
            )
            messagebox.showwarning(
                "Duplicate Priorities",
                f"These mods share a priority value — resolve before saving:\n\n{lines}",
            )
            return

        if not messagebox.askyesno(
            "Save mod_config.cfg?",
            f"Write current load order to:\n{MOD_CONFIG_FILE}\n\n"
            "A backup will be created automatically.\n\n"
            "The editor will close after saving — leaving it open while the "
            "game runs the Mod Loader 'Compatibility' check can crash the game.",
        ):
            return
        try:
            write_config(MOD_CONFIG_FILE, self.cfg)
        except Exception as e:
            messagebox.showerror("Save failed", str(e))
            return
        self.dirty = False
        messagebox.showinfo("Saved", "mod_config.cfg has been updated.\nLaunch Road to Vostok to verify.")
        self.destroy()

    def _on_missing_updates(self):
        missing = [m for m in self.scanned_mods if not m.modworkshop_id]
        if not missing:
            messagebox.showinfo(
                "Missing Update Links",
                "Every mod already declares a ModWorkshop update link. Nothing to patch.",
            )
            return
        MissingUpdatesDialog(self, missing, self.mods_folder, self._load_from_disk)

    def _on_refresh(self):
        if self.dirty and not messagebox.askyesno(
            "Discard changes?",
            "You have unsaved changes. Refresh anyway?",
        ):
            return
        self._load_from_disk()

    def _on_rename_zips(self):
        zip_paths = sorted(self.mods_folder.glob("*.zip"))
        if not zip_paths:
            messagebox.showinfo(
                "Rename .zip → .vmz",
                "No .zip mod files found in the mods folder.",
            )
            return
        RenameZipsDialog(self, zip_paths, self.mods_folder, self._load_from_disk)

    # ── drag to reorder ──────────────────────────────────────────────────────

    def _bind_drag(self, row: ModRow):
        for w in (row, row.label, row.subtitle):
            w.bind("<ButtonPress-1>", lambda e, r=row: self._drag_start(e, r), add="+")
            w.bind("<B1-Motion>", self._drag_motion, add="+")
            w.bind("<ButtonRelease-1>", self._drag_end, add="+")

    def _drag_start(self, event, row: ModRow):
        if isinstance(event.widget, _INTERACTIVE):
            return
        if row not in self.rows:
            return
        self._drag_pending = {
            "row": row,
            "src_idx": self.rows.index(row),
            "start_y": event.widget.winfo_rooty() + event.y,
        }

    def _drag_motion(self, event):
        if self._drag_pending and not self._drag:
            y = event.widget.winfo_rooty() + event.y
            if abs(y - self._drag_pending["start_y"]) >= 6:
                p = self._drag_pending
                self._drag_pending = None
                if p["row"] in self.rows:
                    self._drag = {"row": p["row"], "src_idx": p["src_idx"], "cur_target": p["src_idx"]}
                    p["row"].configure(highlightbackground=COLOR_DRAG, highlightcolor=COLOR_DRAG)
        if not self._drag:
            return
        y = event.widget.winfo_rooty() + event.y
        target_idx = self._get_row_at_screen_y(y)
        if target_idx is None:
            return
        prev_target = self._drag["cur_target"]
        if target_idx == prev_target:
            return
        if prev_target != self._drag["src_idx"] and prev_target < len(self.rows):
            self.rows[prev_target].configure(highlightbackground=_BORDER_BG, highlightcolor=_BORDER_BG)
        self._drag["cur_target"] = target_idx
        if target_idx != self._drag["src_idx"]:
            self.rows[target_idx].configure(highlightbackground=COLOR_DROP, highlightcolor=COLOR_DROP)

    def _drag_end(self, event):
        self._drag_pending = None
        if not self._drag:
            return
        drag = self._drag
        self._drag = None
        drag["row"].configure(highlightbackground=_BORDER_BG, highlightcolor=_BORDER_BG)
        target = drag["cur_target"]
        if target != drag["src_idx"] and target < len(self.rows):
            self.rows[target].configure(highlightbackground=_BORDER_BG, highlightcolor=_BORDER_BG)
        if target == drag["src_idx"]:
            return
        self._move_row_to(drag["src_idx"], target)

    def _get_row_at_screen_y(self, y_screen: int) -> int | None:
        for i, row in enumerate(self.rows):
            if not row.winfo_ismapped():
                continue
            ry = row.winfo_rooty()
            rh = row.winfo_height()
            if ry <= y_screen <= ry + rh:
                return i
        return None

    def _move_row_to(self, src_idx: int, target_idx: int):
        lo, hi = min(src_idx, target_idx), max(src_idx, target_idx)

        # Collect and preserve the priority values across the affected range
        keys_in_range = self.cfg.order[lo:hi + 1]
        priority_values = sorted(self.cfg.priority.get(k, 0) for k in keys_in_range)

        key = self.cfg.order.pop(src_idx)
        self.cfg.order.insert(target_idx, key)

        # Redistribute sorted priorities to the new positions
        for i, k in enumerate(self.cfg.order[lo:hi + 1]):
            self.cfg.priority[k] = priority_values[i]

        row = self.rows.pop(src_idx)
        self.rows.insert(target_idx, row)

        for i in range(lo, hi + 1):
            k = self.cfg.order[i]
            self.rows[i].priority_var.set(str(self.cfg.priority.get(k, 0)))

        self._repack_rows()
        self._check_dupe_priorities()
        self.dirty = True
        self._set_status("Unsaved changes")

    # ── priority duplicate detection ─────────────────────────────────────────

    def _check_dupe_priorities(self):
        # Only enabled mods matter for load order — disabled mods all park at 0,
        # so counting them would flag each other as false duplicates.
        counts = Counter(
            self.cfg.priority.get(k, 0)
            for k in self.cfg.order
            if self.cfg.enabled.get(k, True)
        )
        for row in self.rows:
            enabled = self.cfg.enabled.get(row.cfg_key, True)
            p = self.cfg.priority.get(row.cfg_key, 0)
            row.set_priority_dupe(enabled and counts[p] > 1)

    def _find_dupe_priorities(self) -> dict[int, list[str]]:
        groups: dict[int, list[str]] = defaultdict(list)
        for key in self.cfg.order:
            if not self.cfg.enabled.get(key, True):
                continue
            p = self.cfg.priority.get(key, 0)
            groups[p].append(key)
        return {p: keys for p, keys in groups.items() if len(keys) > 1}

    # ── helpers ──────────────────────────────────────────────────────────────

    def _toggle_notes(self):
        if self.notes_visible:
            self._hide_notes_pane()
        else:
            self._show_notes_pane()

    def _show_notes_pane(self, maximize: bool = False):
        """Add the notes pane to the split (if not already) and expand it.
        maximize=True gives notes all the room it can (mod list at its minsize) —
        used after Analyze; otherwise the last dragged height is restored."""
        if not self.notes_visible:
            self.paned.add(self.notes_container, minsize=100, stretch="never")
            self.notes_visible = True
            self.notes_btn.configure(text="Notes ▼")
        self.after(50, lambda: self._expand_notes_pane(maximize))

    def _hide_notes_pane(self):
        """Collapse the notes pane so the mod list gets the full height.
        The height is remembered via _on_sash_release, not captured here."""
        if self.notes_visible:
            self.paned.forget(self.notes_container)
            self.notes_visible = False
            self.notes_btn.configure(text="Notes ▲")

    def _on_sash_release(self, _event=None):
        """After a sash drag, store the notes pane height so toggling restores
        the user's chosen size."""
        if self.notes_visible:
            h = self.notes_container.winfo_height()
            if h > 20:  # ignore a not-yet-laid-out pane
                self.notes_height = h

    def _expand_notes_pane(self, maximize: bool = False):
        if not self.notes_visible:
            return
        self.update_idletasks()
        paned_h = self.paned.winfo_height()
        # Keep the mod list usable — never let notes eat below its 140px minsize.
        max_notes = max(100, paned_h - 140)
        if maximize:
            notes_h = max_notes
        else:
            notes_h = min(self.notes_height or max(220, int(paned_h * 0.35)), max_notes)
        self.paned.sash_place(0, 1, paned_h - notes_h)

    def _setup_smooth_scroll(self):
        """Throttle canvas scroll commands to ~60fps so rapid input
        (mouse-wheel storms or fast scrollbar drags) coalesces into one
        canvas redraw per frame instead of flooding it.

        Without throttling, fast scrollbar drag fires `yview("moveto", ...)`
        faster than the canvas can repaint its embedded windows, so exposed
        strips show the canvas bg color until each child catches up — the
        "blank-out" effect.

        - `scroll` ops accumulate (delta is additive)
        - `moveto` ops keep only the latest fraction (target supersedes)
        - Flush job runs on a stable 16ms cadence; not cancelled per-event,
          so a continuous drag stays at 60fps instead of waiting for the
          input to stop.

        The scrollbar's command is also re-pointed at the wrapped yview —
        CTkScrollbar caches the original bound method at init time, so just
        replacing `canvas.yview` on its own would leave drag unthrottled.
        """
        canvas = self.list_frame._parent_canvas
        _orig = canvas.yview

        _pending_scroll = [0]                  # accumulated wheel units
        _pending_moveto: list[float | None] = [None]  # latest target fraction
        _scroll_units = ["units"]
        _job: list[str | None] = [None]
        FRAME_MS = 16

        def _flush():
            if _pending_moveto[0] is not None:
                _orig("moveto", _pending_moveto[0])
                _pending_moveto[0] = None
                _pending_scroll[0] = 0          # moveto is absolute; drop pending scroll
            elif _pending_scroll[0]:
                _orig("scroll", _pending_scroll[0], _scroll_units[0])
                _pending_scroll[0] = 0
            _job[0] = None

        def _schedule():
            if _job[0] is None:
                _job[0] = canvas.after(FRAME_MS, _flush)

        def _flushed_yview(op="", *args):
            if not op:
                return _orig()
            if op == "scroll":
                try:
                    _pending_scroll[0] += int(float(args[0]))
                except (ValueError, TypeError, IndexError):
                    return
                if len(args) > 1:
                    _scroll_units[0] = args[1]
                _schedule()
            elif op == "moveto":
                try:
                    _pending_moveto[0] = float(args[0])
                except (ValueError, TypeError, IndexError):
                    return
                _schedule()
            else:
                _orig(op, *args)

        canvas.yview = _flushed_yview
        # Re-point the scrollbar so drag goes through our throttle too.
        self.list_frame._scrollbar.configure(command=_flushed_yview)

    def _configure_note_tags(self):
        """Text tags for the notes box. Tk 8.6 renders emoji monochrome, so we
        color the severity icon + header line ourselves via tag foregrounds."""
        box = self.notes_box._textbox
        bold = (FONT_BODY[0], FONT_BODY[1], "bold")
        header_styles = {
            ICON_SEVERE: COLOR_DUPE,        # red — game won't boot / mod dead
            ICON_CAUTION: COLOR_WARNING[1],  # amber — silent partial loss
            ICON_ORDER: COLOR_ACCENT,        # blue — load-order requirement
            ICON_INFO: _TEXT_MUTED_FG,       # gray — informational
        }
        for icon, color in header_styles.items():
            box.tag_configure(f"hdr_{icon}", foreground=color, font=bold,
                              spacing1=8, spacing3=2)
        box.tag_configure("note_body", foreground=_TEXT_FG, lmargin1=26, lmargin2=26)
        box.tag_configure("note_tech", foreground=_TEXT_DIM_FG,
                          lmargin1=26, lmargin2=26)
        box.tag_configure("note_divider", foreground="#3a3a3a", spacing3=6)
        box.tag_configure("note_section", foreground=_TEXT_MUTED_FG,
                          font=FONT_SECTION, spacing1=6, spacing3=4)

    def _render_clusters(self, clusters):
        """Rebuild the interactive 'keep one' cards above the text notes."""
        for w in self.cluster_cards:
            w.destroy()
        self.cluster_cards = []
        if not clusters:
            return
        names = {m.cfg_key: m.display_name for m in self.scanned_mods}
        for c in clusters:
            self.cluster_cards.append(self._build_cluster_card(c, names))

    def _build_cluster_card(self, cluster, names):
        # The red "border" is a solid outer frame with the card inset 2px inside
        # it — a fill, not a 1px stroke, so nested child frames can't nibble it
        # (which is what breaks CTkFrame's real border_width at high DPI).
        outer = ctk.CTkFrame(self.notes_container, fg_color=COLOR_DUPE, corner_radius=8)
        outer.pack(fill="x", padx=10, pady=(0, 6), before=self.notes_box)
        card = ctk.CTkFrame(outer, fg_color=COLOR_CARD, corner_radius=6)
        card.pack(fill="both", expand=True, padx=2, pady=2)
        ctk.CTkLabel(
            card, anchor="w", font=FONT_SECTION, text_color=COLOR_DUPE,
            text=f"⚔  {len(cluster.members)} mods heavily overlap on "
                 f"{cluster.label}  ·  {cluster.conflict_count} clashes",
        ).pack(fill="x", padx=12, pady=(8, 0))
        ctk.CTkLabel(
            card, anchor="w", font=FONT_SMALL, text_color=COLOR_TEXT_MUTED,
            text="They can't all fully work together — keep one, disable the rest:",
        ).pack(fill="x", padx=12, pady=(0, 4))
        for k in cluster.members:
            self._build_cluster_member_row(card, cluster, k, names)
        ctk.CTkFrame(card, fg_color="transparent", height=4).pack()
        return outer

    def _build_cluster_member_row(self, card, cluster, key, names):
        recommended = key == cluster.recommended_keep
        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=12, pady=2)
        systems = cluster.systems.get(key, [])
        extra = ""
        if systems:
            more = f" +{len(systems) - 3} more" if len(systems) > 3 else ""
            extra = f"   ·   also touches {', '.join(systems[:3])}{more}"
        ctk.CTkLabel(
            row, anchor="w", font=FONT_BODY,
            text_color=(COLOR_TEXT if recommended else COLOR_TEXT_MUTED),
            text=f"{names.get(key, key)}   ({cluster.feature_counts[key]} changes){extra}",
        ).pack(side="left", fill="x", expand=True)
        ctk.CTkButton(
            row, text="Keep (recommended)" if recommended else "Keep",
            width=150 if recommended else 64, height=26, font=FONT_SMALL,
            fg_color=(COLOR_PRIMARY if recommended else COLOR_NEUTRAL),
            hover_color=(COLOR_PRIMARY_HV if recommended else COLOR_NEUTRAL_HV),
            command=lambda k=key: self._resolve_cluster(cluster, k),
        ).pack(side="right")

    def _resolve_cluster(self, cluster, keep_key):
        for k in cluster.members:
            if k != keep_key:
                self.cfg.enabled[k] = False
        self._on_analyze()  # re-analyse enabled subset; the cluster collapses away

    def _show_notes(self, result: AnalysisResult):
        self._render_clusters(result.clusters)
        self.notes_box.configure(state="normal")
        self.notes_box.delete("1.0", "end")

        def render_entry(text: str):
            title, _, body = text.partition("\n")
            self.notes_box.insert("end", title + "\n", f"hdr_{title[:1]}")
            for line in body.splitlines():
                tag = "note_tech" if line.lstrip().startswith("[technical") else "note_body"
                self.notes_box.insert("end", line + "\n", tag)
            self.notes_box.insert("end", "─" * 40 + "\n", "note_divider")

        if result.warnings:
            self.notes_box.insert("end", "MOD CONFLICTS\n", "note_section")
            for w in result.warnings:
                render_entry(w)

        if result.notes:
            self.notes_box.insert("end", "REQUIRED LOAD ORDER\n", "note_section")
            for n in result.notes:
                render_entry(n)

        if not result.warnings and not result.notes:
            self.notes_box.insert("end", "No conflicts detected — your load order is clean.\n",
                                  "note_body")

        self.notes_box.configure(state="disabled")

    def _set_status(self, text: str):
        self.status_label.configure(text=text)


def run():
    app = App()
    app.mainloop()
