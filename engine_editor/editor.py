"""Builded the editor, manage projects, and compile game EXEs."""

import zipfile
import base64
import binascii
import copy
import ctypes
import io
import json
import math
import os
import random
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
import uuid
from pathlib import Path

import pygame
import tkinter
from tkinter import colorchooser, filedialog, messagebox, simpledialog, ttk

from PIL import Image, ImageColor, ImageTk

from .image_cache import ImageCache, decode_image_data

SNAP_SOUND = None
SNAP_SOUND_INTERVAL_SECONDS = 0.2
WORKSPACE_BLOCK_HEIGHT = 51
PROJECT_FORMAT = "engine-editor-project"
# Accept projects saved before the branding change.
LEGACY_PROJECT_FORMATS = {"steve-engine-editor-project"}
try:
    if not pygame.mixer.get_init():
        pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=512)
    bundle_root = Path(
        getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent),
    )
    SNAP_SOUND = pygame.mixer.Sound(str(bundle_root / "sounds" / "snap.wav"))
except pygame.error:
    SNAP_SOUND = None

from .blocks import (
    BLOCKS,
    COLORS,
    apply_color_effect,
    block_label,
    custom_block_label,
    create_block,
    expand_custom_blocks,
    evaluate_spatial_reporter,
    evaluate_condition,
    expand_script,
    parse_value,
    parse_reporter_expression,
    perform_calculation,
    split_event_scripts,
    typed_value,
    validate_control_flow,
)
from .game_bundle import append_game_payload
from .localization import translate
from .paint_editor import PaintEditor
from .physics_editor import PhysicsHitboxEditor
from .sound import imported_audio_is_playing, play_project_sound, stop_sounds
from .system_info import get_process_memory_mb
from sse_modding_tools import (
    MOD_REGISTRY,
    ModContext,
    coerce_input,
    mod_import_names,
)


class SteveEngine:
    """Own the editor's widgets, project data, stage, and build workflow."""

    def __init__(self, root):
        """Set initial project state, create the interface, and draw the stage."""
        self.root = root
        self.root.title("Steve Engine Editor")
        self.root.geometry("1280x760")
        self.root.minsize(1000, 650)
        self.root.configure(bg="#f4f4f4")

        self.blocks = []
        self.image_cache = ImageCache()
        self.custom_blocks = []
        self.editing_custom_block_id = None
        self.palette_custom_definition_id = None
        self.project_path = None
        self.app_settings = self.normalize_app_settings(self.load_app_settings())
        self.mods_directory = (
            Path(sys.executable).resolve().parent / "mods"
            if getattr(sys, "frozen", False)
            else Path(__file__).resolve().parent.parent / "mods"
        )
        MOD_REGISTRY.clear()
        if self.app_settings["modded_blocks_enabled"]:
            try:
                MOD_REGISTRY.load_directory(self.mods_directory)
            except OSError as error:
                MOD_REGISTRY.errors.append(
                    f"Could not read the mods folder {self.mods_directory}: {error}"
                )
        self.language = {
            "english": "en", "russian": "ru", "french": "fr", "romanian": "ro",
        }[self.app_settings["language_mode"]]
        self.game_settings = {
            "file_name": "engine-game",
            "window_title": "",
            "width": 960,
            "height": 720,
            "fullscreen": False,
        }
        self.variables = {}
        self.category = "Events"
        self.drag_index = None
        self.last_snap_sound_at = 0.0
        self.palette_drag_kind = None
        self.palette_drag_origin = None
        self.palette_dragging = False
        self.drop_shadow_ids = []
        self.drop_shadow_position = None
        self.running = False
        self.building_game = False
        self.memory_limit_mb = 512
        # Internal stage center is (200, 205); inspector values are relative to it.
        self.sprite_x = 200
        self.sprite_y = 205
        self.sprite_angle = 90
        self.sprite_name = "Sprite1"
        self.sprite_size = 100
        self.sprite_visible = True
        self.sprite_velocity_x = 0.0
        self.sprite_velocity_y = 0.0
        self.sprite_gravity = 0.0
        self.sprite_costumes = []
        self.sprite_costume_index = 0
        self.sprite_effect = 0
        self.sprite_layer_front = True
        self.backdrops = []
        self.backdrop_index = 0
        self.sounds = []
        self._updating_sprite_controls = False
        self.rotation_style = "all around"
        self.speech = ""
        self.speech_kind = "say"
        self.sound_pitch = 0
        self.sound_volume = 100
        self.stage_images = []
        self.stage_image_refs = []
        self.active_sprite_index = 0
        self.sprites = [{
            **self.sprite_project_data(),
            "blocks": self.blocks,
        }]
        self.sprite_drag_start = None
        self._running_sprite_queue = False
        self._sprite_script_queue = []
        self._sprite_queue_event_key = None

        self.create_ui()
        if self.app_settings["modded_blocks_enabled"] and MOD_REGISTRY.errors:
            self.status.set(
                f"Python mod load errors: {len(MOD_REGISTRY.errors)}. "
                "Open the Modded Blocks category for details."
            )
        self.refresh_sprite_tabs()
        self.show_category(self.category)
        self.draw_workspace()
        self.draw_stage()
        self.root.bind_all("<KeyPress>", self.on_key_press, add="+")
        # a updater will be stupid please dont add 
        
    def tr(self, text):
        """Translate one interface label using the selected language."""
        return translate(text, self.language)

    def save_active_sprite(self):
        """Store the selected sprite's properties and independent block script."""
        if not self.sprites:
            return
        sprite = self.sprite_project_data()
        if self.editing_custom_block_id is not None:
            definition = self.custom_block_by_id(self.editing_custom_block_id)
            if definition is not None:
                definition["body"] = copy.deepcopy(self.blocks)
            sprite["blocks"] = self.sprites[self.active_sprite_index].get("blocks", [])
        else:
            sprite["blocks"] = copy.deepcopy(self.blocks)
        self.sprites[self.active_sprite_index] = sprite

    def sync_active_sprite(self):
        """Keep the active sprite's saved properties current without copying its script."""
        if not self.sprites:
            return
        sprite = self.sprite_project_data()
        sprite["blocks"] = self.sprites[self.active_sprite_index].get("blocks", [])
        self.sprites[self.active_sprite_index] = sprite

    def refresh_sprite_tabs(self):
        """Refresh the Explorer tree after sprite, costume, or sound changes."""
        self.refresh_explorer()

    def select_sprite(self, index):
        """Switch the stage and block workspace to the chosen sprite."""
        if (
            self.running
            or self.editing_custom_block_id is not None
            or not 0 <= index < len(self.sprites)
            or index == self.active_sprite_index
        ):
            return
        self.save_active_sprite()
        self.active_sprite_index = index
        sprite = self.sprites[index]
        self.blocks = copy.deepcopy(sprite.get("blocks", []))
        selected_backdrop = self.backdrop_index
        self.load_sprite_project_data(sprite, self.backdrops)
        self.backdrop_index = selected_backdrop
        self.update_asset_status()
        self.refresh_sprite_tabs()
        self.show_category(self.category)
        self.draw_workspace()
        self.draw_stage()

    def add_sprite(self):
        """Create and select a new sprite with its own empty block script."""
        if self.running:
            return
        if self.editing_custom_block_id is not None:
            return
        self.save_active_sprite()
        name = f"Sprite {len(self.sprites) + 1}"
        sprite = {
            "name": name,
            "x": 0,
            "y": 0,
            "size": 100,
            "direction": 90,
            "visible": True,
            "velocity_x": 0.0,
            "velocity_y": 0.0,
            "gravity": 0.0,
            "costumes": [],
            "costume_index": 0,
            "effect": 0,
            "layer_front": True,
            "backdrop_index": self.backdrop_index,
            "blocks": [],
        }
        self.sprites.append(sprite)
        self.active_sprite_index = len(self.sprites) - 1
        self.blocks = []
        selected_backdrop = self.backdrop_index
        self.load_sprite_project_data(sprite, self.backdrops)
        self.backdrop_index = selected_backdrop
        self.update_asset_status()
        self.refresh_sprite_tabs()
        self.show_category(self.category)
        self.draw_workspace()
        self.draw_stage()
        self.status.set(f"Added {name}")

    def delete_sprite(self):
        """Delete the selected sprite while keeping one sprite in the project."""
        if self.running or self.editing_custom_block_id is not None:
            return
        if len(self.sprites) <= 1:
            messagebox.showinfo(
                "Cannot delete sprite",
                "A project must keep at least one sprite.",
                parent=self.root,
            )
            return
        sprite_name = self.sprites[self.active_sprite_index].get("name", "Sprite")
        if not messagebox.askyesno(
            "Delete sprite",
            f"Delete {sprite_name} and its scripts, costumes, and properties?",
            parent=self.root,
        ):
            return
        self.save_active_sprite()
        self.image_cache.clear()
        del self.sprites[self.active_sprite_index]
        self.active_sprite_index = min(self.active_sprite_index, len(self.sprites) - 1)
        sprite = self.sprites[self.active_sprite_index]
        self.blocks = copy.deepcopy(sprite.get("blocks", []))
        self.load_sprite_project_data(sprite, self.backdrops)
        self.refresh_sprite_tabs()
        self.show_category(self.category)
        self.draw_workspace()
        self.draw_stage()
        self.status.set(f"Deleted {sprite_name}")

    def delete_backdrop(self):
        """Delete the active backdrop and repair every sprite's saved index."""
        if self.running or self.editing_custom_block_id is not None:
            return
        if not self.backdrops:
            messagebox.showinfo(
                "No backdrop to delete",
                "Import a backdrop before deleting one.",
                parent=self.root,
            )
            return
        backdrop = self.backdrops[self.backdrop_index]
        backdrop_name = backdrop.get("name", "Backdrop")
        if not messagebox.askyesno(
            "Delete backdrop",
            f"Delete the backdrop '{backdrop_name}'?",
            parent=self.root,
        ):
            return
        self.save_active_sprite()
        self.image_cache.clear()
        deleted_index = self.backdrop_index
        del self.backdrops[deleted_index]
        for sprite in self.sprites:
            saved_index = sprite.get("backdrop_index", 0)
            if saved_index > deleted_index:
                saved_index -= 1
            elif saved_index == deleted_index:
                saved_index = min(deleted_index, len(self.backdrops) - 1)
            sprite["backdrop_index"] = max(saved_index, 0)
        self.backdrop_index = min(deleted_index, len(self.backdrops) - 1)
        sprite = self.sprites[self.active_sprite_index]
        self.load_sprite_project_data(sprite, self.backdrops)
        self.backdrop_index = max(self.backdrop_index, 0)
        sprite["backdrop_index"] = self.backdrop_index
        self.refresh_explorer()
        self.update_asset_status()
        self.draw_stage()
        self.status.set(f"Deleted backdrop {backdrop_name}")

    def _run_next_sprite_script(self):
        """Run the next matching script from the project's sprite list."""
        if not self._sprite_script_queue:
            self._running_sprite_queue = False
            self.run_button.config(state=tk.NORMAL)
            self.status.set("All sprite scripts finished.")
            return
        sprite_index = self._sprite_script_queue.pop(0)
        self.select_sprite(sprite_index)
        self.run_script(
            self._sprite_queue_event_key,
            _from_sprite_queue=True,
        )

    def _stop_sprite_script_queue(self):
        """Clear a multi-sprite run after a script reports an error."""
        self._sprite_script_queue.clear()
        self._running_sprite_queue = False

    @staticmethod
    def _is_administrator():
        """Check whether Windows started this process with administrator rights."""
        try:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except (AttributeError, OSError):
            return False

    @classmethod
    def can_export_game(cls):
        """Return whether this Windows process can build an installed game EXE."""
        return sys.platform == "win32" and cls._is_administrator()

    def refresh_export_menu(self):
        """Show the administrator requirement and disable builds when unavailable."""
        if not hasattr(self, "file_menu") or not hasattr(self, "export_menu_index"):
            return
        allowed = self.can_export_game()
        self.file_menu.entryconfigure(
            self.export_menu_index,
            label=(
                "Generate Game EXE"
                if allowed else "Generate Game EXE (run as administrator)"
            ),
            state=(
                tk.NORMAL
                if allowed and not getattr(self, "building_game", False)
                else tk.DISABLED
            ),
        )

    def create_ui(self):
        """Create the dark themed editor with Explorer, Preview, Tiles, and code."""
        self.ui_background = "#151923"
        self.panel_background = "#202633"
        self.panel_border = "#323b4b"
        self.text_primary = "#e7ebf2"
        self.text_muted = "#9ca8ba"
        self.root.configure(bg=self.ui_background)
        self.load_editor_assets()

        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(
            "Explorer.Treeview",
            background=self.panel_background,
            fieldbackground=self.panel_background,
            foreground=self.text_primary,
            borderwidth=0,
            rowheight=25,
            font=("Segoe UI", 9),
        )
        style.map(
            "Explorer.Treeview",
            background=[("selected", "#394a68")],
            foreground=[("selected", "white")],
        )
        style.configure(
            "Explorer.Treeview.Heading",
            background="#293243",
            foreground=self.text_primary,
            relief="flat",
            font=("Segoe UI", 9, "bold"),
        )

        menu = tk.Menu(
            self.root, tearoff=False, bg="#202633", fg=self.text_primary,
            activebackground="#394a68", activeforeground="white",
        )
        file_menu = tk.Menu(
            menu, tearoff=False, bg="#202633", fg=self.text_primary,
            activebackground="#394a68", activeforeground="white",
        )
        file_menu.add_command(label="Open .sse Project...", command=self.open_project)
        file_menu.add_command(label="Save .sse Project", command=self.save_project)
        import_menu = tk.Menu(
            file_menu, tearoff=False, bg="#202633", fg=self.text_primary,
            activebackground="#394a68", activeforeground="white",
        )
        import_menu.add_command(label="Image as Block...", command=self.import_image)
        import_menu.add_command(label="Sprite Costume...", command=self.import_costume)
        import_menu.add_command(label="Stage Backdrop...", command=self.import_backdrop)
        import_menu.add_command(label="Sound...", command=self.import_sound)
        file_menu.add_cascade(label="Import", menu=import_menu)
        file_menu.add_separator()
        file_menu.add_command(label="Generate Game EXE", command=self.start_build)
        self.file_menu = file_menu
        self.export_menu_index = file_menu.index("end")
        self.refresh_export_menu()
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.root.destroy)
        menu.add_cascade(label="File", menu=file_menu)
        edit_menu = tk.Menu(
            menu, tearoff=False, bg="#202633", fg=self.text_primary,
            activebackground="#394a68", activeforeground="white",
        )
        edit_menu.add_command(label="Paint Editor...", command=self.open_paint_editor)
        edit_menu.add_command(label="Organize Blocks", command=self.organize_blocks)
        edit_menu.add_command(
            label="Physics Hitboxes...", command=self.open_physics_hitbox_editor,
        )
        edit_menu.add_command(
            label="Delete Current Backdrop",
            command=self.delete_backdrop,
        )
        edit_menu.add_separator()
        edit_menu.add_command(label="Settings...", command=self.open_settings_dialog)
        menu.add_cascade(label="Edit", menu=edit_menu)
        help_menu = tk.Menu(
            menu, tearoff=False, bg="#202633", fg=self.text_primary,
            activebackground="#394a68", activeforeground="white",
        )
        help_menu.add_command(label="About", command=lambda: self.open_about_dialog())
        windowhelp = tkinter.Tk()
        windowhelp.title("About")
        windowhelp.geometry("640x480")
        tkinter.Label(windowhelp, text="welcome i wanna thank you pygame or Pete Shinners thats what the wiki says").pack()
        tkinter.Label(windowhelp, text="test asdgrewryhrtey").pack()

        menu.add_cascade(label="Help", menu=help_menu)
        self.root.configure(menu=menu)

        header = tk.Frame(self.root, bg="#202633", height=54)
        header.pack(fill="x")
        header.pack_propagate(False)
        tk.Label(
            header, text="", bg="#202633",
            fg=self.text_primary, font=("Segoe UI", 15, "bold"),
        ).pack(side="left", padx=16)
        self.status = tk.StringVar(
            value=self.tr("")
        )
        tk.Label(
            header, textvariable=self.status, bg="#202633",
            fg=self.text_muted, font=("Segoe UI", 9),
        ).pack(side="left", padx=12)

        body = tk.Frame(self.root, bg=self.ui_background)
        body.pack(fill="both", expand=True, padx=8, pady=8)

        left_panel = tk.Frame(body, bg=self.panel_background, width=210)
        left_panel.pack(side="left", fill="y", padx=(0, 8))
        left_panel.pack_propagate(False)
        explorer_header = tk.Frame(left_panel, bg=self.panel_background)
        explorer_header.pack(fill="x", padx=10, pady=(10, 5))
        tk.Label(
            explorer_header, text="EXPLORER", bg=self.panel_background,
            fg=self.text_primary, font=("Segoe UI", 10, "bold"),
        ).pack(side="left")
        tk.Button(
            explorer_header, text="+ Sprite", command=self.add_sprite,
            bg="#303a4b", fg=self.text_primary, relief="flat", padx=6, pady=3,
        ).pack(side="right")
        tk.Button(
            explorer_header, text="- Sprite", command=self.delete_sprite,
            bg="#303a4b", fg=self.text_primary, relief="flat", padx=6, pady=3,
        ).pack(side="right", padx=(0, 4))
        explorer_area = tk.Frame(left_panel, bg=self.panel_background)
        explorer_area.pack(fill="both", expand=True, padx=7, pady=(0, 6))
        self.explorer = ttk.Treeview(
            explorer_area, show="tree", selectmode="browse",
            style="Explorer.Treeview",
        )
        explorer_scroll = tk.Scrollbar(
            explorer_area, orient="vertical", command=self.explorer.yview,
            bg="#303a4b", troughcolor=self.panel_background,
        )
        self.explorer.configure(yscrollcommand=explorer_scroll.set)
        self.explorer.pack(side="left", fill="both", expand=True)
        explorer_scroll.pack(side="right", fill="y")
        self.explorer.bind("<<TreeviewSelect>>", self.on_explorer_select)
        self.explorer_item_map = {}

        properties_panel = tk.Frame(
            left_panel, bg="#252e3d", highlightthickness=1,
            highlightbackground=self.panel_border,
        )
        properties_panel.pack(fill="x", padx=7, pady=(2, 8))
        tk.Label(
            properties_panel, text="PROPERTIES", bg="#252e3d",
            fg=self.text_primary, font=("Segoe UI", 9, "bold"),
        ).pack(anchor="w", padx=9, pady=(8, 5))
        properties = tk.Frame(properties_panel, bg="#252e3d")
        properties.pack(fill="x", padx=8)
        self.sprite_name_var = tk.StringVar(value=self.sprite_name)
        self.sprite_x_var = tk.StringVar(value="0")
        self.sprite_y_var = tk.StringVar(value="0")
        self.sprite_size_var = tk.StringVar(value="100")
        self.sprite_direction_var = tk.StringVar(value="90")
        for row, (label, variable) in enumerate((
            ("Name", self.sprite_name_var),
            ("X", self.sprite_x_var),
            ("Y", self.sprite_y_var),
            ("Size", self.sprite_size_var),
            ("Direction", self.sprite_direction_var),
        )):
            tk.Label(
                properties, text=label, bg="#252e3d", fg=self.text_muted,
                font=("Segoe UI", 8),
            ).grid(row=row, column=0, sticky="w", padx=(0, 6), pady=2)
            tk.Entry(
                properties, textvariable=variable, width=13, relief="flat",
                bg="#151923", fg=self.text_primary, insertbackground="white",
            ).grid(row=row, column=1, sticky="ew", pady=2)
        properties.grid_columnconfigure(1, weight=1)
        for variable in (
            self.sprite_name_var, self.sprite_x_var, self.sprite_y_var,
            self.sprite_size_var, self.sprite_direction_var,
        ):
            variable.trace_add("write", self.update_sprite_properties)
        self.sprite_visible_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            properties_panel, text="Visible", variable=self.sprite_visible_var,
            command=self.toggle_sprite_visibility, bg="#252e3d",
            fg=self.text_primary, activebackground="#252e3d",
            activeforeground=self.text_primary, selectcolor="#151923",
        ).pack(anchor="w", padx=7, pady=(2, 5))
        self.asset_status = tk.StringVar(value="No costumes or backdrops")
        tk.Label(
            properties_panel, textvariable=self.asset_status,
            bg="#252e3d", fg=self.text_muted, anchor="w",
            wraplength=178, justify="left", font=("Segoe UI", 8),
        ).pack(fill="x", padx=9, pady=(0, 6))
        self.sound_status = tk.StringVar(
            value=f"{len(self.sounds)} imported sound(s)",
        )
        tk.Label(
            properties_panel, textvariable=self.sound_status,
            bg="#252e3d", fg=self.text_muted, anchor="w",
            font=("Segoe UI", 8),
        ).pack(fill="x", padx=9, pady=(0, 8))

        right_panel = tk.Frame(body, bg=self.panel_background, width=340)
        right_panel.pack(side="right", fill="y", padx=(8, 0))
        right_panel.pack_propagate(False)
        tk.Label(
            right_panel, text="TILES", bg=self.panel_background,
            fg=self.text_primary, font=("Segoe UI", 10, "bold"),
            anchor="w",
        ).pack(fill="x", padx=10, pady=(10, 6))
        tile_content = tk.Frame(right_panel, bg=self.panel_background)
        tile_content.pack(fill="both", expand=True, padx=7, pady=(0, 8))
        category_area = tk.Frame(tile_content, bg=self.panel_background, width=104)
        category_area.pack(side="left", fill="y", padx=(0, 6))
        category_area.pack_propagate(False)
        self.category_canvas = tk.Canvas(
            category_area, width=100, bg=self.panel_background,
            highlightthickness=0,
        )
        category_scroll = tk.Scrollbar(
            category_area, orient="vertical", command=self.category_canvas.yview,
            bg="#303a4b", troughcolor=self.panel_background,
        )
        self.category_buttons = tk.Frame(
            self.category_canvas, bg=self.panel_background,
        )
        self.category_window = self.category_canvas.create_window(
            (0, 0), window=self.category_buttons, anchor="nw",
        )
        self.category_canvas.configure(yscrollcommand=category_scroll.set)
        self.category_canvas.pack(side="left", fill="both", expand=True)
        category_scroll.pack(side="right", fill="y")
        self.category_buttons.bind(
            "<Configure>",
            lambda _event: self.category_canvas.configure(
                scrollregion=self.category_canvas.bbox("all"),
            ),
        )
        self.category_canvas.bind(
            "<Configure>",
            lambda event: self.category_canvas.itemconfigure(
                self.category_window, width=event.width,
            ),
        )
        palette_panel = tk.Frame(
            tile_content, bg=self.panel_background,
            highlightthickness=1, highlightbackground=self.panel_border,
        )
        palette_panel.pack(side="left", fill="both", expand=True)
        self.palette_title = tk.Label(
            palette_panel, text="", bg=self.panel_background,
            fg=self.text_primary, font=("Segoe UI", 10, "bold"), anchor="w",
        )
        self.palette_title.pack(fill="x", padx=9, pady=(9, 6))
        palette_body = tk.Frame(palette_panel, bg=self.panel_background)
        palette_body.pack(fill="both", expand=True, padx=7, pady=(0, 7))
        self.palette_canvas = tk.Canvas(
            palette_body, bg=self.panel_background, highlightthickness=0,
            yscrollincrement=24,
        )
        palette_scroll = tk.Scrollbar(
            palette_body, orient="vertical", command=self.palette_canvas.yview,
            bg="#303a4b", troughcolor=self.panel_background,
        )
        self.palette = tk.Frame(
            self.palette_canvas, bg=self.panel_background,
        )
        self.palette_window = self.palette_canvas.create_window(
            (0, 0), window=self.palette, anchor="nw",
        )
        self.palette_canvas.configure(yscrollcommand=palette_scroll.set)
        self.palette_canvas.pack(side="left", fill="both", expand=True)
        palette_scroll.pack(side="right", fill="y")
        self.palette.bind(
            "<Configure>",
            lambda _event: self.palette_canvas.configure(
                scrollregion=self.palette_canvas.bbox("all"),
            ),
        )
        self.palette_canvas.bind("<Configure>", self.resize_palette_content)
        tk.Label(
            palette_panel,
            text="Click a tile to add it, or drag it into the script.",
            bg=self.panel_background, fg=self.text_muted,
            wraplength=190, justify="left", font=("Segoe UI", 8),
        ).pack(fill="x", padx=9, pady=(0, 8))
        self.refresh_category_buttons()

        center_panel = tk.Frame(body, bg=self.ui_background)
        center_panel.pack(side="left", fill="both", expand=True)
        stage_panel = tk.Frame(
            center_panel, bg=self.panel_background, height=350,
            highlightthickness=1, highlightbackground=self.panel_border,
        )
        stage_panel.pack(fill="both", expand=True, pady=(0, 8))
        stage_panel.pack_propagate(False)
        stage_header = tk.Frame(stage_panel, bg=self.panel_background)
        stage_header.pack(fill="x", padx=10, pady=(8, 6))
        tk.Label(
            stage_header, text="PREVIEW", bg=self.panel_background,
            fg=self.text_primary, font=("Segoe UI", 10, "bold"),
        ).pack(side="left")
        self.run_button = tk.Button(
            stage_header, text=f"▶ {self.tr('Run')}", command=self.run_script,
            bg="#238636", fg="white", activebackground="#2ea043",
            activeforeground="white", relief="flat", padx=10,
        )
        self.run_button.pack(side="right")
        tk.Button(
            stage_header, text=self.tr("Reset"), command=self.reset_stage,
            bg="#303a4b", fg=self.text_primary, relief="flat", padx=9,
        ).pack(side="right", padx=5)
        preview_area = tk.Frame(stage_panel, bg="#11151d")
        preview_area.pack(fill="both", expand=True, padx=9, pady=(0, 9))
        self.stage = tk.Canvas(
            preview_area, width=480, height=270, bg="#f8fafc",
            highlightthickness=1, highlightbackground="#3c4658",
        )
        self.stage.pack(fill="both", expand=True)
        self.stage.bind("<Configure>", self.on_stage_resize)
        self.stage.bind("<ButtonPress-1>", self.begin_sprite_drag)
        self.stage.bind("<B1-Motion>", self.drag_sprite)
        self.stage.bind("<ButtonRelease-1>", self.finish_sprite_drag)
        self.memory_status = tk.StringVar(value="Memory monitor: idle")
        self.system_result = tk.StringVar(value="App memory: not measured")

        script_panel = tk.Frame(
            center_panel, bg=self.panel_background, height=300,
            highlightthickness=1, highlightbackground=self.panel_border,
        )
        script_panel.pack(fill="both", expand=True)
        script_header = tk.Frame(script_panel, bg=self.panel_background)
        script_header.pack(fill="x", padx=10, pady=(8, 6))
        self.script_workspace_title = tk.Label(
            script_header, text="SCRIPT WORKSPACE", bg=self.panel_background,
            fg=self.text_primary, font=("Segoe UI", 10, "bold"), anchor="w",
        )
        self.script_workspace_title.pack(side="left")
        self.finish_custom_edit_button = tk.Button(
            script_header, text="Done editing block",
            command=self.finish_custom_block_edit,
            bg="#5268e8", fg="white", relief="flat", padx=8,
        )
        script_area = tk.Frame(script_panel, bg="#151923")
        script_area.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.script_canvas = tk.Canvas(
            script_area, bg="#151923", highlightthickness=0,
            yscrollincrement=24, xscrollincrement=24,
        )
        script_scroll = tk.Scrollbar(
            script_area, orient="vertical", command=self.script_canvas.yview,
            bg="#303a4b", troughcolor="#202633",
        )
        script_hscroll = tk.Scrollbar(
            script_panel, orient="horizontal", command=self.script_canvas.xview,
            bg="#303a4b", troughcolor="#202633",
        )
        self.script_canvas.configure(
            yscrollcommand=script_scroll.set,
            xscrollcommand=script_hscroll.set,
        )
        self.script_canvas.pack(side="left", fill="both", expand=True)
        script_scroll.pack(side="right", fill="y")
        script_hscroll.pack(fill="x", padx=8, pady=(0, 6))
        self.script_canvas.bind("<Configure>", self.on_workspace_resize)

    def load_editor_assets(self):
        """Load the supplied artwork and keep Tk image references alive."""
        bundle_root = Path(
            getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent),
        )
        self.assets_directory = bundle_root / "assets"
        self.ui_icons = {}
        self.block_art = {}
        self.block_art_refs = {}
        for name in (
            "image.png", "audio.png", "opernation.png", "block.png",
            "block - Copy.png", "Red_block.png", "if then block.png",
            "while  do block.png",
        ):
            path = self.assets_directory / name
            if not path.is_file():
                continue
            with Image.open(path) as source:
                self.block_art[name] = source.convert("RGBA")
                if name in ("image.png", "audio.png", "opernation.png"):
                    icon = self.block_art[name].copy()
                    icon.thumbnail((18, 18), Image.Resampling.NEAREST)
                    self.ui_icons[name] = ImageTk.PhotoImage(icon)

    def on_explorer_select(self, _event=None):
        """Select a sprite or backdrop from the Explorer hierarchy."""
        if getattr(self, "_refreshing_explorer", False):
            return
        selected = self.explorer.selection()
        if not selected:
            return
        item = self.explorer_item_map.get(selected[0])
        if item is None:
            return
        kind, index = item
        if kind == "sprite" and index != self.active_sprite_index:
            self.select_sprite(index)
        elif kind == "backdrop" and index < len(self.backdrops):
            self.save_active_sprite()
            self.backdrop_index = index
            self.sprites[self.active_sprite_index]["backdrop_index"] = index
            self.update_asset_status()
            self.draw_stage()
        elif kind == "costume" and index < len(self.sprite_costumes):
            self.sprite_costume_index = index
            self.sync_active_sprite()
            self.update_asset_status()
            self.draw_stage()
        elif kind == "sound" and index < len(self.sounds):
            self.status.set(f"Selected sound: {self.sounds[index].get('name', 'Sound')}")

    def refresh_explorer(self):
        """Rebuild the Explorer tree from current sprites and media assets."""
        if not hasattr(self, "explorer"):
            return
        self._refreshing_explorer = True
        self.explorer.delete(*self.explorer.get_children())
        self.explorer_item_map.clear()
        image_icon = self.ui_icons.get("image.png")
        audio_icon = self.ui_icons.get("audio.png")
        sprites_root = self.explorer.insert(
            "", "end", iid="explorer-sprites", text="Sprites", open=True,
        )
        for index, sprite in enumerate(self.sprites):
            sprite_id = f"explorer-sprite-{index}"
            self.explorer.insert(
                sprites_root, "end", iid=sprite_id,
                text=sprite.get("name", f"Sprite {index + 1}"),
                image=image_icon,
                open=index == self.active_sprite_index,
            )
            self.explorer_item_map[sprite_id] = ("sprite", index)
            if index != self.active_sprite_index:
                continue
            for costume_index, costume in enumerate(sprite.get("costumes", [])):
                costume_id = f"explorer-costume-{index}-{costume_index}"
                self.explorer.insert(
                    sprite_id, "end", iid=costume_id,
                    text=f"Costume: {costume.get('name', costume_index + 1)}",
                    image=image_icon,
                )
                self.explorer_item_map[costume_id] = (
                    "costume", costume_index,
                )
        backdrop_root = self.explorer.insert(
            "", "end", iid="explorer-backdrops", text="Backdrops", open=True,
        )
        for index, backdrop in enumerate(self.backdrops):
            backdrop_id = f"explorer-backdrop-{index}"
            self.explorer.insert(
                backdrop_root, "end", iid=backdrop_id,
                text=backdrop.get("name", f"Backdrop {index + 1}"),
                image=image_icon,
            )
            self.explorer_item_map[backdrop_id] = ("backdrop", index)
        sound_root = self.explorer.insert(
            "", "end", iid="explorer-sounds", text="Sounds", open=True,
        )
        for index, sound in enumerate(self.sounds):
            sound_id = f"explorer-sound-{index}"
            self.explorer.insert(
                sound_root, "end", iid=sound_id,
                text=sound.get("name", f"Sound {index + 1}"),
                image=audio_icon,
            )
            self.explorer_item_map[sound_id] = ("sound", index)
        active_id = f"explorer-sprite-{self.active_sprite_index}"
        if self.explorer.exists(active_id):
            self.explorer.selection_set(active_id)
        self._refreshing_explorer = False

    def workspace_block_art(self, kind, color, width, height=51):
        """Tint and scale the supplied pixel block art for the script canvas."""
        if not self.block_art:
            return None
        if kind == "if":
            asset_name = "if then block.png"
        elif kind == "while":
            asset_name = "while  do block.png"
        elif kind in ("else", "end_if", "end_while", "end_repeat"):
            asset_name = "Red_block.png"
        elif kind in ("operator", "calculate"):
            asset_name = "block - Copy.png"
        else:
            asset_name = "block.png"
        source = self.block_art.get(asset_name, self.block_art.get("block.png"))
        if source is None:
            return None
        bounds = source.getchannel("A").getbbox()
        if bounds is None:
            return None
        width = max(90, min(int(width), 500))
        key = (asset_name, color, width, height)
        cached = self.block_art_refs.get(key)
        if cached is not None:
            return cached
        silhouette = source.crop(bounds)
        alpha = silhouette.getchannel("A")
        try:
            rgb = ImageColor.getrgb(color)[:3]
        except ValueError:
            rgb = (255, 216, 0)
        tinted = Image.new("RGBA", silhouette.size, (*rgb, 0))
        tinted.putalpha(alpha)
        tinted = tinted.resize((width, height), Image.Resampling.NEAREST)
        photo = ImageTk.PhotoImage(tinted)
        self.block_art_refs[key] = photo
        return photo

    def refresh_category_buttons(self):
        """Rebuild built-in and currently loaded mod category buttons."""
        for child in self.category_buttons.winfo_children():
            child.destroy()
        category_items = list(COLORS.items())
        category_items.extend(
            (name, MOD_REGISTRY.category_style(name)[1])
            for name in sorted(MOD_REGISTRY.categories - COLORS.keys())
        )
        for name, color in category_items:
            if name == "Files" and not self.app_settings["read_write_blocks"]:
                continue
            if name == "System" and not self.app_settings["system_blocks_enabled"]:
                continue
            if (
                (name == "Modded Blocks" or name in MOD_REGISTRY.categories)
                and not self.app_settings["modded_blocks_enabled"]
            ):
                continue
            label, button_color, icon = (
                MOD_REGISTRY.category_style(name)
                if name in MOD_REGISTRY.categories
                else (self.tr(name), color, "")
            )
            text = f"{icon} {label}" if icon else self.tr(label)
            tk.Button(
                self.category_buttons, text=text, anchor="w",
                command=lambda n=name: self.show_category(n),
                bg=button_color, fg="white", activebackground=button_color,
                activeforeground="white",
                relief="flat", padx=10, pady=9, font=("Segoe UI", 10, "bold"),
            ).pack(fill="x", pady=3, padx=(0, 4))

    def show_category(self, name):
        """Replace the palette contents with blocks from one category."""
        if not self.app_settings["modded_blocks_enabled"] and (
            name == "Modded Blocks" or name in MOD_REGISTRY.categories
        ):
            name = "Events"
        if name not in BLOCKS and name not in MOD_REGISTRY.categories:
            name = "Events"
        self.category = name
        category_label = (
            MOD_REGISTRY.category_style(name)[0]
            if name in MOD_REGISTRY.categories
            else self.tr(name)
        )
        title = "Defined Blocks" if name == "Defined Blocks" else (
            f"{category_label} {self.tr('blocks')}"
        )
        self.palette_title.config(text=title)
        for child in self.palette.winfo_children():
            child.destroy()
        self.palette_canvas.yview_moveto(0.0)
        if name == "Defined Blocks":
            self.show_defined_blocks_palette()
            self.bind_palette_mousewheel(self.palette_canvas)
            return
        if name == "Modded Blocks":
            self.show_modded_blocks_palette()
            self.bind_palette_mousewheel(self.palette_canvas)
            return
        if name in MOD_REGISTRY.categories and name not in COLORS:
            self.show_modded_blocks_palette(name)
            self.bind_palette_mousewheel(self.palette_canvas)
            return
        for kind, label in BLOCKS[name]:
            button = tk.Button(
                self.palette, text=self.tr(label), anchor="w", justify="left",
                bg=COLORS[name], fg="white", activebackground=COLORS[name],
                activeforeground="white", relief="flat",
                wraplength=self.palette_text_wraplength(),
                padx=10, pady=7, font=("Segoe UI", 10),
            )
            button.pack(fill="x", pady=5)
            button.bind(
                "<ButtonPress-1>",
                lambda event, block_kind=kind: self.begin_palette_drag(event, block_kind),
            )
            button.bind("<B1-Motion>", self.move_palette_drag)
            button.bind("<ButtonRelease-1>", self.finish_palette_drag)
        if name in MOD_REGISTRY.categories:
            self.show_modded_blocks_palette(name)
        self.bind_palette_mousewheel(self.palette_canvas)

    def palette_text_wraplength(self):
        """Fit palette button labels inside the available content width."""
        return max(100, self.palette_canvas.winfo_width() - 36)

    def resize_palette_content(self, event):
        """Resize palette content and reflow labels when its panel changes."""
        self.palette_canvas.itemconfigure(self.palette_window, width=event.width)
        wraplength = max(100, event.width - 36)
        for child in self.palette.winfo_children():
            if isinstance(child, tk.Button):
                child.configure(wraplength=wraplength)

    def bind_palette_mousewheel(self, widget):
        """Route wheel events over palette content to its vertical canvas."""
        if not getattr(widget, "_palette_mousewheel_bound", False):
            widget.bind("<MouseWheel>", self.on_palette_mousewheel, add="+")
            widget.bind("<Button-4>", self.on_palette_mousewheel, add="+")
            widget.bind("<Button-5>", self.on_palette_mousewheel, add="+")
            widget._palette_mousewheel_bound = True
        for child in widget.winfo_children():
            self.bind_palette_mousewheel(child)

    def on_palette_mousewheel(self, event):
        """Scroll the block palette for Windows/macOS and X11 wheel events."""
        if getattr(event, "num", None) == 4:
            units = -1
        elif getattr(event, "num", None) == 5:
            units = 1
        else:
            units = -1 if event.delta > 0 else 1
        self.palette_canvas.yview_scroll(units, "units")
        return "break"

    def show_modded_blocks_palette(self, category="Modded Blocks"):
        """Show loaded Python blocks, mod errors, and folder controls."""
        if category == "Modded Blocks":
            controls = tk.Frame(self.palette, bg="white")
            controls.pack(fill="x", pady=(2, 8))
            tk.Button(
                controls, text="Open mods folder", command=self.open_mods_folder,
                relief="flat", bg="#f2e9f7", fg="#57306f", padx=5,
            ).pack(fill="x", pady=(0, 4))
            tk.Button(
                controls, text="Reload mods", command=self.reload_mods,
                relief="flat", bg="#f2e9f7", fg="#57306f", padx=5,
            ).pack(fill="x")
            for error in MOD_REGISTRY.errors:
                tk.Label(
                    self.palette, text=error, bg="#fff1f0", fg="#b42318",
                    wraplength=195, justify="left", padx=6, pady=5,
                ).pack(fill="x", pady=3)
        definitions = sorted(
            (
                definition for definition in MOD_REGISTRY.blocks.values()
                if definition.category == category
            ),
            key=lambda definition: (definition.mod_id, definition.block_id),
        )
        if not definitions:
            tk.Label(
                self.palette,
                text=(
                    "Add a .py mod to the mods folder, then reload mods."
                    if category == "Modded Blocks"
                    else f"No {category} blocks are currently loaded."
                ),
                bg="white", fg="#667085", wraplength=190, justify="left",
            ).pack(fill="x", pady=8)
            return
        for definition in definitions:
            category_color = MOD_REGISTRY.category_style(category)[1]
            button_color = definition.color or category_color
            button = tk.Button(
                self.palette,
                text=MOD_REGISTRY.palette_label(
                    MOD_REGISTRY.create_block(definition.kind),
                ),
                anchor="w", justify="left", bg=button_color,
                fg="white", activebackground=button_color,
                activeforeground="white", relief="flat",
                wraplength=self.palette_text_wraplength(),
                padx=10, pady=10, font=("Segoe UI", 10),
            )
            button.pack(fill="x", pady=5)
            button.bind(
                "<ButtonPress-1>",
                lambda event, block_kind=definition.kind:
                    self.begin_palette_drag(event, block_kind),
            )
            button.bind("<B1-Motion>", self.move_palette_drag)
            button.bind("<ButtonRelease-1>", self.finish_palette_drag)

    def open_mods_folder(self):
        """Open the editor's mods directory in the system file manager."""
        try:
            self.mods_directory.mkdir(parents=True, exist_ok=True)
            if sys.platform == "win32":
                os.startfile(str(self.mods_directory))
            else:
                opener = "open" if sys.platform == "darwin" else "xdg-open"
                subprocess.Popen([opener, str(self.mods_directory)])
        except OSError as error:
            messagebox.showerror(
                "Could not open mods folder",
                f"Could not open {self.mods_directory}:\n{error}",
                parent=self.root,
            )

    def reload_mods(self):
        """Reload project-local Python mods and refresh the visible palette."""
        if not self.app_settings["modded_blocks_enabled"]:
            MOD_REGISTRY.clear()
            self.status.set("Enable Modded Blocks in Settings before loading Python mods.")
            return
        try:
            MOD_REGISTRY.load_directory(self.mods_directory)
        except OSError as error:
            messagebox.showerror(
                "Could not load mods",
                f"Could not read the mods folder {self.mods_directory}:\n{error}",
                parent=self.root,
            )
            return
        self.refresh_category_buttons()
        self.show_category("Modded Blocks")
        self.draw_workspace()
        if MOD_REGISTRY.errors:
            self.status.set(f"Loaded mods with {len(MOD_REGISTRY.errors)} error(s).")
        else:
            self.status.set(f"Loaded {len(MOD_REGISTRY.blocks)} modded block(s).")

    def custom_block_by_id(self, definition_id):
        """Find a project-defined block by its stable ID."""
        return next(
            (
                definition for definition in self.custom_blocks
                if definition.get("id") == definition_id
            ),
            None,
        )

    def show_defined_blocks_palette(self):
        """Show custom block management and reusable call blocks."""
        tk.Button(
            self.palette,
            text="+ Make a Block",
            command=self.create_custom_block,
            bg="#ff6680", fg="white", activebackground="#ef5470",
            relief="flat", padx=10, pady=8, font=("Segoe UI", 10, "bold"),
        ).pack(fill="x", pady=(2, 10))
        if not self.custom_blocks:
            tk.Label(
                self.palette,
                text="Create a block to define a reusable script.",
                bg="white", fg="#667085", wraplength=190, justify="left",
            ).pack(fill="x", pady=8)
        for definition in self.custom_blocks:
            card = tk.Frame(self.palette, bg="#fff1f4", padx=8, pady=8)
            card.pack(fill="x", pady=5)
            tk.Label(
                card,
                text=custom_block_label(definition),
                bg="#fff1f4", fg="#9e2340", wraplength=180, justify="left",
                font=("Segoe UI", 10, "bold"),
            ).pack(fill="x", pady=(0, 6))
            actions = tk.Frame(card, bg="#fff1f4")
            actions.pack(fill="x")
            tk.Button(
                actions, text="Edit script",
                command=lambda item=definition: self.edit_custom_block_body(item),
                relief="flat", bg="#5268e8", fg="white", padx=5,
            ).pack(side="left", padx=(0, 4))
            tk.Button(
                actions, text="Edit block",
                command=lambda item=definition: self.edit_custom_block(item),
                relief="flat", bg="white", fg="#344054", padx=5,
            ).pack(side="left")
            tk.Button(
                card, text="Delete",
                command=lambda item=definition: self.delete_custom_block(item),
                relief="flat", bg="white", fg="#b42318", padx=5,
            ).pack(anchor="e", pady=(5, 0))
            call_button = tk.Button(
                card,
                text="Drag or click to add this block",
                relief="flat", bg="#ff6680", fg="white", padx=5, pady=5,
            )
            call_button.pack(fill="x", pady=(5, 0))
            call_button.bind(
                "<ButtonPress-1>",
                lambda event, item=definition: self.begin_custom_palette_drag(event, item),
            )
            call_button.bind("<B1-Motion>", self.move_palette_drag)
            call_button.bind("<ButtonRelease-1>", self.finish_palette_drag)

    def begin_custom_palette_drag(self, event, definition):
        """Remember the custom definition associated with a palette drag."""
        self.palette_custom_definition_id = definition["id"]
        self.begin_palette_drag(event, "custom_call")

    def create_palette_block(self, kind):
        """Create a standard palette block or a call to a custom definition."""
        if kind != "custom_call":
            block = create_block(kind)
            if MOD_REGISTRY.get(kind) is not None:
                block["mod_label"] = MOD_REGISTRY.label(block)
            return block
        definition = self.custom_block_by_id(self.palette_custom_definition_id)
        if definition is None:
            raise ValueError("That custom block definition no longer exists.")
        values = {
            part["name"]: (
                "false" if part["type"] == "boolean"
                else "0" if part["type"] == "number" else ""
            )
            for part in definition["parts"]
            if part.get("kind") == "input"
        }
        return {
            "kind": "custom_call",
            "definition_id": definition["id"],
            "name": definition["name"],
            "parts": copy.deepcopy(definition["parts"]),
            "values": values,
            "label": custom_block_label(definition, values),
        }

    def create_custom_block(self):
        """Create a custom block definition in the editor's block language."""
        result = self.open_custom_block_dialog()
        if result is None:
            return
        definition = {
            "id": uuid.uuid4().hex,
            "name": result["name"],
            "parts": result["parts"],
            "warp": result["warp"],
            "body": [],
        }
        self.custom_blocks.append(definition)
        self.show_category("Defined Blocks")
        self.edit_custom_block_body(definition)

    def edit_custom_block(self, definition):
        """Edit a custom block's visible name, inputs, and refresh behavior."""
        self.save_active_sprite()
        result = self.open_custom_block_dialog(definition)
        if result is None:
            return
        if any(
            item["id"] != definition["id"]
            and item["name"].casefold() == result["name"].casefold()
            for item in self.custom_blocks
        ):
            messagebox.showerror(
                "Duplicate block name",
                "Choose a name that is not already used by another custom block.",
                parent=self.root,
            )
            return
        definition.update(
            name=result["name"],
            parts=result["parts"],
            warp=result["warp"],
        )
        self.refresh_custom_call_blocks(definition)
        self.show_category("Defined Blocks")
        self.draw_workspace()

    def refresh_custom_call_blocks(self, definition):
        """Update stored labels and input fields in existing call blocks."""
        def refresh(blocks):
            for block in blocks:
                if block.get("kind") == "custom_call" and block.get("definition_id") == definition["id"]:
                    old_values = block.get("values", {})
                    block["values"] = {
                        part["name"]: old_values.get(
                            part["name"],
                            "false" if part["type"] == "boolean"
                            else "0" if part["type"] == "number" else "",
                        )
                        for part in definition["parts"]
                        if part.get("kind") == "input"
                    }
                    block["name"] = definition["name"]
                    block["parts"] = copy.deepcopy(definition["parts"])
                    block["label"] = custom_block_label(definition, block["values"])

        for sprite in self.sprites:
            refresh(sprite.get("blocks", []))
        for item in self.custom_blocks:
            refresh(item.get("body", []))
        if self.editing_custom_block_id is not None:
            refresh(self.blocks)
        else:
            self.blocks = copy.deepcopy(
                self.sprites[self.active_sprite_index].get("blocks", self.blocks)
            )

    def delete_custom_block(self, definition):
        """Remove a custom definition and all calls to it from the project."""
        if not messagebox.askyesno(
            "Delete custom block",
            f"Delete '{definition['name']}' and its calls?",
            parent=self.root,
        ):
            return
        self.save_active_sprite()
        self.image_cache.clear()
        if self.editing_custom_block_id == definition["id"]:
            self.finish_custom_block_edit()
        self.custom_blocks = [
            item for item in self.custom_blocks if item["id"] != definition["id"]
        ]

        def remove_calls(blocks):
            return [
                block for block in blocks
                if not (
                    block.get("kind") == "custom_call"
                    and block.get("definition_id") == definition["id"]
                )
            ]

        for sprite in self.sprites:
            sprite["blocks"] = remove_calls(sprite.get("blocks", []))
        for item in self.custom_blocks:
            item["body"] = remove_calls(item.get("body", []))
        self.blocks = copy.deepcopy(
            self.sprites[self.active_sprite_index].get("blocks", [])
        )
        self.show_category("Defined Blocks")
        self.draw_workspace()

    def edit_custom_block_body(self, definition):
        """Open a definition's script in the existing draggable workspace."""
        if self.editing_custom_block_id is not None:
            self.finish_custom_block_edit()
        self.save_active_sprite()
        self.editing_custom_block_id = definition["id"]
        self.blocks = copy.deepcopy(definition.get("body", []))
        self.script_workspace_title.config(text=f"DEFINE {custom_block_label(definition)}")
        self.finish_custom_edit_button.pack(side="right", padx=(0, 8))
        self.show_category("Motion")
        self.draw_workspace()

    def finish_custom_block_edit(self):
        """Save a custom definition body and return to the sprite script."""
        if self.editing_custom_block_id is None:
            return
        self.save_active_sprite()
        self.editing_custom_block_id = None
        self.blocks = copy.deepcopy(
            self.sprites[self.active_sprite_index].get("blocks", [])
        )
        self.finish_custom_edit_button.pack_forget()
        self.script_workspace_title.config(text=self.tr("SCRIPT WORKSPACE"))
        self.show_category("Defined Blocks")
        self.draw_workspace()

    def open_custom_block_dialog(self, definition=None):
        """Show the custom block editor for a name, inputs, labels, and warp flag."""
        dialog = tk.Toplevel(self.root)
        dialog.title("Make a Block")
        dialog.transient(self.root)
        dialog.geometry("700x520")
        dialog.minsize(620, 460)
        dialog.configure(bg="#e6f0ff")
        header = tk.Frame(dialog, bg="#8056d8", height=48)
        header.pack(fill="x")
        header.pack_propagate(False)
        tk.Label(
            header, text="Make a Block", bg="#8056d8", fg="white",
            font=("Segoe UI", 13),
        ).pack(expand=True)
        name = tk.StringVar(value=definition.get("name", "") if definition else "")
        parts = copy.deepcopy(definition.get("parts", []) if definition else [])
        warp = tk.BooleanVar(value=definition.get("warp", False) if definition else False)
        main = tk.Frame(dialog, bg="#e6f0ff", padx=24, pady=20)
        main.pack(fill="both", expand=True)
        tk.Label(
            main, text="Block name", bg="#e6f0ff", fg="#596579",
        ).pack(anchor="w")
        tk.Entry(main, textvariable=name, font=("Segoe UI", 12), width=32).pack(
            anchor="w", pady=(4, 12),
        )
        preview = tk.Label(
            main, text="", bg="#ff6680", fg="white", padx=16, pady=12,
            font=("Segoe UI", 11, "bold"),
        )
        preview.pack(pady=(0, 8))
        parts_area = tk.Frame(main, bg="#e6f0ff")
        parts_area.pack(fill="x", pady=(0, 12))

        def update_preview(*_args):
            preview.config(text=custom_block_label({"name": name.get(), "parts": parts}))
            for child in parts_area.winfo_children():
                child.destroy()
            for index, part in enumerate(parts):
                text = (
                    f"{part['text']}  ×" if part["kind"] == "label"
                    else f"{part['name']} ({part['type']})  ×"
                )
                tk.Button(
                    parts_area, text=text,
                    command=lambda part_index=index: remove_part(part_index),
                    bg="#fff1f4", fg="#8f2340", relief="flat", padx=8, pady=4,
                ).pack(side="left", padx=3, pady=3)

        def remove_part(index):
            del parts[index]
            update_preview()

        def add_input(input_type):
            prompt = tk.Toplevel(dialog)
            prompt.title("Add an input")
            prompt.transient(dialog)
            prompt.resizable(False, False)
            panel = tk.Frame(prompt, padx=14, pady=12)
            panel.pack(fill="both", expand=True)
            tk.Label(panel, text="Input name").grid(row=0, column=0, sticky="w")
            input_name = tk.StringVar(value=f"input{1 + sum(p.get('kind') == 'input' for p in parts)}")
            tk.Entry(panel, textvariable=input_name, width=24).grid(row=0, column=1, padx=8)
            chosen_type = tk.StringVar(
                value="Text" if input_type == "text" else "Number",
            )
            if input_type == "text":
                tk.Label(panel, text="Input type").grid(row=1, column=0, sticky="w", pady=(8, 0))
                tk.OptionMenu(panel, chosen_type, "Text", "Number").grid(
                    row=1, column=1, sticky="ew", padx=8, pady=(8, 0),
                )

            def commit_input():
                normalized = input_name.get().strip()
                if not normalized.isascii() or not normalized.isidentifier():
                    messagebox.showerror(
                        "Invalid input name",
                        "Use a name that starts with a letter or underscore and contains only letters, numbers, and underscores.",
                        parent=prompt,
                    )
                    return
                if any(
                    item.get("kind") == "input" and item.get("name") == normalized
                    for item in parts
                ):
                    messagebox.showerror(
                        "Duplicate input", "Each input must have a unique name.",
                        parent=prompt,
                    )
                    return
                selected_type = (
                    chosen_type.get().lower() if input_type == "text" else input_type
                )
                parts.append({
                    "kind": "input",
                    "name": normalized,
                    "type": selected_type,
                })
                prompt.destroy()
                update_preview()

            tk.Button(panel, text="Add", command=commit_input).grid(
                row=2, column=1, sticky="e", pady=(12, 0),
            )
            prompt.grab_set()
            prompt.wait_window()

        controls = tk.Frame(main, bg="#e6f0ff")
        controls.pack(fill="x", pady=(0, 12))
        tk.Button(
            controls, text="Add an input (number or text)",
            command=lambda: add_input("text"), bg="white", fg="#596579",
            relief="solid", padx=10, pady=12,
        ).pack(side="left", fill="both", expand=True, padx=(0, 6))
        tk.Button(
            controls, text="Add an input (boolean)",
            command=lambda: add_input("boolean"), bg="white", fg="#596579",
            relief="solid", padx=10, pady=12,
        ).pack(side="left", fill="both", expand=True, padx=3)
        tk.Button(
            controls, text="Add a label",
            command=lambda: self.add_custom_label(dialog, parts, update_preview),
            bg="white", fg="#596579", relief="solid", padx=10, pady=12,
        ).pack(side="left", fill="both", expand=True, padx=(6, 0))
        tk.Checkbutton(
            main, text="Run without screen refresh", variable=warp,
            bg="#e6f0ff", fg="#596579", activebackground="#e6f0ff",
        ).pack(anchor="w", pady=(0, 12))
        result = {"value": None}
        buttons = tk.Frame(main, bg="#e6f0ff")
        buttons.pack(fill="x", side="bottom")

        def accept():
            block_name = name.get().strip()
            if not block_name:
                messagebox.showerror(
                    "Block name required", "Enter a name for this block.", parent=dialog,
                )
                return
            if any(
                item.get("id") != (definition or {}).get("id")
                and item.get("name", "").casefold() == block_name.casefold()
                for item in self.custom_blocks
            ):
                messagebox.showerror(
                    "Duplicate block name",
                    "Choose a name that is not already used by another custom block.",
                    parent=dialog,
                )
                return
            result["value"] = {
                "name": block_name,
                "parts": copy.deepcopy(parts),
                "warp": warp.get(),
            }
            dialog.destroy()

        tk.Button(
            buttons, text="Cancel", command=dialog.destroy,
            bg="white", relief="solid", padx=16, pady=8,
        ).pack(side="right")
        tk.Button(
            buttons, text="OK", command=accept,
            bg="#8056d8", fg="white", relief="flat", padx=18, pady=8,
        ).pack(side="right", padx=(0, 8))
        name.trace_add("write", update_preview)
        update_preview()
        dialog.grab_set()
        self.root.wait_window(dialog)
        return result["value"]

    @staticmethod
    def add_custom_label(parent, parts, refresh):
        """Ask for a literal text segment in the custom block's label."""
        text = simpledialog.askstring(
            "Add a label", "Text to show in the block:", parent=parent,
        )
        if text and text.strip():
            parts.append({"kind": "label", "text": text.strip()})
            refresh()

    @staticmethod
    def normalize_app_settings(settings):
        """Return a sanitized settings dictionary with safe defaults."""
        defaults = {
            "command_line_prompt": False,
            "read_write_blocks": True,
            "system_blocks_enabled": False,
            "modded_blocks_enabled": False,
            "language_mode": "english",
        }
        normalized = defaults.copy()
        if not isinstance(settings, dict):
            return normalized
        language = str(settings.get("language_mode", "english")).strip().lower()
        if language not in {"english", "russian", "french", "romanian"}:
            language = "english"
        normalized["language_mode"] = language
        command_setting = settings.get("command_line_prompt", False)
        normalized["command_line_prompt"] = (
            command_setting if isinstance(command_setting, bool) else False
        )
        read_write_enabled = settings.get("read_write_blocks", True)
        normalized["read_write_blocks"] = (
            read_write_enabled if isinstance(read_write_enabled, bool) else True
        )
        system_enabled = settings.get("system_blocks_enabled", False)
        normalized["system_blocks_enabled"] = (
            system_enabled if isinstance(system_enabled, bool) else False
        )
        modded_enabled = settings.get("modded_blocks_enabled", False)
        normalized["modded_blocks_enabled"] = (
            modded_enabled if isinstance(modded_enabled, bool) else False
        )
        return normalized

    def load_app_settings(self):
        """Read saved per-user settings, reporting invalid data to the user."""
        defaults = self.normalize_app_settings({})
        app_data = os.environ.get("APPDATA")
        settings_path = (
            Path(app_data) if app_data
            else Path.home() / "AppData" / "Roaming"
        ) / "EngineEditor" / "settings.json"
        # Read the old settings location once so existing user preferences survive.
        legacy_settings_path = (
            settings_path.parent.parent / "Steveengineeditor" / "settings.json"
        )
        if not settings_path.exists() and legacy_settings_path.is_file():
            settings_path = legacy_settings_path
        try:
            loaded = json.loads(settings_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return defaults
        except (OSError, TypeError, ValueError) as error:
            messagebox.showwarning(
                "Settings not loaded",
                f"Could not read saved settings from {settings_path}:\n{error}",
                parent=self.root,
            )
            return defaults
        if not isinstance(loaded, dict):
            messagebox.showwarning(
                "Settings not loaded",
                f"Saved settings in {settings_path} must contain a JSON object.",
                parent=self.root,
            )
            return defaults
        return self.normalize_app_settings(loaded)

    def save_app_settings(self, settings=None):
        """Persist preferences atomically in the current user's roaming profile."""
        settings = self.normalize_app_settings(settings or self.app_settings)
        app_data = os.environ.get("APPDATA")
        settings_path = (
            Path(app_data) if app_data
            else Path.home() / "AppData" / "Roaming"
        ) / "EngineEditor" / "settings.json"
        temporary_path = settings_path.with_suffix(".tmp")
        try:
            settings_path.parent.mkdir(parents=True, exist_ok=True)
            temporary_path.write_text(
                json.dumps(settings, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(temporary_path, settings_path)
        except OSError as error:
            messagebox.showerror(
                "Could not save settings",
                f"Could not save settings to {settings_path}:\n{error}",
                parent=self.root,
            )
            return False
        return True

    def open_settings_dialog(self):
        """Display the app settings dialog with a locked risky option."""
        dialog = tk.Toplevel(self.root)
        dialog.title(self.tr("settings menu"))
        dialog.transient(self.root)
        dialog.resizable(False, False)
        dialog.grab_set()
        panel = tk.Frame(dialog, padx=18, pady=16)
        panel.pack(fill="both", expand=True)

        admin_access = self._is_administrator()
        command_prompt_setting = tk.BooleanVar(
            value=bool(self.app_settings.get("command_line_prompt", False))
        )
        read_write_setting = tk.BooleanVar(
            value=bool(self.app_settings.get("read_write_blocks", True))
        )
        system_blocks_setting = tk.BooleanVar(
            value=bool(self.app_settings.get("system_blocks_enabled", False))
        )
        modded_blocks_setting = tk.BooleanVar(
            value=bool(self.app_settings.get("modded_blocks_enabled", False))
        )
        language_var = tk.StringVar(
            value=(
                {"english": "English", "russian": "Russian", "french": "French", "romanian": "Romanian"}
                .get(self.app_settings.get("language_mode", "english"), "English")
            )
        )

        tk.Label(
            panel,
            text=self.tr("Settings"),
            font=("Segoe UI", 12, "bold"),
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 12))

        command_label = tk.Label(
            panel,
            text=self.tr("Not recommended: Command Prompt is powerful and can change your system. Continue only if you understand the commands."),
            justify="left",
            wraplength=420,
            fg="#333333" if admin_access else "#888888",
        )
        command_label.grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 6))
        command_checkbox = tk.Checkbutton(
            panel,
            text=self.tr("Administrator command prompt"),
            variable=command_prompt_setting,
            state=tk.NORMAL if admin_access else tk.DISABLED,
            anchor="w",
        )
        command_checkbox.grid(row=2, column=0, columnspan=2, sticky="w", pady=(0, 8))
        command_prompt_button = None
        if not admin_access:
            tk.Label(
                panel,
                text=self.tr("Restart the app as administrator to unlock this option."),
                fg="#888888",
                wraplength=420,
                justify="left",
            ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(0, 12))
        else:
            command_prompt_button = tk.Button(
                panel,
                text=self.tr("Open Command Prompt"),
                command=lambda: self.open_admin_command_prompt(dialog),
                state=tk.NORMAL if command_prompt_setting.get() else tk.DISABLED,
            )
            command_prompt_button.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(0, 12))
            command_prompt_setting.trace_add(
                "write",
                lambda *_args: (
                    command_prompt_button.config(
                        state=tk.NORMAL if command_prompt_setting.get() else tk.DISABLED
                    )
                    if command_prompt_button is not None else None
                ),
            )

        read_write_checkbox = tk.Checkbutton(
            panel,
            text=self.tr("Enable file read/write blocks"),
            variable=read_write_setting,
            anchor="w",
        )
        read_write_checkbox.grid(row=4, column=0, columnspan=2, sticky="w", pady=(0, 12))

        tk.Checkbutton(
            panel,
            text=self.tr("Enable System Blocks (advanced)"),
            variable=system_blocks_setting,
            anchor="w",
        ).grid(row=5, column=0, columnspan=2, sticky="w", pady=(0, 8))

        tk.Checkbutton(
            panel,
            text=self.tr("Enable Python Mods (runs code from the mods folder)"),
            variable=modded_blocks_setting,
            anchor="w",
        ).grid(row=6, column=0, columnspan=2, sticky="w", pady=(0, 2))
        tk.Label(
            panel,
            text=self.tr("Only enable Python mods you trust; they can access files and run code."),
            fg="#9a3412", wraplength=420, justify="left",
        ).grid(row=7, column=0, columnspan=2, sticky="w", pady=(0, 12))
        # this is the language part i think i will add more languages later but for now i will just add these four i mean im lazy
        tk.Label(panel, text=self.tr("Language")).grid(row=8, column=0, sticky="w", pady=(0, 10))
        language_menu = tk.OptionMenu(
            panel,
            language_var,
            "English",
            "Russian",
            "French",
            "Romanian",
        )
        language_menu.grid(row=8, column=1, sticky="ew", pady=(0, 10))

        def apply_settings():
            """Validate the settings form and store the chosen values."""
            chosen_language = str(language_var.get()).strip().lower()
            if chosen_language not in {"english", "russian", "french", "romanian"}:
                chosen_language = "english"
            new_settings = {
                "command_line_prompt": bool(command_prompt_setting.get()),
                "read_write_blocks": bool(read_write_setting.get()),
                "system_blocks_enabled": bool(system_blocks_setting.get()),
                "modded_blocks_enabled": bool(modded_blocks_setting.get()),
                "language_mode": chosen_language,
            }
            self.app_settings = self.normalize_app_settings(new_settings)
            if not self.save_app_settings(self.app_settings):
                return
            if self.app_settings["modded_blocks_enabled"]:
                try:
                    MOD_REGISTRY.load_directory(self.mods_directory)
                except OSError as error:
                    messagebox.showerror(
                        "Could not load mods",
                        f"Could not read the mods folder "
                        f"{self.mods_directory}:\n{error}",
                        parent=dialog,
                    )
            else:
                MOD_REGISTRY.clear()
            self.language = {
                "english": "en", "russian": "ru", "french": "fr", "romanian": "ro",
            }[self.app_settings["language_mode"]]
            if self.category == "Files" and not self.app_settings["read_write_blocks"]:
                self.category = "Events"
            if self.category == "System" and not self.app_settings["system_blocks_enabled"]:
                self.category = "Events"
            if (
                self.category not in BLOCKS
                and self.category not in MOD_REGISTRY.categories
            ) or (
                self.category in MOD_REGISTRY.categories
                and not self.app_settings["modded_blocks_enabled"]
            ):
                self.category = "Events"
            dialog.destroy()
            for widget in self.root.winfo_children():
                widget.destroy()
            self.create_ui()
            self.refresh_sprite_tabs()
            self.show_category(self.category)
            self.draw_workspace()
            self.draw_stage()
            self.status.set(self.tr("Settings saved"))

        buttons = tk.Frame(panel)
        buttons.grid(row=9, column=0, columnspan=2, sticky="e")
        tk.Button(buttons, text=self.tr("Cancel"), command=dialog.destroy).pack(side="right")
        tk.Button(
            buttons,
            text=self.tr("Save settings"),
            command=apply_settings,
            bg="#5268e8",
            fg="white",
            relief="flat",
            padx=12,
        ).pack(side="right", padx=(0, 8))
        dialog.bind("<Return>", lambda _event: apply_settings())
        dialog.bind("<Escape>", lambda _event: dialog.destroy())
        dialog.update_idletasks()
        dialog.geometry(
            f"+{self.root.winfo_rootx() + 100}+{self.root.winfo_rooty() + 100}"
        )

    def open_admin_command_prompt(self, parent):
        """Open a confirmed console only for an already elevated editor process."""
        if not self._is_administrator() or not self.app_settings["command_line_prompt"]:
            messagebox.showerror(
                "Administrator access required",
                self.tr("Restart the app as administrator to unlock this option."),
                parent=parent,
            )
            return
        if not messagebox.askyesno(
            "Administrator Command Prompt",
            self.tr("Open an administrator Command Prompt? Commands can make permanent system changes."),
            parent=parent,
        ):
            return
        try:
            subprocess.Popen(
                ["cmd.exe"],
                creationflags=subprocess.CREATE_NEW_CONSOLE, # note i wanted to add this to add a few commands to the exe but thats later for other os
                
            )
        except OSError as error:
            messagebox.showerror(
                "Could not open Command Prompt",
                str(error),
                parent=parent,
            )

    def save_project(self):
        """Write the current blocks, settings, costumes, and stage to an .sse file."""
        self.save_active_sprite()
        path = self.project_path
        if path is None:
            selected = filedialog.asksaveasfilename(
                parent=self.root,
                title="Save Steve Engine Editor project",
                defaultextension=".sse",
                filetypes=[("Steve Engine Editor project", "*.sse")],
            )
            if not selected:
                return
            path = Path(selected)
            if path.suffix.lower() != ".sse":
                path = path.with_suffix(".sse")
        project_data = {
            "format": PROJECT_FORMAT,
            "version": 1,
            "blocks": self.sprites[self.active_sprite_index].get("blocks", self.blocks),
            "game_settings": self.game_settings,
            "sprite": self.sprite_project_data(),
            "backdrops": self.backdrops,
            "sprites": self.sprites,
            "active_sprite_index": self.active_sprite_index,
            "custom_blocks": self.custom_blocks,
            "sounds": self.sounds,
            "mod_sources": dict(MOD_REGISTRY.sources),
        }
        temporary_path = None
        try:
            serialized_project = json.dumps(
                project_data, ensure_ascii=False, separators=(",", ":"),
            ).encode("utf-8")
            with tempfile.NamedTemporaryFile(
                prefix=f".{path.name}.", suffix=".tmp", dir=path.parent,
                delete=False,
            ) as temporary_file:
                temporary_path = Path(temporary_file.name)
            with zipfile.ZipFile(
                temporary_path, mode="w", compression=zipfile.ZIP_DEFLATED,
            ) as archive:
                archive.writestr("project.json", serialized_project)
            os.replace(temporary_path, path)
        except (OSError, RuntimeError, ValueError, zipfile.LargeZipFile) as error:
            if temporary_path is not None:
                try:
                    temporary_path.unlink()
                except OSError:
                    pass
            messagebox.showerror("Could not save project", str(error), parent=self.root)
            return
        self.project_path = path
        self.status.set(f"Saved {path.name}")

    def open_project(self):
        """Load and validate a saved .sse project before changing editor state."""
        selected = filedialog.askopenfilename(
            parent=self.root,
            title="Open Steve Engine Editor project",
            filetypes=[("Steve Engine Editor project", "*.sse")],
        )
        if not selected:
            return
        path = Path(selected)
        try:
            if zipfile.is_zipfile(path):
                with zipfile.ZipFile(path, mode="r") as archive:
                    try:
                        serialized_project = archive.read("project.json")
                    except KeyError as error:
                        raise ValueError(
                            "The compressed .sse file is missing project.json."
                        ) from error
                project_data = json.loads(serialized_project.decode("utf-8"))
            else:
                # Older .sse projects stored the JSON without compression.
                project_data = json.loads(path.read_text(encoding="utf-8"))
            if (
                not isinstance(project_data, dict)
                or project_data.get("format") not in (
                    PROJECT_FORMAT, *LEGACY_PROJECT_FORMATS,
                )
                or not isinstance(project_data.get("blocks"), list)
            ):
                raise ValueError("This file is not a supported .sse project.")
            loaded_blocks = []
            for saved_block in project_data["blocks"]:
                if not isinstance(saved_block, dict) or not isinstance(saved_block.get("kind"), str):
                    raise ValueError("The project contains a malformed block.")
                block = create_block(saved_block["kind"])
                block.update(saved_block)
                block_label(block)
                loaded_blocks.append(block)
            loaded_custom_blocks = project_data.get("custom_blocks", [])
            if not isinstance(loaded_custom_blocks, list):
                raise ValueError("The project contains invalid custom block definitions.")
            expand_custom_blocks([], loaded_custom_blocks)
            expand_custom_blocks(loaded_blocks, loaded_custom_blocks)
            loaded_custom_blocks = copy.deepcopy(loaded_custom_blocks)
            for definition in loaded_custom_blocks:
                normalized_body = []
                for saved_block in definition["body"]:
                    if not isinstance(saved_block, dict) or not isinstance(
                        saved_block.get("kind"), str,
                    ):
                        raise ValueError(
                            f"Custom block '{definition['name']}' has a malformed block."
                        )
                    block = create_block(saved_block["kind"])
                    block.update(saved_block)
                    block_label(block)
                    normalized_body.append(block)
                definition["body"] = normalized_body
                default_values = {
                    part["name"]: (
                        "false" if part["type"] == "boolean"
                        else "0" if part["type"] == "number" else ""
                    )
                    for part in definition["parts"]
                    if part["kind"] == "input"
                }
                preview_call = {
                    "kind": "custom_call",
                    "definition_id": definition["id"],
                    "name": definition["name"],
                    "parts": definition["parts"],
                    "values": default_values,
                }
                expand_script(
                    expand_custom_blocks([preview_call], loaded_custom_blocks)
                )
            settings = project_data.get("game_settings", {})
            if not isinstance(settings, dict):
                raise ValueError("The project contains invalid game settings.")
            sounds = project_data.get("sounds", [])
            if not isinstance(sounds, list):
                raise ValueError("The project contains invalid sound data.")
            sound_names = set()
            for sound in sounds:
                if (
                    not isinstance(sound, dict)
                    or not isinstance(sound.get("name"), str)
                    or not sound["name"].strip()
                    or sound.get("format") not in ("mp3", "wav", "ogg")
                    or not isinstance(sound.get("audio_data"), str)
                ):
                    raise ValueError("The project contains a malformed sound asset.")
                normalized_name = sound["name"].casefold()
                if normalized_name in sound_names:
                    raise ValueError("The project contains duplicate sound names.")
                sound_names.add(normalized_name)
                try:
                    audio_bytes = base64.b64decode(sound["audio_data"], validate=True)
                except (ValueError, binascii.Error) as error:
                    raise ValueError("The project contains invalid sound data.") from error
                if not audio_bytes or len(audio_bytes) > 20 * 1024 * 1024:
                    raise ValueError("Sound assets must be non-empty and no larger than 20 MB.")
            sprite = project_data.get("sprite", {})
            backdrops = project_data.get("backdrops", [])
            if not isinstance(sprite, dict) or not isinstance(backdrops, list):
                raise ValueError("The project contains invalid sprite or backdrop data.")
            costumes = sprite.get("costumes", [])
            if not isinstance(costumes, list):
                raise ValueError("The project contains invalid costume data.")
            for asset in costumes + backdrops:
                if (
                    not isinstance(asset, dict)
                    or not isinstance(asset.get("name"), str)
                    or not isinstance(asset.get("image_data"), str)
                ):
                    raise ValueError("The project contains a malformed image asset.")
            for field in ("x", "y", "size", "direction", "costume_index", "effect", "backdrop_index"):
                if field in sprite:
                    value = sprite[field]
                    if (
                        isinstance(value, bool)
                        or not isinstance(value, (int, float))
                        or not math.isfinite(value)
                    ):
                        raise ValueError(f"The project contains an invalid sprite {field}.")
                    sprite[field] = round(value)
            if "visible" in sprite and not isinstance(sprite["visible"], bool):
                raise ValueError("The project contains an invalid sprite visibility setting.")
            for field in ("velocity_x", "velocity_y", "gravity"):
                value = sprite.setdefault(field, 0.0)
                if (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(value)
                ):
                    raise ValueError(f"The project contains an invalid sprite {field}.")
            project_sprites = project_data.get("sprites")
            if project_sprites is not None:
                if not isinstance(project_sprites, list) or not project_sprites:
                    raise ValueError("The project must contain at least one sprite.")
                loaded_sprites = []
                for saved_sprite in project_sprites:
                    if not isinstance(saved_sprite, dict):
                        raise ValueError("The project contains a malformed sprite.")
                    sprite_blocks = saved_sprite.get("blocks")
                    sprite_costumes = saved_sprite.get("costumes", [])
                    if not isinstance(sprite_blocks, list) or not isinstance(sprite_costumes, list):
                        raise ValueError("A sprite has invalid blocks or costumes.")
                    for asset in sprite_costumes:
                        if (
                            not isinstance(asset, dict)
                            or not isinstance(asset.get("name"), str)
                            or not isinstance(asset.get("image_data"), str)
                        ):
                            raise ValueError("A sprite contains a malformed costume.")
                    for field in ("x", "y", "size", "direction", "costume_index", "effect", "backdrop_index"):
                        if field in saved_sprite:
                            value = saved_sprite[field]
                            if (
                                isinstance(value, bool)
                                or not isinstance(value, (int, float))
                                or not math.isfinite(value)
                            ):
                                raise ValueError(f"A sprite has an invalid {field} value.")
                            saved_sprite[field] = round(value)
                    if "visible" in saved_sprite and not isinstance(saved_sprite["visible"], bool):
                        raise ValueError("A sprite has an invalid visibility setting.")
                    for field in ("velocity_x", "velocity_y", "gravity"):
                        value = saved_sprite.setdefault(field, 0.0)
                        if (
                            isinstance(value, bool)
                            or not isinstance(value, (int, float))
                            or not math.isfinite(value)
                        ):
                            raise ValueError(f"A sprite has an invalid {field} value.")
                    if not isinstance(saved_sprite.get("name", "Sprite"), str):
                        raise ValueError("A sprite name must be text.")
                    sprite_script = []
                    for saved_block in sprite_blocks:
                        if not isinstance(saved_block, dict) or not isinstance(saved_block.get("kind"), str):
                            raise ValueError("A sprite contains a malformed block.")
                        block = create_block(saved_block["kind"])
                        block.update(saved_block)
                        block_label(block)
                        sprite_script.append(block)
                    expand_custom_blocks(sprite_script, loaded_custom_blocks)
                    loaded_sprites.append({**saved_sprite, "blocks": sprite_script})
                selected_sprite = project_data.get("active_sprite_index", 0)
                if not isinstance(selected_sprite, int) or not 0 <= selected_sprite < len(loaded_sprites):
                    raise ValueError("The project has an invalid selected sprite.")
            else:
                loaded_sprites = None
                selected_sprite = 0
        except (
            OSError, RuntimeError, UnicodeDecodeError, json.JSONDecodeError,
            KeyError, TypeError, ValueError, zipfile.BadZipFile,
        ) as error:
            messagebox.showerror("Could not open project", str(error), parent=self.root)
            return
        self.image_cache.clear()
        self.game_settings.update(settings)
        self.custom_blocks = loaded_custom_blocks
        self.editing_custom_block_id = None
        self.backdrops = backdrops
        self.sounds = sounds
        if loaded_sprites is not None:
            self.sprites = loaded_sprites
            self.active_sprite_index = selected_sprite
            selected = self.sprites[selected_sprite]
            self.blocks = copy.deepcopy(selected["blocks"])
            self.load_sprite_project_data(selected, backdrops)
        else:
            self.sprites = [{**sprite, "blocks": loaded_blocks}]
            self.active_sprite_index = 0
            self.blocks = loaded_blocks
            self.load_sprite_project_data(sprite, backdrops)
        self.project_path = path
        self.finish_custom_edit_button.pack_forget()
        self.script_workspace_title.config(text=self.tr("SCRIPT WORKSPACE"))
        self.refresh_sprite_tabs()
        self.sound_status.set(f"{len(self.sounds)} imported sound(s)")
        self.show_category(self.category)
        self.draw_workspace()
        self.draw_stage()
        self.status.set(f"Opened {path.name}")

    def import_image(self):
        """Choose a PNG/JPG file and add it as an image block."""
        image_path = filedialog.askopenfilename(
            parent=self.root,
            title="Import a sprite image",
            filetypes=[
                ("PNG and JPEG images", "*.png *.jpg *.jpeg"),
                ("PNG images", "*.png"),
                ("JPEG images", "*.jpg *.jpeg"),
            ],
        )
        if not image_path:
            return
        path = Path(image_path)
        if path.stat().st_size > 12 * 1024 * 1024:
            messagebox.showerror(
                "Image is too large",
                "Choose an image smaller than 12 MB.",
                parent=self.root,
            )
            return
        try:
            with Image.open(path) as image:
                if image.format not in ("PNG", "JPEG"):
                    raise ValueError("Choose a PNG or JPEG image.")
                image.verify()
            image_data = base64.b64encode(path.read_bytes()).decode("ascii")
        except (OSError, ValueError) as error:
            messagebox.showerror("Could not import image", str(error), parent=self.root)
            return
        self.add_image_block(path.name, image_data)

    def import_sound(self):
        """Import one MP3, WAV, or OGG file into the current project."""
        selected = filedialog.askopenfilename(
            parent=self.root,
            title="Import a sound",
            filetypes=[
                ("Supported sounds", "*.mp3 *.wav *.ogg"),
                ("MP3 audio", "*.mp3"),
                ("WAV audio", "*.wav"),
                ("OGG audio", "*.ogg"),
            ],
        )
        if not selected:
            return
        path = Path(selected)
        file_format = path.suffix.lower().lstrip(".")
        try:
            if file_format not in ("mp3", "wav", "ogg"):
                raise ValueError("Choose an MP3, WAV, or OGG sound.")
            if path.stat().st_size > 20 * 1024 * 1024:
                raise ValueError("Choose a sound smaller than 20 MB.")
            audio_bytes = path.read_bytes()
            if not audio_bytes:
                raise ValueError("The selected sound file is empty.")
        except OSError as error:
            messagebox.showerror("Could not import sound", str(error), parent=self.root)
            return
        except ValueError as error:
            messagebox.showerror("Could not import sound", str(error), parent=self.root)
            return

        base_name = path.stem[:40] or "Sound"
        name = base_name
        suffix = 2
        existing_names = {sound["name"].casefold() for sound in self.sounds}
        while name.casefold() in existing_names or name.casefold() in {
            "meow", "pop", "bell", "drum",
        }:
            name = f"{base_name[:35]} {suffix}"
            suffix += 1
        self.sounds.append({
            "name": name,
            "format": file_format,
            "audio_data": base64.b64encode(audio_bytes).decode("ascii"),
        })
        self.sound_status.set(f"{len(self.sounds)} imported sound(s)")
        self.refresh_explorer()
        self.status.set(f"Imported sound {name}")

    def select_asset_file(self, title):
        """Choose and validate an image for a costume or stage backdrop."""
        selected = filedialog.askopenfilename(
            parent=self.root,
            title=title,
            filetypes=[
                ("PNG and JPEG images", "*.png *.jpg *.jpeg"),
                ("PNG images", "*.png"),
                ("JPEG images", "*.jpg *.jpeg"),
            ],
        )
        if not selected:
            return None
        path = Path(selected)
        try:
            if path.stat().st_size > 12 * 1024 * 1024:
                raise ValueError("Choose an image smaller than 12 MB.")
            with Image.open(path) as image:
                if image.format not in ("PNG", "JPEG"):
                    raise ValueError("Choose a PNG or JPEG image.")
                image.verify()
            return {
                "name": path.stem[:40] or "Image",
                "image_data": base64.b64encode(path.read_bytes()).decode("ascii"),
            }
        except (OSError, ValueError) as error:
            messagebox.showerror("Could not import image", str(error), parent=self.root)
            return None

    def import_costume(self):
        """Add an image to this sprite's costume list."""
        asset = self.select_asset_file("Add a sprite costume")
        if asset is None:
            return
        self.sprite_costumes.append(asset)
        self.sprite_costume_index = len(self.sprite_costumes) - 1
        if self.sprite_name == "Sprite1":
            self.sprite_name = asset["name"]
        self.sprite_name_var.set(self.sprite_name)
        self.sync_active_sprite()
        self.refresh_sprite_tabs()
        self.update_asset_status()
        self.draw_stage()
        self.status.set(f"Added costume: {asset['name']}")

    def import_backdrop(self):
        """Add an image to the stage's backdrop list."""
        asset = self.select_asset_file("Add a stage backdrop")
        if asset is None:
            return
        self.backdrops.append(asset)
        self.backdrop_index = len(self.backdrops) - 1
        self.refresh_explorer()
        self.update_asset_status()
        self.draw_stage()
        self.status.set(f"Added backdrop: {asset['name']}")

    def update_asset_status(self):
        """Refresh the small label showing the selected costume and backdrop."""
        costume = (
            self.sprite_costumes[self.sprite_costume_index]["name"]
            if self.sprite_costumes else "Default"
        )
        backdrop = (
            self.backdrops[self.backdrop_index]["name"]
            if self.backdrops else "White"
        )
        self.asset_status.set(
            f"{costume}  |  {backdrop} ({len(self.backdrops)})"
        )

    def next_costume(self):
        """Select the next sprite costume, wrapping around at the end."""
        if self.sprite_costumes:
            self.sprite_costume_index = (
                self.sprite_costume_index + 1
            ) % len(self.sprite_costumes)
            self.update_asset_status()
            self.draw_stage()

    def next_backdrop(self):
        """Select the next stage backdrop, wrapping around at the end."""
        if self.backdrops:
            self.backdrop_index = (self.backdrop_index + 1) % len(self.backdrops)
            self.update_asset_status()
            self.draw_stage()

    def sprite_project_data(self):
        """Collect sprite settings in a JSON-friendly dictionary."""
        return {
            "name": self.sprite_name,
            "x": round(self.sprite_x - 200),
            "y": round(205 - self.sprite_y),
            # Runtime blocks can produce fractional values (for example, point
            # towards mouse-pointer). Keep saved sprite fields in the integer
            # format that open_project validates.
            "size": round(self.sprite_size),
            "direction": round(self.sprite_angle) % 360,
            "visible": self.sprite_visible,
            "velocity_x": getattr(self, "sprite_velocity_x", 0.0),
            "velocity_y": getattr(self, "sprite_velocity_y", 0.0),
            "gravity": getattr(self, "sprite_gravity", 0.0),
            "costumes": self.sprite_costumes,
            "costume_index": int(self.sprite_costume_index),
            "effect": round(self.sprite_effect),
            "layer_front": self.sprite_layer_front,
            "backdrop_index": int(self.backdrop_index),
        }

    def load_sprite_project_data(self, sprite, backdrops):
        """Restore sprite and backdrop settings and refresh their controls."""
        self.sprite_name = sprite.get("name", "Sprite1")
        self.sprite_x = 200 + sprite.get("x", 0)
        self.sprite_y = 205 - sprite.get("y", 0)
        self.sprite_size = max(10, min(300, sprite.get("size", 100)))
        self.sprite_angle = sprite.get("direction", 90) % 360
        self.sprite_visible = sprite.get("visible", True)
        self.sprite_velocity_x = sprite.get("velocity_x", 0.0)
        self.sprite_velocity_y = sprite.get("velocity_y", 0.0)
        self.sprite_gravity = sprite.get("gravity", 0.0)
        self.sprite_costumes = sprite.get("costumes", [])
        self.sprite_costume_index = (
            sprite.get("costume_index", 0) % len(self.sprite_costumes)
            if self.sprite_costumes else 0
        )
        self.sprite_effect = sprite.get("effect", 0)
        self.sprite_layer_front = sprite.get("layer_front", True)
        self.backdrops = backdrops
        self.backdrop_index = sprite.get("backdrop_index", 0)
        if self.backdrops:
            self.backdrop_index %= len(self.backdrops)
        self._updating_sprite_controls = True
        self.sprite_name_var.set(self.sprite_name)
        self.sprite_x_var.set(str(round(self.sprite_x - 200)))
        self.sprite_y_var.set(str(round(205 - self.sprite_y)))
        self.sprite_size_var.set(str(self.sprite_size))
        self.sprite_direction_var.set(str(self.sprite_angle))
        self.sprite_visible_var.set(self.sprite_visible)
        self._updating_sprite_controls = False
        self.update_asset_status()

    def update_sprite_properties(self, *_args):
        """Read valid inspector values and apply them to the live sprite."""
        if self._updating_sprite_controls:
            return
        previous_name = self.sprites[self.active_sprite_index].get(
            "name", "Sprite1",
        )
        try:
            self.sprite_name = self.sprite_name_var.get().strip() or "Sprite1"
            self.sprite_x = 200 + int(self.sprite_x_var.get())
            self.sprite_y = 205 - int(self.sprite_y_var.get())
            self.sprite_size = max(10, min(300, int(self.sprite_size_var.get())))
            self.sprite_angle = int(self.sprite_direction_var.get()) % 360
        except ValueError:
            return
        self.sync_active_sprite()
        if self.sprite_name != previous_name:
            self.refresh_sprite_tabs()
        self.draw_stage()

    def toggle_sprite_visibility(self):
        """Apply the Show checkbox value to the sprite on the stage."""
        self.sprite_visible = self.sprite_visible_var.get()
        self.sync_active_sprite()
        self.draw_stage()

    def open_paint_editor(self):
        """Open the drawing window and connect its Save button to this editor."""
        PaintEditor(self.root, self.add_drawing_block)

    def open_physics_hitbox_editor(self):
        """Edit physics rectangles and joints on the selected sprite costume."""
        if not self.sprite_costumes:
            messagebox.showinfo(
                "No costume selected",
                "Import a costume image before editing physics hitboxes.",
                parent=self.root,
            )
            return
        costume = self.sprite_costumes[self.sprite_costume_index]
        try:
            image = decode_image_data(costume["image_data"])
        except (KeyError, ValueError, OSError) as error:
            messagebox.showerror(
                "Could not open physics hitboxes", str(error), parent=self.root,
            )
            return

        def save_parts(parts):
            costume["physics_parts"] = parts
            self.sync_active_sprite()
            self.draw_stage()
            self.status.set(f"Saved physics parts for {costume['name']}")

        PhysicsHitboxEditor(
            self.root, image, costume.get("physics_parts", []), save_parts,
        )

    def add_drawing_block(self, image_data):
        """Receive a finished drawing from PaintEditor as a new image block."""
        self.add_image_block("Drawing", image_data)
        return True

    def add_image_block(self, name, image_data):
        """Store a picture as a positioned image block in the project."""
        block = create_block("image")
        block.update(
            name=Path(name).stem[:40] or "Image",
            image_data=image_data,
            workspace_x=24,
            workspace_y=max(
                (
                    item.get("workspace_y", 20 + index * 64)
                    for index, item in enumerate(self.blocks)
                ),
                default=12,
            ) + 66,
        )
        self.blocks.append(block)
        self.draw_workspace()
        self.draw_stage()
        self.status.set(f"Added image sprite: {block['name']}")
        self.script_canvas.yview_moveto(1.0)

    def organize_blocks(self):
        """Lay blocks out vertically in their current execution order."""
        blocks = self.ordered_blocks()
        x = 24
        y = 24
        for block in blocks:
            block["workspace_x"] = x
            block["workspace_y"] = y
            y += 68
        self.draw_workspace()
        self.script_canvas.xview_moveto(0)
        self.script_canvas.yview_moveto(0)
        self.status.set("Blocks organized in top-to-bottom script order.")

    def on_key_press(self, event):
        """Run the script assigned to the key that was just pressed."""
        key = event.keysym.lower()
        if event.widget.winfo_class() in ("Entry", "TEntry", "Text", "Spinbox", "TSpinbox"):
            return
        self.run_script(self.normalize_key(key))

    def sensing_reporter(self, expression):
        """Resolve a spatial reporter against the current editor stage."""
        kind, arguments = parse_reporter_expression(expression)
        pointer_x, pointer_y = self.root.winfo_pointerxy()
        stage_width = max(self.stage.winfo_width(), 360)
        stage_height = max(self.stage.winfo_height(), 410)
        stage_x = (
            pointer_x - self.stage.winfo_rootx() - stage_width / 2 + 200
        )
        stage_y = (
            self.stage.winfo_rooty() + stage_height / 2 - pointer_y + 205
        )
        backdrop_data = (
            self.backdrops[self.backdrop_index]["image_data"]
            if self.backdrops else None
        )
        actor_image_data = (
            self.sprite_costumes[self.sprite_costume_index]["image_data"]
            if self.sprite_costumes else None
        )
        return evaluate_spatial_reporter(
            kind, arguments,
            self.sprite_x, self.sprite_y, self.sprite_size,
            stage_width, stage_height, stage_x, stage_y,
            self.sprites, self.active_sprite_index,
            backdrop_data, actor_image_data,
        )

    @staticmethod
    def normalize_key(key):
        """Map Tkinter key names to the simpler names used by key blocks."""
        aliases = {
            "return": "enter",
            "kp_enter": "enter",
            "space": "space",
            "prior": "page_up",
            "next": "page_down",
        }
        return aliases.get(key.lower(), key.lower())

    def begin_palette_drag(self, event, kind):
        """Remember which palette block may be dragged into the workspace."""
        self.palette_drag_kind = kind
        self.palette_drag_origin = (event.x_root, event.y_root)
        self.palette_dragging = False
        self.drop_shadow_position = None

    def move_palette_drag(self, event):
        """Show the drop preview while a palette block is moved."""
        if self.palette_drag_origin is None:
            return "break"
        start_x, start_y = self.palette_drag_origin
        if abs(event.x_root - start_x) + abs(event.y_root - start_y) >= 8:
            self.palette_dragging = True
            self.status.set("Drop the block into the script workspace.")
            target_widget = self.root.winfo_containing(event.x_root, event.y_root)
            while target_widget is not None and target_widget is not self.script_canvas:
                target_widget = getattr(target_widget, "master", None)
            if target_widget is self.script_canvas:
                canvas_x = self.script_canvas.canvasx(
                    event.x_root - self.script_canvas.winfo_rootx(),
                )
                canvas_y = self.script_canvas.canvasy(
                    event.y_root - self.script_canvas.winfo_rooty(),
                )
                drop_x, drop_y = canvas_x - 24, canvas_y - 24
                self.drop_shadow_position = self.find_bottom_snap_position(
                    drop_x, drop_y,
                )
                if self.drop_shadow_position is not None:
                    self.play_snap_sound()
                else:
                    self.drop_shadow_position = self.find_drop_position(
                        drop_x, drop_y,
                    )
                self.draw_drop_shadow(self.palette_drag_kind, self.drop_shadow_position)
            else:
                self.clear_drop_shadow()

    def finish_palette_drag(self, event):
        """Add a dragged block at its preview location, or handle a simple click."""
        kind = self.palette_drag_kind
        if kind is None:
            return "break"

        if self.palette_dragging:
            target_widget = self.root.winfo_containing(event.x_root, event.y_root)
            while target_widget is not None and target_widget is not self.script_canvas:
                target_widget = getattr(target_widget, "master", None)
            if target_widget is self.script_canvas:
                canvas_x = self.script_canvas.canvasx(
                    event.x_root - self.script_canvas.winfo_rootx()
                )
                canvas_y = self.script_canvas.canvasy(
                    event.y_root - self.script_canvas.winfo_rooty()
                )
                block = self.create_palette_block(kind)
                drop_x, drop_y = canvas_x - 24, canvas_y - 24
                position = self.find_bottom_snap_position(drop_x, drop_y)
                if position is not None:
                    self.play_snap_sound()
                else:
                    position = self.find_drop_position(drop_x, drop_y)
                block["workspace_x"], block["workspace_y"] = position
                self.blocks.append(block)
                self.draw_workspace()
                self.status.set("Block added to the workspace.")
        else:
            self.add_block(kind)

        self.palette_drag_kind = None
        self.palette_custom_definition_id = None
        self.palette_drag_origin = None
        self.palette_dragging = False
        self.drop_shadow_position = None
        self.clear_drop_shadow()

    def add_block(self, kind):
        """Add a new block at the bottom of the current workspace layout."""
        block = self.create_palette_block(kind)
        block["workspace_x"] = 24
        if self.blocks:
            block["workspace_y"] = max(
                self.workspace_block_bottom(item, index)
                for index, item in enumerate(self.blocks)
            )
        else:
            block["workspace_y"] = 12
        self.blocks.append(block)
        self.draw_workspace()
        self.status.set("Drag blocks freely; scripts run from top to bottom.")
        self.script_canvas.yview_moveto(1.0)

    def ordered_blocks(self):
        """Return blocks from top to bottom, matching their script order."""
        return sorted(
            self.blocks,
            key=lambda block: (
                block.get("workspace_y", 0),
                block.get("workspace_x", 0),
            ),
        )

    def on_workspace_resize(self, event):
        """Redraw the workspace when its visible area changes size."""
        if event.width > 1 and event.height > 1:
            self.draw_workspace()

    def draw_workspace(self):
        """Draw the grid, each block, and any notes in the workspace."""
        if not hasattr(self, "script_canvas"):
            return
        canvas = self.script_canvas
        canvas.delete("all")
        width = max(canvas.winfo_width(), 600)
        height = max(canvas.winfo_height(), 500)
        world_width = max(1600, width)
        world_height = max(1200, height)
        for x in range(12, world_width, 24):
            for y in range(12, world_height, 24):
                canvas.create_oval(
                    x, y, x + 2, y + 2, fill="#303849", outline="",
                    tags=("workspace-grid",),
                )
        blocks_with_indices = sorted(
            enumerate(self.blocks),
            key=lambda pair: (
                pair[1].get("workspace_y", 20 + pair[0] * 64),
                pair[1].get("workspace_x", 24),
            ),
        )
        for index, block in blocks_with_indices:
            left = block.get("workspace_x", 24)
            top = block.get("workspace_y", 20 + index * 64)
            label = block_label(block)
            right = left + min(max(len(label) * 8 + 44, 220), 480)
            if block["kind"] == "custom_call":
                color = COLORS["Defined Blocks"]
            elif block["kind"].startswith("mod:"):
                definition = MOD_REGISTRY.get(block["kind"])
                if definition is None:
                    color = COLORS["Modded Blocks"]
                else:
                    color = (
                        definition.color
                        or MOD_REGISTRY.category_style(definition.category)[1]
                    )
            else:
                category = next(
                    name for name, definitions in BLOCKS.items()
                    if any(defined_kind == block["kind"] for defined_kind, _ in definitions)
                )
                color = COLORS[category]
            points = [
                left, top + 9, left + 13, top + 9, left + 13, top,
                left + 42, top, left + 42, top + 9, right, top + 9,
                right, top + 43, left + 42, top + 43, left + 42, top + 51,
                left + 13, top + 51, left + 13, top + 43, left, top + 43,
            ]
            tag = f"block-{index}"
            art = self.workspace_block_art(
                block["kind"], color, right - left, height=51,
            )
            if art is None:
                canvas.create_polygon(
                    points, fill=color, outline=color, width=1,
                    tags=("script-block", tag),
                )
            else:
                canvas.create_image(
                    left, top, anchor="nw", image=art,
                    tags=("script-block", tag),
                )
            canvas.create_text(
                left + 14, top + 27, text=label, anchor="w",
                fill="white", font=("Segoe UI", 10, "bold"),
                tags=("script-block", tag),
            )
            note = block.get("note")
            if note:
                note_lines = note.splitlines()[:4]
                note_text = "\n".join(note_lines)
                note_height = 16 * len(note_lines) + 12
                canvas.create_rectangle(
                    left, top + 55, left + 300, top + 55 + note_height,
                    fill="#353124", outline="#776b3f",
                    tags=("block-note", tag),
                )
                canvas.create_text(
                    left + 8, top + 61, text=note_text,
                    anchor="nw", width=284, fill="#f3df9c",
                    font=("Segoe UI", 9), tags=("block-note", tag),
                )
            canvas.tag_bind(tag, "<ButtonPress-1>", lambda event, i=index: self.begin_drag(event, i))
            canvas.tag_bind(tag, "<B1-Motion>", self.drag_block)
            canvas.tag_bind(tag, "<ButtonRelease-1>", self.end_drag)
            canvas.tag_bind(tag, "<Double-Button-1>", self.edit_current_block)
            canvas.tag_bind(tag, "<Button-3>", self.show_block_menu)
        canvas.configure(scrollregion=(0, 0, world_width, world_height))

    def block_index_from_event(self, event):
        """Find which workspace block tag was clicked by a mouse event."""
        for tag in event.widget.gettags("current"):
            if tag.startswith("block-"):
                return int(tag.removeprefix("block-"))
        return None

    def edit_current_block(self, event):
        """Open the settings dialog for the block under the pointer."""
        index = self.block_index_from_event(event)
        if index is not None:
            self.edit_block(index)

    def remove_current_block(self, event):
        """Delete the block under the pointer."""
        index = self.block_index_from_event(event)
        if index is not None:
            self.remove_block(index)

    def show_block_menu(self, event):
        """Show right-click actions for adding notes or deleting a block."""
        index = self.block_index_from_event(event)
        if index is None:
            return
        menu = tk.Menu(self.root, tearoff=False)
        menu.add_command(
            label="Add / edit note",
            command=lambda: self.edit_note(index),
        )
        if self.blocks[index].get("note"):
            menu.add_command(
                label="Remove note",
                command=lambda: self.clear_note(index),
            )
        menu.add_separator()
        menu.add_command(
            label="Remove block",
            command=lambda: self.remove_block(index),
        )
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def edit_note(self, index):
        """Create or edit the short note attached to one block."""
        note = simpledialog.askstring(
            "Block note",
            "Add a note to this block:",
            initialvalue=self.blocks[index].get("note", ""),
            parent=self.root,
        )
        if note is None:
            return
        note = note.strip()
        if note:
            self.blocks[index]["note"] = note
        else:
            self.blocks[index].pop("note", None)
        self.draw_workspace()

    def clear_note(self, index):
        """Remove the note from one block and redraw the workspace."""
        self.blocks[index].pop("note", None)
        self.draw_workspace()

    def begin_drag(self, event, index):
        """Record the pointer offset before moving an existing workspace block."""
        self.drag_index = index
        block = self.blocks[index]
        self.drag_pointer = (
            self.script_canvas.canvasx(event.x),
            self.script_canvas.canvasy(event.y),
        )
        self.drag_offset = (
            self.drag_pointer[0] - block.get("workspace_x", 24),
            self.drag_pointer[1] - block.get("workspace_y", 20),
        )
        self.drop_shadow_position = None

    def drag_block(self, event):
        """Move a block and continuously calculate its snap-preview position."""
        if self.drag_index is None:
            return
        pointer_x = self.script_canvas.canvasx(event.x)
        pointer_y = self.script_canvas.canvasy(event.y)
        delta_x = pointer_x - self.drag_pointer[0]
        delta_y = pointer_y - self.drag_pointer[1]
        tag = f"block-{self.drag_index}"
        self.script_canvas.move(tag, delta_x, delta_y)
        block = self.blocks[self.drag_index]
        block["workspace_x"] = max(12, int(block.get("workspace_x", 24) + delta_x))
        block["workspace_y"] = max(12, int(block.get("workspace_y", 20) + delta_y))
        self.drag_pointer = (pointer_x, pointer_y)
        drop_x = pointer_x - self.drag_offset[0]
        drop_y = pointer_y - self.drag_offset[1]
        self.drop_shadow_position = self.find_bottom_snap_position(
            drop_x, drop_y, moving_index=self.drag_index,
        )
        if self.drop_shadow_position is not None:
            self.play_snap_sound()
        else:
            self.drop_shadow_position = self.find_drop_position(
                drop_x, drop_y, moving_index=self.drag_index,
            )
        self.draw_drop_shadow(block["kind"], self.drop_shadow_position)

    def end_drag(self, event):
        """Place the dragged block at the same spot shown by the preview."""
        if self.drag_index is None:
            return
        block = self.blocks[self.drag_index]
        release_x = self.script_canvas.canvasx(event.x) - self.drag_offset[0]
        release_y = self.script_canvas.canvasy(event.y) - self.drag_offset[1]
        bottom_snap = self.find_bottom_snap_position(
            release_x, release_y, moving_index=self.drag_index,
        )
        if bottom_snap is not None:
            block["workspace_x"], block["workspace_y"] = bottom_snap
            self.play_snap_sound()
        elif self.drop_shadow_position is not None:
            block["workspace_x"], block["workspace_y"] = self.drop_shadow_position
        self.drag_index = None
        self.drop_shadow_position = None
        self.clear_drop_shadow()
        self.draw_workspace()

    def play_snap_sound(self):
        """Play snap audio immediately, at most once every half second."""
        if SNAP_SOUND is None:
            return
        now = time.monotonic()
        if now - self.last_snap_sound_at < SNAP_SOUND_INTERVAL_SECONDS:
            return
        try:
            SNAP_SOUND.play()
        except pygame.error:
            return
        self.last_snap_sound_at = now

    def find_bottom_snap_position(self, x, y, moving_index=None):
        """Return a block's bottom connector when the release is within 15 px."""
        for index, block in enumerate(self.blocks):
            if index == moving_index:
                continue
            block_x = block.get("workspace_x", 24)
            connector_y = self.workspace_block_bottom(block, index)
            if math.hypot(block_x - x, connector_y - y) <= 15:
                return max(12, int(block_x)), max(12, int(connector_y))
        return None

    @staticmethod
    def workspace_block_bottom(block, index):
        """Return the visible bottom edge, including any note attached below it."""
        block_y = block.get("workspace_y", 20 + index * 64)
        note = block.get("note", "")
        if note:
            note_height = 16 * min(len(note.splitlines()), 4)
            return block_y + 68 + note_height
        return block_y + WORKSPACE_BLOCK_HEIGHT

    def find_drop_position(self, x, y, moving_index=None):
        """Snap near a block connector; otherwise retain a free workspace position."""
        # Candidate points are the tops and bottoms of every other block.
        candidates = []
        for index, block in enumerate(self.blocks):
            if index == moving_index:
                continue
            block_x = block.get("workspace_x", 24)
            block_y = block.get("workspace_y", 20 + index * 64)
            candidates.extend((
                (block_x, block_y, x, y),
                (block_x, self.workspace_block_bottom(block, index), x, y),
            ))
        if candidates:
            best = min(candidates, key=lambda item: (item[0] - x) ** 2 + (item[1] - y) ** 2)
            if abs(best[0] - x) <= 110 and abs(best[1] - y) <= 38:
                return max(12, int(best[0])), max(12, int(best[1]))
        return max(12, int(x)), max(12, int(y))

    def draw_drop_shadow(self, kind, position):
        """Draw a dashed block-shaped preview at the proposed drop position."""
        self.clear_drop_shadow()
        if position is None:
            return
        x, y = position
        label = block_label(self.create_palette_block(kind))
        right = x + min(max(len(label) * 8 + 44, 220), 480)
        points = [
            x, y + 9, x + 13, y + 9, x + 13, y,
            x + 42, y, x + 42, y + 9, right, y + 9,
            right, y + 43, x + 42, y + 43, x + 42, y + 51,
            x + 13, y + 51, x + 13, y + 43, x, y + 43,
        ]
        shadow = self.script_canvas.create_polygon(
            points, fill="#c5cbd3", outline="#8993a0", dash=(4, 3),
            width=2, tags=("drop-shadow",),
        )
        text = self.script_canvas.create_text(
            x + 14, y + 27, text=label, anchor="w",
            fill="#77818d", font=("Segoe UI", 10, "bold"),
            tags=("drop-shadow",),
        )
        self.drop_shadow_ids = [shadow, text]

    def clear_drop_shadow(self):
        """Remove all canvas items used for the temporary drop preview."""
        if hasattr(self, "script_canvas"):
            for item in self.drop_shadow_ids:
                self.script_canvas.delete(item)
        self.drop_shadow_ids = []

    def remove_block(self, index):
        """Delete a block by its index and redraw the workspace."""
        block = self.blocks[index]
        image_data = block.get("image_data")
        if image_data:
            self.image_cache.discard_asset(image_data)
        del self.blocks[index]
        self.draw_workspace()
        self.status.set("Block removed.")

    def ask_number_or_input(self, title, prompt, initial, minimum=None, maximum=None, integer=False):
        """Read a numeric setting or a $customInput placeholder."""
        value = simpledialog.askstring(
            title,
            f"{prompt} (or use $inputName):",
            initialvalue=str(initial),
            parent=self.root,
        )
        if value is None:
            return None
        value = value.strip()
        if value.startswith("$") and value[1:].isascii() and value[1:].isidentifier():
            return value
        try:
            number = float(value)
        except ValueError:
            number = float("nan")
        if not math.isfinite(number):
            messagebox.showerror(
                "Invalid number",
                f"Enter a finite number or a custom input such as $steps.",
                parent=self.root,
            )
            return None
        if (minimum is not None and number < minimum) or (
            maximum is not None and number > maximum
        ):
            messagebox.showerror(
                "Number out of range",
                f"Choose a value from {minimum} to {maximum}.",
                parent=self.root,
            )
            return None
        if integer:
            if not number.is_integer():
                messagebox.showerror(
                    "Whole number required",
                    "Enter a whole number or a custom input.",
                    parent=self.root,
                )
                return None
            return int(number)
        return number

    def edit_block(self, index):
        """Show the appropriate settings dialog for the selected block type."""
        block = self.blocks[index]
        kind = block["kind"]
        mod_definition = MOD_REGISTRY.get(kind)
        if mod_definition is not None:
            values = dict(block.get("inputs", {}))
            for input_definition in mod_definition.inputs:
                value = simpledialog.askstring(
                    "Edit modded block input",
                    f"Value for {input_definition.name} "
                    f"({input_definition.input_type}):",
                    initialvalue=str(values.get(
                        input_definition.name, input_definition.default,
                    )),
                    parent=self.root,
                )
                if value is None:
                    return
                try:
                    values[input_definition.name] = coerce_input(
                        value, input_definition,
                    )
                except ValueError as error:
                    messagebox.showerror(
                        "Invalid modded block input", str(error), parent=self.root,
                    )
                    return
            block["inputs"] = values
            block["mod_label"] = MOD_REGISTRY.label(block)
        elif kind == "custom_call":
            values = dict(block.get("values", {}))
            for part in block.get("parts", []):
                if part.get("kind") != "input":
                    continue
                input_name = part["name"]
                value = simpledialog.askstring(
                    "Edit custom block input",
                    f"Value for {input_name} ({part['type']}):",
                    initialvalue=str(values.get(input_name, "")),
                    parent=self.root,
                )
                if value is None:
                    return
                value = value.strip()
                if part["type"] == "boolean":
                    normalized = value.lower()
                    if normalized not in ("true", "false", "yes", "no", "1", "0"):
                        messagebox.showerror(
                            "Invalid boolean",
                            f"Enter true or false for '{input_name}'.",
                            parent=self.root,
                        )
                        return
                    value = "true" if normalized in ("true", "yes", "1") else "false"
                elif part["type"] == "number":
                    try:
                        number = float(value)
                        if not math.isfinite(number):
                            raise ValueError("Input must be finite.")
                    except ValueError:
                        messagebox.showerror(
                            "Invalid number",
                            f"Enter a number for '{input_name}'.",
                            parent=self.root,
                        )
                        return
                values[input_name] = value
            block["values"] = values
            block["label"] = custom_block_label(
                {"name": block["name"], "parts": block.get("parts", [])},
                values,
            )
        elif kind == "key":
            value = simpledialog.askstring(
                "Edit block",
                "Key name (for example: space, a, enter):",
                initialvalue=block["key"],
                parent=self.root,
            )
            if value and value.strip():
                block["key"] = value.strip().lower()
        elif kind == "move":
            value = self.ask_number_or_input(
                "Edit block", "Move how many steps?", block["steps"],
            )
            if value is not None:
                block["steps"] = value
        elif kind == "turn":
            value = self.ask_number_or_input(
                "Edit block", "Turn how many degrees?", block["degrees"],
            )
            if value is not None:
                block["degrees"] = value
        elif kind == "turn_counterclockwise":
            value = self.ask_number_or_input(
                "Edit block", "Turn counterclockwise how many degrees?",
                block["degrees"],
            )
            if value is not None:
                block["degrees"] = value
        elif kind in ("glide", "glide_random"):
            seconds = self.ask_number_or_input(
                "Edit glide", "Glide duration in seconds (0.1 to 60):",
                block["seconds"], minimum=0.1, maximum=60,
            )
            if seconds is None:
                return
            block["seconds"] = seconds
            if kind == "glide":
                x = self.ask_number_or_input(
                    "Edit glide", "Destination X", block["x"],
                    minimum=-1000, maximum=1000,
                )
                if x is None:
                    return
                y = self.ask_number_or_input(
                    "Edit glide", "Destination Y", block["y"],
                    minimum=-1000, maximum=1000,
                )
                if y is None:
                    return
                block.update(x=x, y=y)
        elif kind == "point_direction":
            value = self.ask_number_or_input(
                "Edit block", "Direction in degrees (0 is up, 90 is right):",
                block["degrees"],
            )
            if value is not None:
                block["degrees"] = value
        elif kind in ("touching", "distance_to"):
            choices = ["mouse-pointer"]
            if kind == "touching":
                choices.append("edge")
            choices.extend(
                sprite.get("name", f"Sprite {index + 1}")
                for index, sprite in enumerate(self.sprites)
                if index != self.active_sprite_index
            )
            selected = simpledialog.askstring(
                "Edit sensing target",
                f"Target ({', '.join(choices)}):",
                initialvalue=block["target"],
                parent=self.root,
            )
            if selected is not None:
                selected = selected.strip()
                if selected not in choices:
                    messagebox.showerror(
                        "Invalid sensing target",
                        f"Choose one of: {', '.join(choices)}.",
                        parent=self.root,
                    )
                    return
                block["target"] = selected
        elif kind in ("touching_color", "color_touching"):
            color_fields = ["color"]
            if kind == "color_touching":
                color_fields.append("other_color")
            for field in color_fields:
                selected = colorchooser.askcolor(
                    color=block[field],
                    title="Choose a sensing color",
                    parent=self.root,
                )[1]
                if selected is None:
                    return
                block[field] = selected
        elif kind in ("change_x", "change_y", "set_x", "set_y"):
            field = "amount" if kind.startswith("change") else "value"
            axis = "X" if kind.endswith("_x") else "Y"
            value = self.ask_number_or_input(
                "Edit block",
                f"{'Change' if field == 'amount' else 'Set'} {axis} by/to:",
                block[field],
            )
            if value is not None:
                block[field] = value
        elif kind == "rotation_style":
            value = simpledialog.askstring(
                "Edit rotation style",
                "Choose all around, left-right, or don't rotate:",
                initialvalue=block["style"],
                parent=self.root,
            )
            if value is not None:
                normalized = value.strip().lower()
                styles = {
                    "all around": "all around",
                    "left-right": "left-right",
                    "don't rotate": "don't rotate",
                    "dont rotate": "don't rotate",
                }
                if normalized not in styles:
                    messagebox.showerror(
                        "Invalid rotation style",
                        "Choose all around, left-right, or don't rotate.",
                        parent=self.root,
                    )
                    return
                block["style"] = styles[normalized]
        elif kind in ("play_sound", "start_sound"):
            sound_choices = ["Meow", "Pop", "Bell", "Drum"]
            sound_choices.extend(sound["name"] for sound in self.sounds)
            value = simpledialog.askstring(
                "Edit sound",
                f"Sound name ({', '.join(sound_choices)}):",
                initialvalue=block["sound"],
                parent=self.root,
            )
            if value is not None:
                selected_name = value.strip()
                matches = {
                    sound.casefold(): sound for sound in sound_choices
                }
                if selected_name.casefold() not in matches:
                    messagebox.showerror(
                        "Sound not found",
                        f"Choose one of: {', '.join(sound_choices)}.",
                        parent=self.root,
                    )
                    return
                block["sound"] = matches[selected_name.casefold()]
        elif kind in ("change_pitch", "set_pitch", "change_volume", "set_volume"):
            field = "amount" if kind.startswith("change") else "value"
            effect = "pitch" if "pitch" in kind else "volume"
            value = self.ask_number_or_input(
                f"Edit {effect}",
                f"{'Change' if field == 'amount' else 'Set'} {effect} "
                f"{'by' if field == 'amount' else 'to'} "
                f"{'(-100 to 100)' if effect == 'pitch' else '(0 to 100)'}:",
                block[field],
                minimum=-100 if effect == "pitch" or field == "amount" else 0,
                maximum=100,
                integer=True,
            )
            if value is not None:
                block[field] = value
        elif kind == "goto":
            x = self.ask_number_or_input(
                "Edit block", "X position (-200 to 200)", block["x"],
                minimum=-200, maximum=200, integer=True,
            )
            if x is None:
                return
            y = self.ask_number_or_input(
                "Edit block", "Y position (-200 to 200)", block["y"],
                minimum=-200, maximum=200, integer=True,
            )
            if y is None:
                return
            block.update(x=x, y=y)
        elif kind == "physics_gravity":
            gravity_x = self.ask_number_or_input(
                "Edit physics gravity", "Gravity X:", block["x"],
            )
            if gravity_x is None:
                return
            gravity_y = self.ask_number_or_input(
                "Edit physics gravity", "Gravity Y:", block["y"],
            )
            if gravity_y is None:
                return
            block.update(x=gravity_x, y=gravity_y)
        elif kind == "physics_body_type":
            part = simpledialog.askstring(
                "Edit physics part", "Part name:", initialvalue=block["part"],
                parent=self.root,
            )
            if part is None:
                return
            body_type = simpledialog.askstring(
                "Edit physics body type",
                "Choose static, kinematic, or dynamic:",
                initialvalue=block["body_type"], parent=self.root,
            )
            if body_type is None:
                return
            body_type = body_type.strip().lower()
            if body_type not in ("static", "kinematic", "dynamic"):
                messagebox.showerror(
                    "Invalid body type",
                    "Choose static, kinematic, or dynamic.",
                    parent=self.root,
                )
                return
            block.update(part=part.strip() or "Body", body_type=body_type)
        elif kind in ("physics_force", "physics_impulse", "physics_velocity"):
            part = simpledialog.askstring(
                "Edit physics part", "Part name:", initialvalue=block["part"],
                parent=self.root,
            )
            if part is None:
                return
            x = self.ask_number_or_input(
                "Edit physics vector", "X:", block["x"],
            )
            if x is None:
                return
            y = self.ask_number_or_input(
                "Edit physics vector", "Y:", block["y"],
            )
            if y is None:
                return
            block.update(part=part.strip() or "Body", x=x, y=y)
        elif kind == "physics_material":
            part = simpledialog.askstring(
                "Edit physics part", "Part name:", initialvalue=block["part"],
                parent=self.root,
            )
            if part is None:
                return
            friction = self.ask_number_or_input(
                "Edit physics material", "Friction (0 to 1):",
                block["friction"], minimum=0, maximum=1,
            )
            if friction is None:
                return
            restitution = self.ask_number_or_input(
                "Edit physics material", "Bounce / restitution (0 to 1):",
                block["restitution"], minimum=0, maximum=1,
            )
            if restitution is None:
                return
            block.update(
                part=part.strip() or "Body",
                friction=friction,
                restitution=restitution,
            )
        elif kind in ("say", "say_forever", "think", "think_forever"):
            text = simpledialog.askstring(
                "Edit speech block", "What should the sprite say or think?",
                initialvalue=block["text"], parent=self.root,
            )
            if text is None:
                return
            block["text"] = text
            if kind in ("say", "think"):
                seconds = self.ask_number_or_input(
                    "Edit speech block", "Show message for how many seconds",
                    block["seconds"], minimum=1, maximum=60, integer=True,
                )
                if seconds is None:
                    return
                block["seconds"] = seconds
        elif kind in ("switch_costume", "switch_backdrop"):
            choices = self.sprite_costumes if kind == "switch_costume" else self.backdrops
            if not choices:
                messagebox.showinfo(
                    "No images available",
                    "Add a costume or backdrop using the + buttons under the stage first.",
                    parent=self.root,
                )
                return
            names = ", ".join(asset["name"] for asset in choices)
            name = simpledialog.askstring(
                "Choose image", f"Image name ({names}):",
                initialvalue=block["name"], parent=self.root,
            )
            if name is not None:
                block["name"] = name.strip()
        elif kind in ("change_size", "set_size"):
            field = "amount" if kind == "change_size" else "value"
            value = self.ask_number_or_input(
                "Edit sprite size",
                "Change size by:" if field == "amount" else "Set size to (%):",
                block[field], minimum=-300 if field == "amount" else 10,
                maximum=300, integer=True,
            )
            if value is not None:
                block[field] = value
        elif kind in ("change_effect", "set_effect"):
            field = "amount" if kind == "change_effect" else "value"
            effect = simpledialog.askstring(
                "Graphic effect", "Effect name (color):",
                initialvalue=block["effect"], parent=self.root,
            )
            if effect is None:
                return
            if effect.strip().lower() != "color":
                messagebox.showerror(
                    "Unsupported effect", "The supported effect is color.",
                    parent=self.root,
                )
                return
            value = self.ask_number_or_input(
                "Graphic effect",
                "Change effect by:" if field == "amount" else "Set effect to (0 to 200):",
                block[field], minimum=-200 if field == "amount" else 0,
                maximum=200, integer=True,
            )
            if value is not None:
                block.update(effect="color", **{field: value})
        elif kind == "print":
            value = simpledialog.askstring(
                "Edit print block",
                "Text or value to print (use $name to read a variable):",
                initialvalue=block["value"],
                parent=self.root,
            )
            if value is not None:
                block["value"] = value
        elif kind == "input":
            name = simpledialog.askstring(
                "Edit input block",
                "Variable name for the user's answer:",
                initialvalue=block["name"],
                parent=self.root,
            )
            if name is None or not name.strip():
                return
            prompt = simpledialog.askstring(
                "Edit input block",
                "Question to ask:",
                initialvalue=block["prompt"],
                parent=self.root,
            )
            if prompt is None:
                return
            block.update(name=name.strip(), prompt=prompt)
        elif kind == "read_file":
            name = simpledialog.askstring(
                "Read text file",
                "Variable name for the file contents:",
                initialvalue=block["name"],
                parent=self.root,
            )
            if name is not None and name.strip():
                block["name"] = name.strip()
        elif kind == "write_file":
            value = simpledialog.askstring(
                "Write text file",
                "Text to write (use $name to insert a variable):",
                initialvalue=block["value"],
                parent=self.root,
            )
            if value is not None:
                block["value"] = value
        elif kind == "set_variable":
            name = simpledialog.askstring(
                "Set variable",
                "Variable name:",
                initialvalue=block["name"],
                parent=self.root,
            )
            if name is None or not name.strip():
                return
            data_type = simpledialog.askstring(
                "Set variable",
                "Data type (int, float, str, bool, list, dict, None):",
                initialvalue=block["data_type"],
                parent=self.root,
            )
            if data_type is None:
                return
            data_type = data_type.strip()
            if data_type not in ("int", "float", "str", "bool", "list", "dict", "None"):
                messagebox.showerror("Invalid data type", "Choose int, float, str, bool, list, dict, or None.", parent=self.root)
                return
            value = simpledialog.askstring(
                "Set variable",
                "Value (lists/dicts use Python literal syntax):",
                initialvalue=block["value"],
                parent=self.root,
            )
            if value is None:
                return
            block.update(name=name.strip(), data_type=data_type, value=value)
        elif kind == "calculate":
            name = simpledialog.askstring(
                "Math block", "Result variable name:", initialvalue=block["name"], parent=self.root,
            )
            if name is None or not name.strip():
                return
            left = simpledialog.askstring(
                "Math block",
                "Left value, variable ($name), or sensing reporter "
                "(@touching(mouse-pointer), @distance_to(mouse-pointer)):",
                initialvalue=block["left"], parent=self.root,
            )
            if left is None:
                return
            operator_name = simpledialog.askstring(
                "Math block", "Operator (+, -, *, /, //, %, **):",
                initialvalue=block["operator"], parent=self.root,
            )
            if operator_name is None:
                return
            right = simpledialog.askstring(
                "Math block",
                "Right value, variable ($name), or sensing reporter "
                "(@touching(mouse-pointer), @distance_to(mouse-pointer)):",
                initialvalue=block["right"], parent=self.root,
            )
            if right is None:
                return
            block.update(
                name=name.strip(), left=left, operator=operator_name.strip(), right=right,
            )
        elif kind in ("if", "while"):
            left = simpledialog.askstring(
                "Edit condition",
                "Left value, variable ($name), or sensing reporter "
                "(@touching(mouse-pointer), @touching_color(#rrggbb), "
                "@color_touching(#rrggbb;#rrggbb), @distance_to(mouse-pointer)):",
                initialvalue=block["left"], parent=self.root,
            )
            if left is None:
                return
            operator_name = simpledialog.askstring(
                "Edit condition",
                "Comparison (==, !=, <, <=, >, >=, contains, truthy, falsy):",
                initialvalue=block["operator"],
                parent=self.root,
            )
            if operator_name is None:
                return
            right = simpledialog.askstring(
                "Edit condition",
                "Right value or sensing reporter "
                "(@touching(mouse-pointer), @touching_color(#rrggbb), "
                "@color_touching(#rrggbb;#rrggbb), @distance_to(mouse-pointer)):",
                initialvalue=block["right"], parent=self.root,
            )
            if right is None:
                return
            block.update(left=left, operator=operator_name.strip(), right=right)
        elif kind == "repeat":
            value = self.ask_number_or_input(
                "Edit block", "Repeat how many times", block["count"],
                minimum=1, maximum=100, integer=True,
            )
            if value is not None:
                block["count"] = value
        elif kind == "wait":
            value = self.ask_number_or_input(
                "Edit block", "Wait how many seconds", block["seconds"],
                minimum=1, maximum=60, integer=True,
            )
            if value is not None:
                block["seconds"] = value
        elif kind == "memory_limit":
            value = simpledialog.askinteger(
                "Edit block",
                "Maximum app memory in MB (64 to 8192):",
                initialvalue=block["megabytes"],
                minvalue=64,
                maxvalue=8192,
                parent=self.root,
            )
            if value is not None:
                block["megabytes"] = value
        elif kind == "rendering":
            mode = simpledialog.askstring(
                "Game rendering",
                "Choose software (CPU), opengl (GPU), or opengl_es (OpenGL ES 2):",
                initialvalue=block["mode"],
                parent=self.root,
            )
            if mode is not None:
                normalized = mode.strip().lower()
                modes = {
                    "software": "software",
                    "software (cpu)": "software",
                    "opengl": "opengl",
                    "opengl (gpu)": "opengl",
                    "opengl_es": "opengl_es",
                    "opengl es 2": "opengl_es",
                }
                if normalized not in modes:
                    messagebox.showerror(
                        "Invalid renderer",
                        "Choose software (CPU), opengl (GPU), or opengl_es.",
                        parent=self.root,
                    )
                    return
                block["mode"] = modes[normalized]
        elif kind == "image":
            x = simpledialog.askinteger(
                "Edit image sprite", "Stage X position:", initialvalue=block["x"],
                minvalue=-500, maxvalue=500, parent=self.root,
            )
            if x is None:
                return
            y = simpledialog.askinteger(
                "Edit image sprite", "Stage Y position:", initialvalue=block["y"],
                minvalue=-500, maxvalue=500, parent=self.root,
            )
            if y is None:
                return
            block.update(x=x, y=y)
        self.draw_workspace()
        self.draw_stage()

    def execute_mod_block(self, block):
        """Run a Python block against a validated copy of the active sprite state."""
        sprite = {
            "x": self.sprite_x - 200,
            "y": 205 - self.sprite_y,
            "direction": self.sprite_angle,
            "size": self.sprite_size,
            "visible": self.sprite_visible,
            "velocity_x": getattr(self, "sprite_velocity_x", 0.0),
            "velocity_y": getattr(self, "sprite_velocity_y", 0.0),
            "gravity": getattr(self, "sprite_gravity", 0.0),
        }
        MOD_REGISTRY.execute(block, ModContext(sprite, self.variables))
        self.sprite_x = 200 + sprite["x"]
        self.sprite_y = 205 - sprite["y"]
        self.sprite_angle = sprite["direction"]
        self.sprite_size = sprite["size"]
        self.sprite_visible = sprite["visible"]
        self.sprite_velocity_x = sprite["velocity_x"]
        self.sprite_velocity_y = sprite["velocity_y"]
        self.sprite_gravity = sprite["gravity"]

    def draw_stage(self):
        """Render the backdrop, imported images, sprite, and speech bubble."""
        stage = self.stage
        stage.delete("all")
        width = max(stage.winfo_width(), 360)
        height = max(stage.winfo_height(), 410)
        stage.create_rectangle(0, 0, width, height, fill="white", outline="")
        self._updating_sprite_controls = True
        self.sprite_name_var.set(self.sprite_name)
        self.sprite_x_var.set(str(round(self.sprite_x - 200)))
        self.sprite_y_var.set(str(round(205 - self.sprite_y)))
        self.sprite_size_var.set(str(self.sprite_size))
        self.sprite_direction_var.set(str(self.sprite_angle))
        self._updating_sprite_controls = False
        self.sync_active_sprite()
        self.stage_image_refs = []
        if self.backdrops:
            backdrop = self.backdrops[self.backdrop_index]
            try:
                image_data = backdrop["image_data"]

                def create_backdrop():
                    image = decode_image_data(image_data).resize(
                        (width, height), Image.Resampling.LANCZOS,
                    )
                    return ImageTk.PhotoImage(image)

                photo = self.image_cache.get(
                    ("backdrop", image_data, width, height), create_backdrop,
                )
            except (KeyError, ValueError, OSError):
                photo = None
            if photo is not None:
                self.stage_image_refs.append(photo)
                stage.create_image(width / 2, height / 2, image=photo)
        stage.create_text(
            12, 12, text=f"x: {round(self.sprite_x - 200)}   y: {round(205 - self.sprite_y)}",
            anchor="nw", fill="#536273", font=("Segoe UI", 9),
        )
        cx = width / 2 + self.sprite_x - 200
        cy = height / 2 + self.sprite_y - 205
        self.draw_other_sprites(front=False)
        if not self.sprite_layer_front:
            self.draw_sprite(stage, cx, cy)
        for block in self.ordered_blocks():
            if block["kind"] != "image" or not block.get("image_data"):
                continue
            try:
                image_data = block["image_data"]

                def create_block_image():
                    image = decode_image_data(image_data)
                    image.thumbnail((300, 300))
                    return ImageTk.PhotoImage(image)

                photo = self.image_cache.get(
                    ("block", image_data), create_block_image,
                )
            except (ValueError, OSError):
                continue
            self.stage_image_refs.append(photo)
            stage.create_image(
                width / 2 + block.get("x", 0),
                height / 2 - block.get("y", 0),
                image=photo,
            )
        self.draw_other_sprites(front=True)
        if self.sprite_layer_front:
            self.draw_sprite(stage, cx, cy)

    def draw_other_sprites(self, front):
        """Render non-selected sprites while preserving the selected inspector state."""
        current_state = {
            "name": self.sprite_name,
            "x": self.sprite_x,
            "y": self.sprite_y,
            "size": self.sprite_size,
            "direction": self.sprite_angle,
            "visible": self.sprite_visible,
            "costumes": self.sprite_costumes,
            "costume_index": self.sprite_costume_index,
            "effect": self.sprite_effect,
            "layer_front": self.sprite_layer_front,
        }
        current_speech = self.speech
        self.speech = ""
        for index, sprite in enumerate(self.sprites):
            if index == self.active_sprite_index or sprite.get("layer_front", True) != front:
                continue
            self.sprite_name = sprite.get("name", "Sprite")
            self.sprite_x = 200 + sprite.get("x", 0)
            self.sprite_y = 205 - sprite.get("y", 0)
            self.sprite_size = sprite.get("size", 100)
            self.sprite_angle = sprite.get("direction", 90)
            self.sprite_visible = sprite.get("visible", True)
            self.sprite_costumes = sprite.get("costumes", [])
            self.sprite_costume_index = sprite.get("costume_index", 0)
            self.sprite_effect = sprite.get("effect", 0)
            self.sprite_layer_front = sprite.get("layer_front", True)
            center_x = self.stage.winfo_width() / 2 + self.sprite_x - 200
            center_y = self.stage.winfo_height() / 2 + self.sprite_y - 205
            self.draw_sprite(self.stage, center_x, center_y)
        self.sprite_name = current_state["name"]
        self.sprite_x = current_state["x"]
        self.sprite_y = current_state["y"]
        self.sprite_size = current_state["size"]
        self.sprite_angle = current_state["direction"]
        self.sprite_visible = current_state["visible"]
        self.sprite_costumes = current_state["costumes"]
        self.sprite_costume_index = current_state["costume_index"]
        self.sprite_effect = current_state["effect"]
        self.sprite_layer_front = current_state["layer_front"]
        self.speech = current_speech

    def begin_sprite_drag(self, event):
        """Select the sprite under the pointer and start moving it on the stage."""
        if self.running:
            self.sprite_drag_start = None
            return
        width = max(self.stage.winfo_width(), 360)
        height = max(self.stage.winfo_height(), 410)
        for index in reversed(range(len(self.sprites))):
            sprite = self.sprites[index]
            if not sprite.get("visible", True):
                continue
            center_x = width / 2 + sprite.get("x", 0)
            center_y = height / 2 - sprite.get("y", 0)
            radius = max(24, min(100, 40 * sprite.get("size", 100) / 100))
            if (event.x - center_x) ** 2 + (event.y - center_y) ** 2 <= radius ** 2:
                if index != self.active_sprite_index:
                    self.select_sprite(index)
                self.sprite_drag_start = (
                    event.x, event.y, self.sprite_x, self.sprite_y,
                )
                return
        self.sprite_drag_start = None

    def drag_sprite(self, event):
        """Update the selected sprite's stage coordinates while dragging."""
        if self.sprite_drag_start is None:
            return
        start_x, start_y, sprite_x, sprite_y = self.sprite_drag_start
        self.sprite_x = sprite_x + event.x - start_x
        self.sprite_y = sprite_y + event.y - start_y
        self.sync_active_sprite()
        self.draw_stage()

    def finish_sprite_drag(self, _event):
        """Finish a stage drag and leave the sprite selected."""
        self.sprite_drag_start = None

    def draw_sprite(self, stage, center_x, center_y):
        """Draw the active costume or default sprite at its current properties."""
        if not self.sprite_visible:
            return
        if self.sprite_costumes:
            costume = self.sprite_costumes[self.sprite_costume_index]
            try:
                image_data = costume["image_data"]
                max_dimension = max(1, round(400 * self.sprite_size / 100))
                if self.rotation_style == "left-right":
                    rendered_angle = 90 if 90 < self.sprite_angle < 270 else -90
                elif self.rotation_style == "don't rotate":
                    rendered_angle = 0
                else:
                    rendered_angle = self.sprite_angle

                def create_costume_image():
                    image = apply_color_effect(
                        decode_image_data(image_data), self.sprite_effect,
                    )
                    image.thumbnail(
                        (max_dimension, max_dimension), Image.Resampling.LANCZOS,
                    )
                    image = image.rotate(
                        -rendered_angle, expand=True,
                        resample=Image.Resampling.BICUBIC,
                    )
                    return ImageTk.PhotoImage(image)

                photo = self.image_cache.get(
                    ("costume", image_data, self.sprite_effect, max_dimension,
                     rendered_angle),
                    create_costume_image,
                )
            except (KeyError, ValueError, OSError):
                photo = None
            if photo is not None:
                self.stage_image_refs.append(photo)
                stage.create_image(center_x, center_y, image=photo)
                if self.speech:
                    bubble_x = min(max(center_x, 65), max(65, stage.winfo_width() - 65))
                    stage.create_oval(
                        bubble_x - 62, center_y - 73, bubble_x + 62, center_y - 39,
                        fill="white", outline="#777",
                    )
                    stage.create_text(
                        bubble_x, center_y - 56, text=self.speech[:22],
                        fill="#333", font=("Segoe UI", 9),
                    )
                    if self.speech_kind == "think":
                        stage.create_oval(
                            bubble_x - 48, center_y - 36, bubble_x - 42,
                            center_y - 30, fill="white", outline="#777",
                        )
                        stage.create_oval(
                            bubble_x - 38, center_y - 28, bubble_x - 34,
                            center_y - 24, fill="white", outline="#777",
                        )
                return
        if self.rotation_style == "left-right":
            rendered_angle = 90 if 90 < self.sprite_angle < 270 else -90
        elif self.rotation_style == "don't rotate":
            rendered_angle = 0
        else:
            rendered_angle = self.sprite_angle
        angle = math.radians(rendered_angle)
        scale = self.sprite_size / 100
        tip = (
            center_x + 27 * scale * math.sin(angle),
            center_y - 27 * scale * math.cos(angle),
        )
        left = (
            center_x + 15 * scale * math.sin(angle + 2.5),
            center_y - 15 * scale * math.cos(angle + 2.5),
        )
        right = (
            center_x + 15 * scale * math.sin(angle - 2.5),
            center_y - 15 * scale * math.cos(angle - 2.5),
        )
        stage.create_polygon(
            tip[0], tip[1], left[0], left[1], center_x, center_y,
            right[0], right[1], fill="#ff9f43", outline="#b35a16", width=2,
        )
        stage.create_oval(
            center_x - 17 * scale, center_y - 17 * scale,
            center_x + 17 * scale, center_y + 17 * scale,
            fill="#ffcc4d", outline="#b35a16", width=2,
        )
        stage.create_oval(
            center_x - 6 * scale, center_y - 7 * scale,
            center_x - 2 * scale, center_y - 3 * scale, fill="#333", outline="",
        )
        stage.create_oval(
            center_x + 2 * scale, center_y - 7 * scale,
            center_x + 6 * scale, center_y - 3 * scale, fill="#333", outline="",
        )
        if self.speech:
            bubble_x = min(max(center_x, 65), max(65, stage.winfo_width() - 65))
            stage.create_oval(
                bubble_x - 62, center_y - 73, bubble_x + 62, center_y - 39,
                fill="white", outline="#777",
            )
            stage.create_text(
                bubble_x, center_y - 56, text=self.speech[:22],
                fill="#333", font=("Segoe UI", 9),
            )

    def on_stage_resize(self, event):
        """Redraw the stage after the user resizes the editor window."""
        if event.width > 1 and event.height > 1:
            self.draw_stage()

    def reset_stage(self):
        """Return the sprite and its visual effects to their starting values."""
        self.sprite_x = 200
        self.sprite_y = 205
        self.sprite_angle = 90
        self.sprite_size = 100
        self.sprite_effect = 0
        self.sprite_visible = True
        self.sprite_visible_var.set(True)
        self.sprite_size_var.set("100")
        self.sprite_direction_var.set("90")
        self.speech = ""
        self.speech_kind = "say"
        self.draw_stage()
        self.status.set("Stage reset.")

    def run_script(self, event_key=None, _from_sprite_queue=False):
        """Run matching scripts for every sprite, keeping Tkinter responsive."""
        # Tkinter's after() callbacks keep the window responsive during scripts.
        if self.running or self.editing_custom_block_id is not None:
            return
        if not _from_sprite_queue and len(self.sprites) > 1:
            self.save_active_sprite()
            matching_sprites = []
            has_keyboard_scripts = False
            try:
                for index, sprite in enumerate(self.sprites):
                    scripts = split_event_scripts(
                        expand_custom_blocks(sprite.get("blocks", []), self.custom_blocks)
                    )
                    has_keyboard_scripts = has_keyboard_scripts or any(
                        script["event"] == "key" for script in scripts
                    )
                    selected_script = next(
                        (
                            script for script in scripts
                            if (
                                script["event"] == "flag" and event_key is None
                            ) or (
                                script["event"] == "key"
                                and event_key is not None
                                and script["key"] == event_key.lower()
                            )
                        ),
                        None,
                    )
                    if selected_script and expand_script(selected_script["blocks"]):
                        matching_sprites.append(index)
            except ValueError as error:
                messagebox.showerror("Script error", str(error), parent=self.root)
                return
            if not matching_sprites:
                if event_key is None and has_keyboard_scripts:
                    self.status.set(
                        "Ready. Press a key used by a 'when key pressed' block."
                    )
                    return
                messagebox.showinfo(
                    "No actions", "Add matching event blocks to a sprite script first.",
                    parent=self.root,
                )
                return
            self._sprite_script_queue = matching_sprites
            self._sprite_queue_event_key = event_key
            self._running_sprite_queue = True
            self.run_button.config(state=tk.DISABLED)
            self._run_next_sprite_script()
            return
        try:
            scripts = split_event_scripts(
                expand_custom_blocks(self.ordered_blocks(), self.custom_blocks)
            )
            if event_key is None:
                selected = next(
                    (script for script in scripts if script["event"] == "flag"),
                    None,
                )
            else:
                selected = next(
                    (
                        script for script in scripts
                        if script["event"] == "key"
                        and script["key"] == event_key.lower()
                    ),
                    None,
                )
            if selected is None:
                if self._running_sprite_queue:
                    self._run_next_sprite_script()
                elif event_key is None and any(
                    script["event"] == "key" for script in scripts
                ):
                    self.status.set(
                        "Ready. Press a key used by a 'when key pressed' block."
                    )
                return
            actions = expand_script(selected["blocks"])
        except ValueError as error:
            messagebox.showerror("Script error", str(error), parent=self.root)
            self._stop_sprite_script_queue()
            return
        if not actions:
            if self._running_sprite_queue:
                self._run_next_sprite_script()
            else:
                messagebox.showinfo("No actions", "Add some blocks to your script first.", parent=self.root)
            return
        self.running = True
        self.run_button.config(state=tk.DISABLED)
        self.status.set(f"Running {len(actions)} action(s)...")
        if event_key is None:
            self.variables = {}
            self.sound_pitch = 0
            self.sound_volume = 100
        try:
            control_jumps = validate_control_flow(actions)
        except ValueError as error:
            self.running = False
            self.run_button.config(state=tk.NORMAL)
            messagebox.showerror("Script error", str(error), parent=self.root)
            self._stop_sprite_script_queue()
            return
        executed_steps = 0

        def stop_script(message, failed=False, redraw=True):
            """Finish execution, unlock Run, and optionally report an error."""
            self.running = False
            if redraw:
                self.draw_stage()
            if failed and self._running_sprite_queue:
                self._stop_sprite_script_queue()
            elif self._running_sprite_queue:
                self._run_next_sprite_script()
                return
            self.run_button.config(state=tk.NORMAL)
            self.status.set(message)
            if failed:
                messagebox.showerror("Script stopped", message, parent=self.root)

        def check_memory_limit():
            """Read process memory and stop the script if its limit is exceeded."""
            try:
                memory_mb = get_process_memory_mb()
            except OSError as error:
                stop_script(f"Could not read app memory usage: {error}", failed=True)
                return False
            if memory_mb > self.memory_limit_mb:
                self.image_cache.clear()
                try:
                    memory_mb = get_process_memory_mb()
                except OSError as error:
                    stop_script(
                        f"Could not read app memory usage after cache cleanup: {error}",
                        failed=True,
                        redraw=False,
                    )
                    return False
            self.memory_status.set(
                f"App memory: {memory_mb:.1f} MB / {self.memory_limit_mb} MB"
            )
            if memory_mb > self.memory_limit_mb:
                stop_script(
                    f"Script stopped: app memory usage ({memory_mb:.1f} MB) "
                    f"exceeded the {self.memory_limit_mb} MB limit.",
                    failed=True,
                    redraw=False,
                )
                return False
            return True

        def wait_before_next_action(next_index, remaining_ms, clear_speech=False):
            """Wait without freezing Tkinter, checking memory between intervals."""
            interval = min(250, remaining_ms)

            def resume():
                """Continue the delay or run the next action after the wait."""
                if not self.running or not check_memory_limit():
                    return
                remaining = remaining_ms - interval
                if remaining <= 0:
                    if clear_speech:
                        self.speech = ""
                        self.draw_stage()
                    run_action(next_index)
                else:
                    wait_before_next_action(next_index, remaining, clear_speech)

            self.root.after(interval, resume)

        def wait_for_imported_audio(next_index):
            """Keep the Play Sound block active until its imported audio finishes."""
            if not self.running or not check_memory_limit():
                return
            if imported_audio_is_playing():
                self.root.after(30, wait_for_imported_audio, next_index)
            else:
                run_action(next_index)

        def animate_glide(index, start_x, start_y, target_x, target_y, duration_ms):
            """Move the sprite through timed intermediate positions."""
            steps = max(1, min(60, round(duration_ms / 40)))

            def move_step(step):
                """Draw one glide frame, then schedule the next frame."""
                if not self.running or not check_memory_limit():
                    return
                fraction = step / steps
                self.sprite_x = start_x + (target_x - start_x) * fraction
                self.sprite_y = start_y + (target_y - start_y) * fraction
                self.draw_stage()
                if step >= steps:
                    run_action(index + 1)
                else:
                    # Schedule the next frame instead of pausing the Tkinter loop.
                    self.root.after(
                        max(1, round(duration_ms / steps)),
                        move_step,
                        step + 1,
                    )

            move_step(1)

        def run_action(index):
            """Execute one block action and schedule whichever action follows."""
            nonlocal executed_steps
            if index >= len(actions):
                stop_script("Script finished.")
                return
            executed_steps += 1
            if executed_steps > 5000:
                stop_script(
                    "Script stopped after 5,000 steps. Check for a loop that never ends.",
                    failed=True,
                )
                return

            action = actions[index]
            delay = 0 if action.get("_run_without_refresh") else 160
            kind = action["kind"]
            wait_for_audio = False
            next_index = index + 1
            if kind != "memory_limit":
                if not check_memory_limit():
                    return

            try:
                # Each branch translates one block kind into changes to game state.
                if MOD_REGISTRY.get(kind) is not None:
                    self.execute_mod_block(action)
                elif kind == "move":
                    angle = math.radians(self.sprite_angle)
                    delta_x = action["steps"] * math.sin(angle)
                    delta_y = -action["steps"] * math.cos(angle)
                    self.sprite_x += delta_x
                    self.sprite_y += delta_y
                elif kind == "turn":
                    self.sprite_angle = (self.sprite_angle + action["degrees"]) % 360
                elif kind == "turn_counterclockwise":
                    self.sprite_angle = (self.sprite_angle - action["degrees"]) % 360
                elif kind in ("goto_random", "glide_random"):
                    target_x = 200 + random.randint(-180, 180)
                    target_y = 205 - random.randint(-160, 160)
                    if kind == "glide_random":
                        animate_glide(
                            index, self.sprite_x, self.sprite_y, target_x, target_y,
                            max(100, int(action["seconds"] * 1000)),
                        )
                        return
                    self.sprite_x, self.sprite_y = target_x, target_y
                elif kind == "goto":
                    self.sprite_x = 200 + action["x"]
                    self.sprite_y = 205 - action["y"]
                elif kind == "glide":
                    animate_glide(
                        index, self.sprite_x, self.sprite_y,
                        200 + action["x"], 205 - action["y"],
                        max(100, int(action["seconds"] * 1000)),
                    )
                    return
                elif kind == "point_direction":
                    self.sprite_angle = action["degrees"] % 360
                elif kind == "point_mouse":
                    pointer_x, pointer_y = self.root.winfo_pointerxy()
                    mouse_x = pointer_x - self.stage.winfo_rootx() - self.stage.winfo_width() / 2 + 200
                    mouse_y = self.stage.winfo_rooty() + self.stage.winfo_height() / 2 - pointer_y + 205
                    self.sprite_angle = math.degrees(
                        math.atan2(mouse_x - self.sprite_x, self.sprite_y - mouse_y),
                    ) % 360
                elif kind == "change_x":
                    self.sprite_x += action["amount"]
                elif kind == "set_x":
                    self.sprite_x = 200 + action["value"]
                elif kind == "change_y":
                    self.sprite_y -= action["amount"]
                elif kind == "set_y":
                    self.sprite_y = 205 - action["value"]
                elif kind == "bounce":
                    width = max(self.stage.winfo_width(), 360)
                    height = max(self.stage.winfo_height(), 410)
                    min_x, max_x = 200 - width / 2 + 20, 200 + width / 2 - 20
                    min_y, max_y = 205 - height / 2 + 20, 205 + height / 2 - 20
                    if self.sprite_x < min_x or self.sprite_x > max_x:
                        self.sprite_x = min(max(self.sprite_x, min_x), max_x)
                        self.sprite_angle = (-self.sprite_angle) % 360
                    if self.sprite_y < min_y or self.sprite_y > max_y:
                        self.sprite_y = min(max(self.sprite_y, min_y), max_y)
                        self.sprite_angle = (180 - self.sprite_angle) % 360
                elif kind == "rotation_style":
                    self.rotation_style = action["style"]
                elif kind == "say":
                    self.speech = action["text"]
                    self.speech_kind = "say"
                    delay = action["seconds"] * 1000
                elif kind == "think":
                    self.speech = action["text"]
                    self.speech_kind = "think"
                    delay = action["seconds"] * 1000
                elif kind in ("say_forever", "think_forever"):
                    self.speech = action["text"]
                    self.speech_kind = "think" if kind == "think_forever" else "say"
                elif kind in ("switch_costume", "next_costume"):
                    if not self.sprite_costumes:
                        raise ValueError("Add a sprite costume before using a costume block.")
                    if kind == "next_costume":
                        self.sprite_costume_index = (
                            self.sprite_costume_index + 1
                        ) % len(self.sprite_costumes)
                    else:
                        match = next(
                            (
                                index for index, costume
                                in enumerate(self.sprite_costumes)
                                if costume["name"].casefold()
                                == action["name"].casefold()
                            ),
                            None,
                        )
                        if match is None:
                            raise ValueError(
                                f"Costume '{action['name']}' was not found."
                            )
                        self.sprite_costume_index = match
                    self.update_asset_status()
                elif kind in ("switch_backdrop", "next_backdrop"):
                    if not self.backdrops:
                        raise ValueError("Add a stage backdrop before using a backdrop block.")
                    if kind == "next_backdrop":
                        self.backdrop_index = (
                            self.backdrop_index + 1
                        ) % len(self.backdrops)
                    else:
                        match = next(
                            (
                                index for index, backdrop
                                in enumerate(self.backdrops)
                                if backdrop["name"].casefold()
                                == action["name"].casefold()
                            ),
                            None,
                        )
                        if match is None:
                            raise ValueError(
                                f"Backdrop '{action['name']}' was not found."
                            )
                        self.backdrop_index = match
                    self.update_asset_status()
                elif kind == "change_size":
                    self.sprite_size = max(
                        10, min(300, self.sprite_size + action["amount"]),
                    )
                    self.sprite_size_var.set(str(self.sprite_size))
                elif kind == "set_size":
                    self.sprite_size = max(10, min(300, action["value"]))
                    self.sprite_size_var.set(str(self.sprite_size))
                elif kind == "change_effect":
                    self.sprite_effect = max(
                        0, min(200, self.sprite_effect + action["amount"]),
                    )
                elif kind == "set_effect":
                    self.sprite_effect = max(0, min(200, action["value"]))
                elif kind == "clear_effects":
                    self.sprite_effect = 0
                elif kind in ("show", "hide"):
                    self.sprite_visible = kind == "show"
                    self.sprite_visible_var.set(self.sprite_visible)
                elif kind == "layer_front":
                    self.sprite_layer_front = True
                elif kind == "layer_back":
                    self.sprite_layer_front = False
                elif kind == "print":
                    self.speech = str(parse_value(action["value"], self.variables))
                    self.speech_kind = "say"
                    self.status.set(f"Printed: {self.speech[:80]}")
                    delay = 2000
                elif kind == "input":
                    answer = simpledialog.askstring(
                        "Program input", action["prompt"], parent=self.root,
                    )
                    self.variables[action["name"]] = answer or ""
                elif kind in ("read_file", "write_file"):
                    if not self.app_settings["read_write_blocks"]:
                        raise ValueError("File read/write blocks are disabled in Settings.")
                    file_types = [
                        ("Supported save files", "*.txt *.dat *.sav"),
                        ("Text files", "*.txt"),
                        ("Data files", "*.dat"),
                        ("Save files", "*.sav"),
                    ]
                    if kind == "read_file":
                        selected_file = filedialog.askopenfilename(
                            parent=self.root,
                            title="Choose a text or save file to read",
                            filetypes=file_types,
                        )
                        if selected_file:
                            selected_path = Path(selected_file)
                            if selected_path.suffix.lower() not in {".txt", ".dat", ".sav"}:
                                raise ValueError("Choose a .txt, .dat, or .sav file.")
                            content = selected_path.read_bytes()
                            if len(content) > 1_000_000:
                                raise ValueError("File is larger than the 1 MB block limit.")
                            self.variables[action["name"]] = content.decode("utf-8")
                    else:
                        selected_file = filedialog.asksaveasfilename(
                            parent=self.root,
                            title="Choose where to write a text or save file",
                            defaultextension=".txt",
                            filetypes=file_types,
                        )
                        if selected_file:
                            selected_path = Path(selected_file)
                            if selected_path.suffix.lower() not in {".txt", ".dat", ".sav"}:
                                raise ValueError("Choose a .txt, .dat, or .sav file.")
                            content = str(parse_value(action["value"], self.variables))
                            encoded_content = content.encode("utf-8")
                            if len(encoded_content) > 1_000_000:
                                raise ValueError("Text is larger than the 1 MB block limit.")
                            selected_path.write_bytes(encoded_content)
                elif kind == "set_variable":
                    self.variables[action["name"]] = typed_value(
                        action["data_type"], action["value"], self.variables,
                    )
                elif kind == "calculate":
                    perform_calculation(
                        action, self.variables, self.sensing_reporter,
                    )
                elif kind in ("play_sound", "start_sound"):
                    sound_duration = play_project_sound(
                        action["sound"], self.sounds,
                        self.sound_pitch, self.sound_volume,
                    )
                    if kind == "play_sound":
                        if sound_duration is None:
                            wait_for_audio = True
                        else:
                            delay = sound_duration
                elif kind == "stop_sounds":
                    stop_sounds()
                elif kind == "change_pitch":
                    self.sound_pitch = max(
                        -100, min(100, self.sound_pitch + action["amount"]),
                    )
                elif kind == "set_pitch":
                    self.sound_pitch = max(-100, min(100, action["value"]))
                elif kind == "clear_sound_effects":
                    self.sound_pitch = 0
                    self.sound_volume = 100
                elif kind == "change_volume":
                    self.sound_volume = max(
                        0, min(100, self.sound_volume + action["amount"]),
                    )
                elif kind == "set_volume":
                    self.sound_volume = max(0, min(100, action["value"]))
                elif kind == "volume":
                    self.speech = f"Volume: {self.sound_volume}%"
                    self.speech_kind = "say"
                    delay = 1500
                elif kind == "if":
                    if not evaluate_condition(
                        action, self.variables, self.sensing_reporter,
                    ):
                        alternate = control_jumps.get((index, "else"))
                        next_index = (
                            alternate + 1 if alternate is not None
                            else control_jumps[index] + 1
                        )
                elif kind == "if_touched":
                    raise ValueError(
                        "Touch blocks require OpenGL or OpenGL ES game rendering."
                    )
                elif kind == "else":
                    next_index = control_jumps[index] + 1
                elif kind == "while":
                    if not evaluate_condition(
                        action, self.variables, self.sensing_reporter,
                    ):
                        next_index = control_jumps[index] + 1
                elif kind == "end_while":
                    next_index = control_jumps[index]
                elif kind == "memory_limit":
                    self.memory_limit_mb = action["megabytes"]
                    self.image_cache.set_budget_for_memory_limit(
                        self.memory_limit_mb,
                    )
                    self.system_result.set(f"App memory limit: {self.memory_limit_mb} MB")
                    if not check_memory_limit():
                        return
                elif kind == "memory_result":
                    memory_mb = get_process_memory_mb()
                    self.system_result.set(
                        f"App memory result: {memory_mb:.1f} MB "
                        f"(limit: {self.memory_limit_mb} MB)"
                    )
                elif kind == "rendering":
                    renderer = {
                        "opengl": "OpenGL (GPU)",
                        "opengl_es": "OpenGL ES 2",
                        "software": "Software (CPU)",
                    }[action["mode"]]
                    self.system_result.set(
                        f"Generated games will use {renderer}; editor preview is unchanged."
                    )
            except (ArithmeticError, KeyError, OSError, RuntimeError, TypeError, ValueError) as error:
                stop_script(f"Block error: {error}", failed=True)
                return

            if not action.get("_run_without_refresh"):
                self.draw_stage()
            if kind in (
                "if", "if_touched", "else", "end_if", "while", "end_while",
            ):
                self.root.after(1, run_action, next_index)
            elif wait_for_audio:
                self.root.after(30, wait_for_imported_audio, next_index)
            else:
                wait_before_next_action(
                    next_index, delay,
                    clear_speech=kind in (
                        "say", "think", "print", "volume",
                    ),
                )

        run_action(0)

    def start_build(self):
        """Ask for game settings, validate the script, and start EXE creation."""
        if not self.can_export_game():
            messagebox.showinfo(
                "Administrator access required",
                "Generating game EXE files is available only when Steve Engine "
                "Editor is running with administrator access.",
                parent=self.root,
            )
            return
        if self.editing_custom_block_id is not None:
            messagebox.showinfo(
                "Finish editing the block",
                "Finish editing the custom block script before building the game.",
                parent=self.root,
            )
            return
        if self.running:
            messagebox.showinfo(
                "Game is running",
                "Wait for the script to finish before generating the game.",
                parent=self.root,
            )
            return

        dialog = tk.Toplevel(self.root)
        dialog.title("Game build settings")
        dialog.transient(self.root)
        dialog.resizable(False, False)
        dialog.grab_set()
        panel = tk.Frame(dialog, padx=18, pady=16)
        panel.pack(fill="both", expand=True)
        tk.Label(
            panel,
            text="Set up your game before creating the EXE",
            font=("Segoe UI", 12, "bold"),
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 12))

        file_name = tk.StringVar(value=self.game_settings["file_name"])
        window_title = tk.StringVar(value=self.game_settings["window_title"])
        width = tk.StringVar(value=str(self.game_settings["width"]))
        height = tk.StringVar(value=str(self.game_settings["height"]))
        fullscreen = tk.BooleanVar(value=self.game_settings["fullscreen"])
        for row, label, variable in (
            (1, "EXE file name", file_name),
            (2, "Window title (leave blank for no title text)", window_title),
            (3, "Window width", width),
            (4, "Window height", height),
        ):
            tk.Label(panel, text=label).grid(row=row, column=0, sticky="w", pady=4)
            tk.Entry(panel, textvariable=variable, width=34).grid(
                row=row, column=1, sticky="ew", pady=4,
            )
        tk.Checkbutton(
            panel, text="Start the game in fullscreen", variable=fullscreen,
        ).grid(row=5, column=0, columnspan=2, sticky="w", pady=(6, 12))
        buttons = tk.Frame(panel)
        buttons.grid(row=6, column=0, columnspan=2, sticky="e")
        tk.Button(buttons, text="Cancel", command=dialog.destroy).pack(side="right")

        def build_from_settings():
            """Validate the build dialog and launch the EXE build in a worker."""
            name = file_name.get().strip()
            if not name or any(character in name for character in '<>:"/\\|?*'):
                messagebox.showerror(
                    "Invalid EXE name",
                    "Use a file name without path separators or Windows filename symbols.",
                    parent=dialog,
                )
                return
            try:
                game_width = int(width.get())
                game_height = int(height.get())
            except ValueError:
                messagebox.showerror(
                    "Invalid window size",
                    "Width and height must be whole numbers.",
                    parent=dialog,
                )
                return
            if not 360 <= game_width <= 3840 or not 360 <= game_height <= 2160:
                messagebox.showerror(
                    "Invalid window size",
                    "Choose a width from 360 to 3840 and a height from 360 to 2160.",
                    parent=dialog,
                )
                return
            try:
                self.save_active_sprite()
                game_blocks = copy.deepcopy(self.ordered_blocks())
                actions = [
                    action
                    for sprite in self.sprites
                    for script in split_event_scripts(
                        expand_custom_blocks(sprite.get("blocks", []), self.custom_blocks)
                    )
                    for action in expand_script(script["blocks"])
                ]
            except (KeyError, TypeError, ValueError) as error:
                messagebox.showerror("Cannot build game", str(error), parent=dialog)
                return
            if not actions:
                messagebox.showinfo(
                    "No game actions",
                    "Add game actions to the script before generating an EXE.",
                    parent=dialog,
                )
                return
            self.game_settings = {
                "file_name": name,
                "window_title": window_title.get().strip(),
                "width": game_width,
                "height": game_height,
                "fullscreen": fullscreen.get(),
                "rendering": next(
                    (
                        action["mode"] for action in reversed(actions)
                        if action.get("kind") == "rendering"
                    ),
                    "software",
                ),
                "sprite": self.sprite_project_data(),
                "backdrops": copy.deepcopy(self.backdrops),
                "sprites": copy.deepcopy(self.sprites),
                "active_sprite_index": self.active_sprite_index,
                "custom_blocks": copy.deepcopy(self.custom_blocks),
                "mod_sources": dict(MOD_REGISTRY.sources),
                "sounds": copy.deepcopy(self.sounds),
            }
            dialog.destroy()
            self.building_game = True
            self.refresh_export_menu()
            self.status.set("Building standalone game EXE...")
            threading.Thread(
                target=self.build_exe,
                args=(game_blocks, copy.deepcopy(self.game_settings)),
                daemon=True,
            ).start()

        tk.Button(
            buttons, text="Build game EXE", command=build_from_settings,
            bg="#5268e8", fg="white", relief="flat", padx=12,
        ).pack(side="right", padx=(0, 8))
        dialog.bind("<Return>", lambda _event: build_from_settings())
        dialog.bind("<Escape>", lambda _event: dialog.destroy())
        dialog.update_idletasks()
        dialog.geometry(
            f"+{self.root.winfo_rootx() + 100}+{self.root.winfo_rooty() + 100}"
        )

    def build_exe(self, game_blocks, game_settings=None):
        """Create a game EXE from the installed standalone player runtime."""
        project_dir = Path(__file__).resolve().parent.parent
        game_settings = game_settings or self.game_settings.copy()
        game_settings["read_write_blocks"] = self.app_settings["read_write_blocks"]
        exe_path = project_dir / "dist" / f"{game_settings['file_name']}.exe"
        try:
            if getattr(sys, "frozen", False):
                editor_directory = Path(sys.executable).resolve().parent
                player_executable = (
                    editor_directory / "player" / "steve-game-player.exe"
                )
                if not player_executable.is_file():
                    raise FileNotFoundError(
                        "Steve Game Player is missing. Reinstall the editor "
                        "with the game player component."
                    )
                runtime_directory = player_executable.parent / "_internal"
                if not runtime_directory.is_dir():
                    raise FileNotFoundError(
                        "Steve Game Player runtime files are missing. Reinstall "
                        "the editor with the game player component."
                    )
                exe_path = (
                    editor_directory / "games" / game_settings["file_name"]
                    / f"{game_settings['file_name']}.exe"
                )
                append_game_payload(
                    player_executable,
                    exe_path,
                    game_blocks,
                    game_settings,
                    runtime_directory=runtime_directory,
                )
                self.root.after(
                    0,
                    self.finish_build,
                    f"Standalone game created:\n{exe_path}",
                    True,
                )
                return

            with tempfile.TemporaryDirectory(
                prefix="engine-editor-game-build-",
                dir=project_dir,
            ) as temporary_dir:
                build_dir = Path(temporary_dir)
                output_dir = build_dir / "output"
                command = [
                    sys.executable,
                    "-m",
                    "PyInstaller",
                    "--clean",
                    "--onefile",
                    "--windowed",
                    "--name",
                    game_settings["file_name"],
                    "--noconfirm",
                    "--paths",
                    str(project_dir),
                    "--distpath",
                    str(output_dir),
                    "--workpath",
                    str(build_dir / "work"),
                    "--specpath",
                    str(build_dir),
                    str(project_dir / "game_player.py"),
                ]
                native_names = (
                    ("engine_physics_native.dll", "engine_native.dll")
                    if sys.platform == "win32"
                    else ("engine_physics_native.dylib", "libengine_native.dylib")
                    if sys.platform == "darwin"
                    else ("engine_physics_native.so", "libengine_native.so")
                )
                for native_name in native_names:
                    native_library = project_dir / "engine_editor" / native_name
                    if native_library.is_file():
                        command.extend((
                            "--add-binary",
                            f"{native_library}{os.pathsep}engine_editor",
                        ))
                for import_name in mod_import_names(
                    game_settings.get("mod_sources", {}),
                ):
                    command.extend(("--hidden-import", import_name))
                result = subprocess.run(
                    command,
                    cwd=project_dir,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if result.returncode != 0:
                    details = (result.stderr or result.stdout).strip()
                    if not details:
                        details = "PyInstaller exited without reporting an error."
                    self.root.after(
                        0,
                        self.finish_build,
                        f"Game build failed:\n{details[-3000:]}",
                        False,
                    )
                    return

                built_game = output_dir / f"{game_settings['file_name']}.exe"
                if not built_game.is_file():
                    self.root.after(
                        0,
                        self.finish_build,
                        f"Build finished, but the game executable was not found:\n{built_game}",
                        False,
                    )
                    return

                append_game_payload(
                    built_game,
                    exe_path,
                    game_blocks,
                    game_settings,
                )
        except (OSError, TypeError, ValueError) as error:
            self.root.after(
                0, self.finish_build, f"Could not build the game executable: {error}", False,
            )
            return
        self.root.after(0, self.finish_build, f"Standalone game created:\n{exe_path}", True)

    def finish_build(self, message, succeeded):
        """Refresh the export menu and report the build result to the user."""
        self.building_game = False
        self.refresh_export_menu()
        self.status.set("Build complete." if succeeded else "Build failed.")
        if succeeded:
            messagebox.showinfo("Build complete", message, parent=self.root)
        else:
            messagebox.showerror("Build failed", message, parent=self.root)
