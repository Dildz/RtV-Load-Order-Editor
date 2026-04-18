"""customtkinter GUI for RtV load order editor."""
from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import messagebox

import customtkinter as ctk

from analyzer import PRIORITY_START, PRIORITY_STEP, AnalysisResult, analyze
from config_io import ModConfig, read_config, sync_with_mods, write_config
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
COLOR_NEUTRAL     = "#3a3a3a"
COLOR_NEUTRAL_HV  = "#4a4a4a"

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
        filename: str,
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
        self.filename = filename
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
            self, text=filename, anchor="w",
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
            command=lambda: self.on_move(self.filename, -1),
        )
        self.up_btn.grid(row=0, column=4, padx=2, pady=8)
        self.down_btn = ctk.CTkButton(
            self, text="▼", width=30, height=30,
            corner_radius=6,
            fg_color=COLOR_NEUTRAL, hover_color=COLOR_NEUTRAL_HV,
            command=lambda: self.on_move(self.filename, +1),
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
        self.on_change(self.filename, "enabled", self.enabled_var.get())

    def _priority_changed(self):
        try:
            v = int(self.priority_var.get())
        except ValueError:
            self.priority_var.set("0")
            v = 0
        self.on_change(self.filename, "priority", v)

    def get_priority(self) -> int:
        try:
            return int(self.priority_var.get())
        except ValueError:
            return 0

    def get_enabled(self) -> bool:
        return self.enabled_var.get()


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
            button_block, text="Refresh", width=96, height=34,
            corner_radius=8, font=FONT_BODY,
            fg_color=COLOR_NEUTRAL, hover_color=COLOR_NEUTRAL_HV,
            command=self._on_refresh,
        )
        self.refresh_btn.pack(side="left", padx=4)

        self.analyze_btn = ctk.CTkButton(
            button_block, text="Analyze Mods", width=130, height=34,
            corner_radius=8, font=FONT_BODY,
            fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HV,
            command=self._on_analyze,
        )
        self.analyze_btn.pack(side="left", padx=4)

        self.apply_btn = ctk.CTkButton(
            button_block, text="Save & Apply", width=130, height=34,
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

        # Set initial split: notes pane gets ~1/4 of the window height
        def _initial_split():
            self.update_idletasks()
            h = self.winfo_height()
            notes_h = max(220, int(h * 0.27))
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

        # Set initial split: notes pane gets ~180px from the bottom
        self.after(50, lambda: paned.sash_place(0, 1, self.winfo_height() - 200))

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
        sync_with_mods(self.cfg, [m.filename for m in self.scanned_mods])

        # Reorder cfg.order so it matches priority value (low → high) for display
        self.cfg.order.sort(key=lambda fn: (self.cfg.priority.get(fn, 0), fn.lower()))

        self._rebuild_rows()
        self._set_status(f"{len(self.scanned_mods)} mods loaded")
        self.footer_label.configure(text=f"Mods folder:  {self.mods_folder}")
        self.dirty = False

    def _rebuild_rows(self):
        for row in self.rows:
            row.destroy()
        self.rows = []

        names_by_file = {m.filename: m for m in self.scanned_mods}

        for fname in self.cfg.order:
            mod_info = names_by_file.get(fname)
            display_name = mod_info.display_name if mod_info else fname
            locked = mod_info.declared_priority is not None if mod_info else False

            row = ModRow(
                self.list_frame,
                filename=fname,
                display_name=display_name,
                priority=self.cfg.priority.get(fname, 0),
                enabled=self.cfg.enabled.get(fname, True),
                locked=locked,
                suggest_disable=fname in self.suggest_disable,
                on_change=self._on_row_change,
                on_move=self._on_row_move,
            )
            row.pack(fill="x", pady=4)
            self.rows.append(row)

    # ── actions ──────────────────────────────────────────────────────────────

    def _on_row_change(self, filename: str, field: str, value):
        if field == "enabled":
            self.cfg.enabled[filename] = bool(value)
        elif field == "priority":
            self.cfg.priority[filename] = int(value)
        self.dirty = True
        self._set_status("Unsaved changes")

    def _on_row_move(self, filename: str, delta: int):
        try:
            idx = self.cfg.order.index(filename)
        except ValueError:
            return
        new_idx = idx + delta
        if new_idx < 0 or new_idx >= len(self.cfg.order):
            return

        # Swap priorities with the neighbour, then swap order
        other = self.cfg.order[new_idx]
        p1 = self.cfg.priority.get(filename, 0)
        p2 = self.cfg.priority.get(other, 0)
        self.cfg.priority[filename] = p2
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
        self.cfg.order = [r.filename for r in result.recommendations]
        self.suggest_disable = set(result.suggest_disable)

        # Auto-disable mods flagged as dead — user can re-enable manually if desired
        for fname in self.suggest_disable:
            self.cfg.enabled[fname] = False

        # Renumber priorities: locked mods keep their declared value, disabled
        # mods get 0 (so they don't waste a number that an enabled mod could use),
        # and enabled mods get sequential values starting at PRIORITY_START.
        locked_values = {r.priority for r in result.recommendations if r.locked}
        next_value = PRIORITY_START
        for r in result.recommendations:
            if r.locked:
                self.cfg.priority[r.filename] = r.priority
                continue
            if not self.cfg.enabled.get(r.filename, True):
                self.cfg.priority[r.filename] = 0
                continue
            while next_value in locked_values:
                next_value += 1
            self.cfg.priority[r.filename] = next_value
            next_value += PRIORITY_STEP

        # Re-sort cfg.order to reflect the new priority values
        self.cfg.order.sort(key=lambda fn: (self.cfg.priority.get(fn, 0), fn.lower()))

        # Carry over any cfg-only mods (in cfg but not on disk) at the end
        on_disk = {m.filename for m in self.scanned_mods}
        for fn in list(self.cfg.priority.keys()):
            if fn not in on_disk and fn not in self.cfg.order:
                self.cfg.order.append(fn)

        self._rebuild_rows()
        self._show_notes(result)
        self.dirty = True
        self._set_status("Analysis applied — review and Save")

    def _on_apply(self):
        if not messagebox.askyesno(
            "Save mod_config.cfg?",
            f"Write current load order to:\n{MOD_CONFIG_FILE}\n\n"
            "A backup will be created automatically.",
        ):
            return
        try:
            write_config(MOD_CONFIG_FILE, self.cfg)
        except Exception as e:
            messagebox.showerror("Save failed", str(e))
            return
        self.dirty = False
        self._set_status("Saved.")
        messagebox.showinfo("Saved", "mod_config.cfg has been updated.\nLaunch Road to Vostok to verify.")

    def _on_refresh(self):
        if self.dirty and not messagebox.askyesno(
            "Discard changes?",
            "You have unsaved changes. Refresh anyway?",
        ):
            return
        self._load_from_disk()

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
