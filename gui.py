"""customtkinter GUI for RtV load order editor."""
from __future__ import annotations

import shutil
import tkinter as tk
from pathlib import Path
from tkinter import messagebox

import customtkinter as ctk

from analyzer import PRIORITY_START, PRIORITY_STEP, AnalysisResult, analyze
from config_io import ModConfig, read_config, sync_with_mods, write_config
from mod_patcher import extract_modworkshop_id, patch_mod_archive
from paths import MOD_CONFIG_FILE, get_mods_folder, verify_mod_config_exists
from vmz_scanner import ModInfo, scan_mods_folder

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
COLOR_PRIMARY     = "#2d8f47"  # green for save
COLOR_PRIMARY_HV  = "#3aa055"
COLOR_ACCENT      = "#1f6feb"  # blue for analyze
COLOR_ACCENT_HV   = "#2d7df0"
COLOR_NEUTRAL     = "#3a3a3a"  # grey for refresh
COLOR_NEUTRAL_HV  = "#4a4a4a"
COLOR_TEAL        = "#0d9488"  # teal for rename .zip → .vmz
COLOR_TEAL_HV     = "#14b8a6"
COLOR_PURPLE      = "#7c3aed"  # purple for missing updates
COLOR_PURPLE_HV   = "#8b4dff"

# ── Fonts ──────────────────────────────────────────────────────────────────
FONT_TITLE   = ("Segoe UI", 18, "bold")
FONT_SECTION = ("Segoe UI", 13, "bold")
FONT_BODY    = ("Segoe UI", 12)
FONT_SMALL   = ("Segoe UI", 11)
FONT_MONO    = ("Consolas", 11)


class ModRow(ctk.CTkFrame):
    """A single mod card — checkbox, name, priority field, up/down arrows."""

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
    ):
        super().__init__(
            master,
            fg_color=COLOR_CARD,
            corner_radius=8,
            border_width=1,
            border_color=COLOR_BORDER,
            height=46,
        )
        self.cfg_key = cfg_key
        self.locked = locked
        self.suggest_disable = suggest_disable
        self.on_change = on_change
        self.on_move = on_move

        if not enabled:
            name_color = COLOR_TEXT_DIM
        elif suggest_disable:
            name_color = COLOR_WARNING
        elif locked:
            name_color = COLOR_LOCK
        else:
            name_color = COLOR_TEXT

        self.enabled_var = ctk.BooleanVar(value=enabled)
        self.check = ctk.CTkCheckBox(
            self, text="", width=22,
            variable=self.enabled_var,
            command=self._enabled_changed,
        )
        self.check.grid(row=0, column=0, padx=(12, 8), pady=10)

        prefix = "🔒 " if locked else ("⚠ " if suggest_disable else "")
        self.label = ctk.CTkLabel(
            self,
            text=f"{prefix}{display_name}",
            anchor="w",
            font=FONT_BODY,
            text_color=name_color,
        )
        self.label.grid(row=0, column=1, sticky="w", padx=(0, 4))

        self.subtitle = ctk.CTkLabel(
            self, text=cfg_key, anchor="w",
            font=FONT_SMALL, text_color=COLOR_TEXT_MUTED,
        )
        self.subtitle.grid(row=0, column=2, sticky="w", padx=(0, 8))

        self.priority_var = ctk.StringVar(value=str(priority))
        self.priority_entry = ctk.CTkEntry(
            self, textvariable=self.priority_var, width=70, height=30,
            justify="center", font=FONT_BODY,
            corner_radius=6,
        )
        self.priority_entry.grid(row=0, column=3, padx=(8, 4), pady=8)
        self.priority_entry.bind("<FocusOut>", lambda e: self._priority_changed())
        self.priority_entry.bind("<Return>", lambda e: self._priority_changed())

        self.up_btn = ctk.CTkButton(
            self, text="▲", width=30, height=30,
            corner_radius=6,
            fg_color=COLOR_NEUTRAL, hover_color=COLOR_NEUTRAL_HV,
            command=lambda: self.on_move(self.cfg_key, -1),
        )
        self.up_btn.grid(row=0, column=4, padx=2, pady=8)
        self.down_btn = ctk.CTkButton(
            self, text="▼", width=30, height=30,
            corner_radius=6,
            fg_color=COLOR_NEUTRAL, hover_color=COLOR_NEUTRAL_HV,
            command=lambda: self.on_move(self.cfg_key, +1),
        )
        self.down_btn.grid(row=0, column=5, padx=(2, 12), pady=8)

        self.grid_columnconfigure(2, weight=1)

        # Hover effect — subtle lighten on the card
        for w in (self, self.label, self.subtitle):
            w.bind("<Enter>", self._on_hover_in)
            w.bind("<Leave>", self._on_hover_out)

    def _on_hover_in(self, _):
        self.configure(fg_color=COLOR_CARD_HOVER)

    def _on_hover_out(self, _):
        self.configure(fg_color=COLOR_CARD)

    def _enabled_changed(self):
        self.on_change(self.cfg_key, "enabled", self.enabled_var.get())

    def _priority_changed(self):
        try:
            v = int(self.priority_var.get())
        except ValueError:
            self.priority_var.set("0")
            v = 0
        self.on_change(self.cfg_key, "priority", v)

    def get_priority(self) -> int:
        try:
            return int(self.priority_var.get())
        except ValueError:
            return 0

    def get_enabled(self) -> bool:
        return self.enabled_var.get()


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
            fg_color=COLOR_TEAL, hover_color=COLOR_TEAL_HV,
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


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("RtV Load Order Editor")
        self.geometry("1000x780")
        self.minsize(820, 560)
        self.configure(fg_color=COLOR_BG)

        self.mods_folder: Path | None = None
        self.scanned_mods: list[ModInfo] = []
        self.cfg: ModConfig = ModConfig()
        self.rows: list[ModRow] = []
        self.suggest_disable: set[str] = set()
        self.dirty = False

        self._build_layout()
        self.after(100, self._initial_load)

    def _build_layout(self):
        # ── Top toolbar ──────────────────────────────────────────────────
        top = ctk.CTkFrame(self, fg_color="transparent")
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
            fg_color=COLOR_TEAL, hover_color=COLOR_TEAL_HV,
            command=self._on_rename_zips,
        )
        self.rename_btn.pack(side="left", padx=4)

        self.missing_updates_btn = ctk.CTkButton(
            button_block, text="Missing Update Links", width=150, height=34,
            corner_radius=8, font=FONT_BODY,
            fg_color=COLOR_PURPLE, hover_color=COLOR_PURPLE_HV,
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

        # ── Resizable split: mod list / notes ────────────────────────────
        paned = tk.PanedWindow(
            self, orient="vertical",
            sashwidth=8, sashrelief="flat",
            bg=COLOR_BG[1], bd=0,
        )
        paned.pack(fill="both", expand=True, padx=18, pady=(4, 8))

        # Wrap the scrollable list in a plain frame (PanedWindow can't host
        # a CTkScrollableFrame directly — its internal canvas confuses it).
        list_wrapper = ctk.CTkFrame(paned, fg_color="transparent")
        self.list_frame = ctk.CTkScrollableFrame(
            list_wrapper, label_text="", fg_color="transparent",
        )
        self.list_frame.pack(fill="both", expand=True)
        paned.add(list_wrapper, minsize=140, stretch="always")

        notes_container = ctk.CTkFrame(
            paned, fg_color=COLOR_CARD,
            corner_radius=10, border_width=1, border_color=COLOR_BORDER,
        )
        ctk.CTkLabel(
            notes_container, text="Notes & Warnings",
            font=FONT_SECTION, text_color=COLOR_TEXT, anchor="w",
        ).pack(fill="x", padx=14, pady=(10, 4))
        self.notes_box = ctk.CTkTextbox(
            notes_container, wrap="word", font=FONT_BODY,
            fg_color="transparent", border_width=0,
        )
        self.notes_box.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.notes_box.configure(state="disabled")
        paned.add(notes_container, minsize=100, stretch="never")

        # Set initial split: notes pane gets ~40% of the window height
        def _initial_split():
            self.update_idletasks()
            h = self.winfo_height()
            notes_h = max(320, int(h * 0.40))
            paned.sash_place(0, 1, h - notes_h)
        self.after(80, _initial_split)

        # ── Bottom status bar ────────────────────────────────────────────
        footer = ctk.CTkFrame(self, fg_color="transparent", height=24)
        footer.pack(fill="x", padx=18, pady=(0, 10))
        self.footer_label = ctk.CTkLabel(
            footer, text="", font=FONT_SMALL,
            text_color=COLOR_TEXT_MUTED, anchor="w",
        )
        self.footer_label.pack(side="left", fill="x", expand=True)

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

        self._load_from_disk()

    def _load_from_disk(self):
        self.scanned_mods = scan_mods_folder(self.mods_folder)
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

        self._rebuild_rows()
        status = f"{len(self.scanned_mods)} mods loaded"
        if orphans:
            status += f"  |  {len(orphans)} stale entry(s) removed — Save to persist"
        self._set_status(status)
        self.footer_label.configure(text=f"Mods folder:  {self.mods_folder}")
        self.dirty = bool(orphans)

    def _rebuild_rows(self):
        for row in self.rows:
            row.destroy()
        self.rows = []

        mods_by_key = {m.cfg_key: m for m in self.scanned_mods}

        for key in self.cfg.order:
            mod_info = mods_by_key.get(key)
            display_name = mod_info.display_name if mod_info else key
            locked = mod_info.declared_priority is not None if mod_info else False

            row = ModRow(
                self.list_frame,
                cfg_key=key,
                display_name=display_name,
                priority=self.cfg.priority.get(key, 0),
                enabled=self.cfg.enabled.get(key, True),
                locked=locked,
                suggest_disable=key in self.suggest_disable,
                on_change=self._on_row_change,
                on_move=self._on_row_move,
            )
            row.pack(fill="x", pady=4)
            self.rows.append(row)

    # ── actions ──────────────────────────────────────────────────────────────

    def _on_row_change(self, cfg_key: str, field: str, value):
        if field == "enabled":
            self.cfg.enabled[cfg_key] = bool(value)
        elif field == "priority":
            self.cfg.priority[cfg_key] = int(value)
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

        # Swap priorities with the neighbour, then swap order
        other = self.cfg.order[new_idx]
        p1 = self.cfg.priority.get(cfg_key, 0)
        p2 = self.cfg.priority.get(other, 0)
        self.cfg.priority[cfg_key] = p2
        self.cfg.priority[other] = p1
        self.cfg.order[idx], self.cfg.order[new_idx] = self.cfg.order[new_idx], self.cfg.order[idx]

        self._rebuild_rows()
        self.dirty = True
        self._set_status("Unsaved changes")

    def _on_analyze(self):
        if not self.scanned_mods:
            messagebox.showwarning("No mods", "Nothing to analyze.")
            return

        result = analyze(self.scanned_mods)
        self._apply_recommendation(result)

    def _apply_recommendation(self, result: AnalysisResult):
        self.cfg.order = [r.cfg_key for r in result.recommendations]
        self.suggest_disable = set(result.suggest_disable)

        # Auto-disable mods flagged as dead — user can re-enable manually if desired
        for key in self.suggest_disable:
            self.cfg.enabled[key] = False

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

        # Re-sort cfg.order to reflect the new priority values
        self.cfg.order.sort(key=lambda k: (self.cfg.priority.get(k, 0), k.lower()))

        # Carry over any cfg-only mods (in cfg but not on disk) at the end
        on_disk = {m.cfg_key for m in self.scanned_mods}
        for k in list(self.cfg.priority.keys()):
            if k not in on_disk and k not in self.cfg.order:
                self.cfg.order.append(k)

        self._rebuild_rows()
        self._show_notes(result)
        self.dirty = True
        self._set_status("Analysis applied — review and Save")

    def _on_apply(self):
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

    # ── helpers ──────────────────────────────────────────────────────────────

    def _show_notes(self, result: AnalysisResult):
        self.notes_box.configure(state="normal")
        self.notes_box.delete("1.0", "end")

        if result.warnings:
            self.notes_box.insert("end", "MOD CONFLICTS (some mods may not work)\n")
            for w in result.warnings:
                self.notes_box.insert("end", f"  - {w}\n\n")

        if result.notes:
            self.notes_box.insert("end", "REQUIRED LOAD ORDER\n")
            for n in result.notes:
                self.notes_box.insert("end", f"  - {n}\n\n")

        if not result.warnings and not result.notes:
            self.notes_box.insert("end", "No conflicts detected — your load order is clean.\n")

        self.notes_box.configure(state="disabled")

    def _set_status(self, text: str):
        self.status_label.configure(text=text)


def run():
    app = App()
    app.mainloop()
