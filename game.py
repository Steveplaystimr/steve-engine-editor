"""Render and run a project as a clean, standalone game window."""

import base64
import binascii
import copy
import io
import math
import random
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog

from PIL import Image, ImageTk

from .blocks import (
    apply_color_effect,
    evaluate_condition,
    evaluate_spatial_reporter,
    expand_custom_blocks,
    expand_script,
    parse_value,
    parse_reporter_expression,
    perform_calculation,
    split_event_scripts,
    typed_value,
    validate_control_flow,
)
from .opengl_stage import OpenGLStage
from .system_info import get_process_memory_mb
from sse_modding_tools import MOD_REGISTRY, ModContext
from .sound import imported_audio_is_playing, play_project_sound, stop_sounds


class GamePlayer:
    """Run game scripts and draw their sprite, costumes, and backdrop."""

    def __init__(self, root, blocks, settings=None):
        """Validate game data, create the stage window, and bind game events."""
        if not isinstance(blocks, list) or any(not isinstance(block, dict) for block in blocks):
            raise ValueError(
                "This game file contains an invalid block list. "
                "Rebuild it from the editor and try again."
            )
        self.root = root
        self.blocks = [block.copy() for block in blocks]
        self.settings = settings if isinstance(settings, dict) else {}
        mod_sources = self.settings.get("mod_sources", {})
        mod_errors = MOD_REGISTRY.load_sources(mod_sources)
        if mod_errors:
            raise ValueError(
                "Could not load the Python mods bundled with this game:\n"
                + "\n".join(mod_errors)
            )
        self.read_write_blocks_enabled = (
            self.settings.get("read_write_blocks", True) is True
        )
        self.game_sprites = self.settings.get("sprites", [])
        self.custom_blocks = self.settings.get("custom_blocks", [])
        expand_custom_blocks([], self.custom_blocks)
        self.rendering_mode = self.settings.get("rendering", "software")
        if self.rendering_mode not in ("software", "opengl"):
            raise ValueError("Game rendering must be 'software' or 'opengl'.")
        self.active_game_sprite_index = self.settings.get("active_sprite_index", 0)
        if (
            not isinstance(self.game_sprites, list)
            or any(not isinstance(sprite, dict) for sprite in self.game_sprites)
            or not isinstance(self.active_game_sprite_index, int)
            or (
                self.game_sprites
                and not 0 <= self.active_game_sprite_index < len(self.game_sprites)
            )
        ):
            raise ValueError("This game contains an invalid sprite list.")
        if any(
            not isinstance(sprite.get("blocks", []), list)
            for sprite in self.game_sprites
        ):
            raise ValueError("A game sprite contains an invalid script.")
        renderer_blocks = [self.blocks]
        renderer_blocks.extend(
            sprite.get("blocks", []) for sprite in self.game_sprites
        )
        for sprite_blocks in renderer_blocks:
            expanded_blocks = expand_custom_blocks(sprite_blocks, self.custom_blocks)
            for action in expand_script(expanded_blocks):
                if action.get("kind") == "rendering":
                    mode = action.get("mode")
                    if mode not in ("software", "opengl"):
                        raise ValueError(
                            "Rendering blocks must select Software (CPU) or OpenGL (GPU)."
                        )
                    self.rendering_mode = mode
        self.variables = {}
        self.expanded_blocks = expand_custom_blocks(self.blocks, self.custom_blocks)
        self.scripts = split_event_scripts(self.expanded_blocks)
        self.memory_limit_mb = 512
        self.running = False
        sprite_state = self.settings.get("sprite", {})
        self.backdrops = self.settings.get("backdrops", [])
        self.sounds = self.settings.get("sounds", [])
        if not isinstance(self.sounds, list):
            raise ValueError("This game contains malformed imported sound data.")
        sound_names = set()
        for sound in self.sounds:
            if (
                not isinstance(sound, dict)
                or not isinstance(sound.get("name"), str)
                or not sound["name"].strip()
                or sound.get("format") not in ("mp3", "wav", "ogg")
                or not isinstance(sound.get("audio_data"), str)
            ):
                raise ValueError("This game contains malformed imported sound data.")
            if sound["name"].casefold() in sound_names:
                raise ValueError("This game contains duplicate imported sound names.")
            sound_names.add(sound["name"].casefold())
            try:
                audio_data = base64.b64decode(sound["audio_data"], validate=True)
            except (ValueError, binascii.Error) as error:
                raise ValueError("This game contains invalid imported sound data.") from error
            if not audio_data or len(audio_data) > 20 * 1024 * 1024:
                raise ValueError(
                    "Game sound assets must be non-empty and no larger than 20 MB."
                )
        if not isinstance(sprite_state, dict) or not isinstance(self.backdrops, list):
            raise ValueError("This game contains invalid sprite or backdrop data.")
        self.sprite_x = 200 + sprite_state.get("x", 0)
        self.sprite_y = 205 - sprite_state.get("y", 0)
        self.sprite_angle = sprite_state.get("direction", 90) % 360
        self.sprite_size = sprite_state.get("size", 100)
        self.sprite_visible = sprite_state.get("visible", True)
        self.sprite_costumes = sprite_state.get("costumes", [])
        if (
            not isinstance(self.sprite_size, int)
            or not 10 <= self.sprite_size <= 300
            or not isinstance(self.sprite_visible, bool)
        ):
            raise ValueError("This game contains invalid sprite settings.")
        if not isinstance(self.sprite_costumes, list) or any(
            not isinstance(costume, dict)
            or not isinstance(costume.get("name"), str)
            or not isinstance(costume.get("image_data"), str)
            for costume in self.sprite_costumes
        ):
            raise ValueError("This game contains invalid sprite costume data.")
        if any(
            not isinstance(backdrop, dict)
            or not isinstance(backdrop.get("name"), str)
            or not isinstance(backdrop.get("image_data"), str)
            for backdrop in self.backdrops
        ):
            raise ValueError("This game contains invalid backdrop data.")
        self.sprite_costume_index = (
            sprite_state.get("costume_index", 0) % len(self.sprite_costumes)
            if self.sprite_costumes else 0
        )
        self.sprite_effect = sprite_state.get("effect", 0)
        self.sprite_layer_front = sprite_state.get("layer_front", True)
        self.backdrop_index = sprite_state.get("backdrop_index", 0)
        if self.backdrops:
            self.backdrop_index %= len(self.backdrops)
        self.initial_sprite_state = {
            "x": self.sprite_x,
            "y": self.sprite_y,
            "angle": self.sprite_angle,
            "size": self.sprite_size,
            "visible": self.sprite_visible,
            "costume_index": self.sprite_costume_index,
            "effect": self.sprite_effect,
            "layer_front": self.sprite_layer_front,
            "backdrop_index": self.backdrop_index,
        }
        self.initial_game_sprites = copy.deepcopy(self.game_sprites)
        self._running_sprite_queue = False
        self._sprite_script_queue = []
        self._sprite_queue_event_key = None
        self.rotation_style = "all around"
        self.speech = ""
        self.speech_kind = "say"
        self.sound_pitch = 0
        self.sound_volume = 100
        self.fullscreen = False
        self.stage_image_refs = []
        self.opengl_error = None

        title = self.settings.get("window_title", "")
        width = self.settings.get("width", 960)
        height = self.settings.get("height", 720)
        if not isinstance(title, str):
            raise ValueError("The game window title must be text.")
        if (
            not isinstance(width, int) or not isinstance(height, int)
            or not 360 <= width <= 3840 or not 360 <= height <= 2160
        ):
            raise ValueError("Game window size must be between 360x360 and 3840x2160.")
        self.root.title(title)
        self.root.geometry(f"{width}x{height}")
        self.root.minsize(500, 450)
        self.root.configure(bg="white")

        self.opengl_stage = None
        if self.rendering_mode == "opengl":
            self.root.withdraw()
            self.opengl_stage = OpenGLStage(
                width, height, title, self.on_opengl_key_press, self.root.quit,
            )
            self.stage = self.opengl_stage
            if self.settings.get("fullscreen", False):
                self.fullscreen = True
                self.opengl_stage.set_fullscreen(True)
            self.root.after(16, self.poll_opengl_events)
        else:
            self.stage = tk.Canvas(
                root, bg="white", highlightthickness=0,
            )
            self.stage.pack(fill="both", expand=True)
            self.stage.bind("<Configure>", self.on_stage_resize)
            self.root.bind("<F11>", self.toggle_fullscreen)
            self.root.bind("<Escape>", self.leave_fullscreen)
            self.root.bind_all("<KeyPress>", self.on_key_press, add="+")
            if self.settings.get("fullscreen", False):
                self.root.after(100, self.toggle_fullscreen)
        self.draw_stage()
        has_flag_script = any(script["event"] == "flag" for script in self.scripts)
        if self.game_sprites:
            has_flag_script = has_flag_script or any(
                any(
                    script["event"] == "flag"
                    for script in split_event_scripts(
                        expand_custom_blocks(
                            sprite.get("blocks", []), self.custom_blocks,
                        )
                    )
                )
                for sprite in self.game_sprites
            )
        if has_flag_script:
            self.root.after(100, self.run_script)

    def save_active_game_sprite(self):
        """Save the live sprite state and script before switching actors."""
        if not self.game_sprites:
            return
        self.game_sprites[self.active_game_sprite_index].update(
            x=round(self.sprite_x - 200),
            y=round(205 - self.sprite_y),
            direction=self.sprite_angle,
            size=self.sprite_size,
            visible=self.sprite_visible,
            costumes=self.sprite_costumes,
            costume_index=self.sprite_costume_index,
            effect=self.sprite_effect,
            layer_front=self.sprite_layer_front,
            blocks=self.blocks,
        )

    def activate_game_sprite(self, index, reset):
        """Load one sprite's independent state and script into the runtime."""
        self.save_active_game_sprite()
        source = self.initial_game_sprites[index] if reset else self.game_sprites[index]
        self.active_game_sprite_index = index
        self.sprite_x = 200 + source.get("x", 0)
        self.sprite_y = 205 - source.get("y", 0)
        self.sprite_angle = source.get("direction", 90) % 360
        self.sprite_size = source.get("size", 100)
        self.sprite_visible = source.get("visible", True)
        self.sprite_costumes = source.get("costumes", [])
        self.sprite_costume_index = (
            source.get("costume_index", 0) % len(self.sprite_costumes)
            if self.sprite_costumes else 0
        )
        self.sprite_effect = source.get("effect", 0)
        self.sprite_layer_front = source.get("layer_front", True)
        self.blocks = copy.deepcopy(source.get("blocks", []))
        self.expanded_blocks = expand_custom_blocks(self.blocks, self.custom_blocks)
        self.scripts = split_event_scripts(self.expanded_blocks)
        self.initial_sprite_state = {
            "x": self.sprite_x,
            "y": self.sprite_y,
            "angle": self.sprite_angle,
            "size": self.sprite_size,
            "visible": self.sprite_visible,
            "costume_index": self.sprite_costume_index,
            "effect": self.sprite_effect,
            "layer_front": self.sprite_layer_front,
            "backdrop_index": self.backdrop_index,
        }

    def run_next_sprite_script(self):
        """Run the next sprite's matching event script in the generated game."""
        if not self._sprite_script_queue:
            self._running_sprite_queue = False
            return
        sprite_index = self._sprite_script_queue.pop(0)
        self.activate_game_sprite(
            sprite_index,
            reset=self._sprite_queue_event_key is None,
        )
        self.run_script(
            self._sprite_queue_event_key,
            _from_sprite_queue=True,
        )

    def toggle_fullscreen(self, _event=None):
        """Switch between windowed mode and fullscreen mode."""
        self.fullscreen = not self.fullscreen
        if self.opengl_stage is not None:
            self.opengl_stage.set_fullscreen(self.fullscreen)
        else:
            self.root.attributes("-fullscreen", self.fullscreen)
        return "break"

    def leave_fullscreen(self, _event=None):
        """Leave fullscreen when Escape is pressed."""
        if self.fullscreen:
            self.fullscreen = False
            if self.opengl_stage is not None:
                self.opengl_stage.set_fullscreen(False)
            else:
                self.root.attributes("-fullscreen", False)
            return "break"
        return None

    def on_key_press(self, event):
        """Find and run a script triggered by the pressed key."""
        key = self.normalize_key(event.keysym)
        self.run_script(key)

    def sensing_reporter(self, expression):
        """Resolve a spatial reporter against the active game stage."""
        kind, arguments = parse_reporter_expression(expression)
        if self.opengl_stage is not None:
            pointer_x, pointer_y = self.opengl_stage.pointer_position()
            stage_x = pointer_x - self.stage.winfo_width() / 2 + 200
            stage_y = self.stage.winfo_height() / 2 - pointer_y + 205
        elif isinstance(self.stage, tk.Canvas):
            pointer_x, pointer_y = self.root.winfo_pointerxy()
            stage_x = (
                pointer_x - self.stage.winfo_rootx()
                - self.stage.winfo_width() / 2 + 200
            )
            stage_y = (
                self.stage.winfo_rooty() + self.stage.winfo_height() / 2
                - pointer_y + 205
            )
        else:
            raise RuntimeError("The game stage is not available for sensing.")
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
            max(self.stage.winfo_width(), 360),
            max(self.stage.winfo_height(), 360),
            stage_x, stage_y,
            self.game_sprites, self.active_game_sprite_index,
            backdrop_data, actor_image_data,
        )

    def execute_mod_block(self, block):
        """Run a Python block against the active game sprite and variables."""
        sprite = {
            "x": self.sprite_x - 200,
            "y": 205 - self.sprite_y,
            "direction": self.sprite_angle,
            "size": self.sprite_size,
            "visible": self.sprite_visible,
        }
        MOD_REGISTRY.execute(block, ModContext(sprite, self.variables))
        self.sprite_x = 200 + sprite["x"]
        self.sprite_y = 205 - sprite["y"]
        self.sprite_angle = sprite["direction"]
        self.sprite_size = sprite["size"]
        self.sprite_visible = sprite["visible"]

    def on_opengl_key_press(self, key):
        """Handle native game-window keys without exposing the hidden Tk root."""
        if key == "F11":
            self.toggle_fullscreen()
        elif key == "Escape":
            self.leave_fullscreen()
        else:
            self.run_script(self.normalize_key(key))

    def poll_opengl_events(self):
        """Keep the native OpenGL window responsive alongside Tk timers."""
        if self.opengl_stage is not None:
            try:
                self.opengl_stage.poll_events()
            except RuntimeError as error:
                self.opengl_error = str(error)
                self.root.quit()
                return
            self.root.after(16, self.poll_opengl_events)

    @staticmethod
    def normalize_key(key):
        """Convert Tkinter key names to the names stored in key blocks."""
        return {
            "return": "enter",
            "kp_enter": "enter",
            "space": "space",
            "prior": "page_up",
            "next": "page_down",
        }.get(key.lower(), key.lower())

    def draw_stage(self):
        """Redraw the backdrop, image blocks, and sprite in correct layer order."""
        self.save_active_game_sprite()
        width = max(self.stage.winfo_width(), 360)
        height = max(self.stage.winfo_height(), 360)
        self.stage.delete("all")
        self.stage.create_rectangle(0, 0, width, height, fill="white", outline="")

        self.stage_image_refs = []
        if self.backdrops:
            backdrop = self.backdrops[self.backdrop_index]
            try:
                # Image bytes are stored as base64 text in the embedded project data.
                image_bytes = base64.b64decode(backdrop["image_data"], validate=True)
                with Image.open(io.BytesIO(image_bytes)) as source:
                    image = source.convert("RGBA").resize(
                        (width, height), Image.Resampling.LANCZOS,
                    )
                    photo = self.make_stage_image(image)
            except (KeyError, ValueError, OSError) as error:
                raise ValueError(f"Could not display backdrop: {error}") from error
            self.stage_image_refs.append(photo)
            self.stage.create_image(width / 2, height / 2, image=photo)

        center_x = width / 2 + self.sprite_x - 200
        center_y = height / 2 + self.sprite_y - 205
        self.draw_other_sprites(front=False)
        if not self.sprite_layer_front:
            self.draw_sprite(center_x, center_y)
        for block in sorted(
            self.expanded_blocks,
            key=lambda item: (
                item.get("workspace_y", 0),
                item.get("workspace_x", 0),
            ),
        ):
            if block.get("kind") != "image" or not block.get("image_data"):
                continue
            try:
                image_bytes = base64.b64decode(block["image_data"], validate=True)
                with Image.open(io.BytesIO(image_bytes)) as source:
                    image = source.convert("RGBA")
                    image.thumbnail((min(width, 400), min(height, 400)))
                    photo = self.make_stage_image(image)
            except (ValueError, OSError):
                continue
            self.stage_image_refs.append(photo)
            self.stage.create_image(
                width / 2 + block.get("x", 0),
                height / 2 - block.get("y", 0),
                image=photo,
            )

        self.draw_other_sprites(front=True)
        if self.sprite_layer_front:
            self.draw_sprite(center_x, center_y)

    def draw_other_sprites(self, front):
        """Draw the non-active saved sprites around the active game sprite."""
        saved_state = (
            self.sprite_x,
            self.sprite_y,
            self.sprite_angle,
            self.sprite_size,
            self.sprite_visible,
            self.sprite_costumes,
            self.sprite_costume_index,
            self.sprite_effect,
            self.sprite_layer_front,
        )
        speech = self.speech
        self.speech = ""
        width = max(self.stage.winfo_width(), 360)
        height = max(self.stage.winfo_height(), 360)
        for index, sprite in enumerate(self.game_sprites):
            if index == self.active_game_sprite_index:
                continue
            if bool(sprite.get("layer_front", True)) != front:
                continue
            self.sprite_x = 200 + sprite.get("x", 0)
            self.sprite_y = 205 - sprite.get("y", 0)
            self.sprite_angle = sprite.get("direction", 90)
            self.sprite_size = sprite.get("size", 100)
            self.sprite_visible = sprite.get("visible", True)
            self.sprite_costumes = sprite.get("costumes", [])
            self.sprite_costume_index = sprite.get("costume_index", 0)
            self.sprite_effect = sprite.get("effect", 0)
            center_x = width / 2 + self.sprite_x - 200
            center_y = height / 2 + self.sprite_y - 205
            self.draw_sprite(center_x, center_y)
        (
            self.sprite_x,
            self.sprite_y,
            self.sprite_angle,
            self.sprite_size,
            self.sprite_visible,
            self.sprite_costumes,
            self.sprite_costume_index,
            self.sprite_effect,
            self.sprite_layer_front,
        ) = saved_state
        self.speech = speech

    def draw_sprite(self, center_x, center_y):
        """Draw the current costume or fallback sprite and its speech bubble."""
        if not self.sprite_visible:
            return
        if self.sprite_costumes:
            costume = self.sprite_costumes[self.sprite_costume_index]
            try:
                image_bytes = base64.b64decode(costume["image_data"], validate=True)
                with Image.open(io.BytesIO(image_bytes)) as source:
                    image = source.convert("RGBA")
                    image = apply_color_effect(image, self.sprite_effect)
                    max_dimension = max(1, round(400 * self.sprite_size / 100))
                    image.thumbnail(
                        (max_dimension, max_dimension), Image.Resampling.LANCZOS,
                    )
                    if self.rotation_style == "left-right":
                        rendered_angle = 90 if 90 < self.sprite_angle < 270 else -90
                    elif self.rotation_style == "don't rotate":
                        rendered_angle = 0
                    else:
                        rendered_angle = self.sprite_angle
                    image = image.rotate(
                        -rendered_angle, expand=True,
                        resample=Image.Resampling.BICUBIC,
                    )
                    photo = self.make_stage_image(image)
            except (KeyError, ValueError, OSError) as error:
                raise ValueError(f"Could not display costume: {error}") from error
            self.stage_image_refs.append(photo)
            self.stage.create_image(center_x, center_y, image=photo)
            if self.speech:
                bubble_x = min(max(center_x, 65), max(65, self.stage.winfo_width() - 65))
                self.stage.create_oval(
                    bubble_x - 62, center_y - 73, bubble_x + 62, center_y - 39,
                    fill="white", outline="#777",
                )
                self.stage.create_text(
                    bubble_x, center_y - 56, text=self.speech[:22],
                    fill="#333", font=("Segoe UI", 9),
                )
                if self.speech_kind == "think":
                    self.stage.create_oval(
                        bubble_x - 48, center_y - 36, bubble_x - 42,
                        center_y - 30, fill="white", outline="#777",
                    )
                    self.stage.create_oval(
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
        self.stage.create_polygon(
            tip[0], tip[1], left[0], left[1], center_x, center_y,
            right[0], right[1], fill="#ff9f43", outline="#b35a16", width=2,
        )
        self.stage.create_oval(
            center_x - 17 * scale, center_y - 17 * scale,
            center_x + 17 * scale, center_y + 17 * scale,
            fill="#ffcc4d", outline="#b35a16", width=2,
        )
        self.stage.create_oval(
            center_x - 6 * scale, center_y - 7 * scale,
            center_x - 2 * scale, center_y - 3 * scale,
            fill="#333", outline="",
        )
        self.stage.create_oval(
            center_x + 2 * scale, center_y - 7 * scale,
            center_x + 6 * scale, center_y - 3 * scale,
            fill="#333", outline="",
        )
        if self.speech:
            bubble_x = min(
                max(center_x, 65), max(65, self.stage.winfo_width() - 65),
            )
            self.stage.create_oval(
                bubble_x - 62, center_y - 73, bubble_x + 62, center_y - 39,
                fill="white", outline="#777",
            )
            self.stage.create_text(
                bubble_x, center_y - 56, text=self.speech[:22],
                fill="#333", font=("Segoe UI", 9),
            )
            if self.speech_kind == "think":
                self.stage.create_oval(
                    bubble_x - 48, center_y - 36, bubble_x - 42,
                    center_y - 30, fill="white", outline="#777",
                )
                self.stage.create_oval(
                    bubble_x - 38, center_y - 28, bubble_x - 34,
                    center_y - 24, fill="white", outline="#777",
                )

    def make_stage_image(self, image):
        """Keep PIL pixels for GPU drawing or create a Tk image for software."""
        if self.opengl_stage is not None:
            return image.copy()
        return ImageTk.PhotoImage(image)

    def on_stage_resize(self, event):
        """Redraw the game canvas when the game window changes size."""
        if event.width > 1 and event.height > 1:
            self.draw_stage()

    def run_script(self, event_key=None, _from_sprite_queue=False):
        """Run matching sprite scripts using non-blocking Tkinter timers."""
        # The after() callbacks let the game window continue responding to input.
        if self.running:
            return
        if not _from_sprite_queue and len(self.game_sprites) > 1:
            matching_sprites = []
            try:
                for index, sprite in enumerate(self.game_sprites):
                    selected_script = next(
                        (
                            script
                            for script in split_event_scripts(
                                expand_custom_blocks(
                                    sprite.get("blocks", []), self.custom_blocks,
                                )
                            )
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
            except (KeyError, TypeError, ValueError) as error:
                messagebox.showerror("Game script error", str(error), parent=self.root)
                return
            if not matching_sprites:
                return
            self._sprite_script_queue = matching_sprites
            self._sprite_queue_event_key = event_key
            self._running_sprite_queue = True
            self.run_next_sprite_script()
            return
        if event_key is None:
            selected_scripts = [
                script for script in self.scripts if script["event"] == "flag"
            ]
        else:
            normalized_key = self.normalize_key(event_key or "")
            selected_scripts = [
                script for script in self.scripts
                if script["event"] == "key"
                and script.get("key") == normalized_key
            ]
        try:
            actions = []
            for script in selected_scripts:
                actions.extend(expand_script(script["blocks"]))
        except ValueError as error:
            messagebox.showerror("Game script error", str(error), parent=self.root)
            self._sprite_script_queue.clear()
            self._running_sprite_queue = False
            return
        if not actions:
            if self._running_sprite_queue:
                self.run_next_sprite_script()
            return

        if event_key is None:
            self.sprite_x = self.initial_sprite_state["x"]
            self.sprite_y = self.initial_sprite_state["y"]
            self.sprite_angle = self.initial_sprite_state["angle"]
            self.sprite_size = self.initial_sprite_state["size"]
            self.sprite_visible = self.initial_sprite_state["visible"]
            self.sprite_costume_index = self.initial_sprite_state["costume_index"]
            self.sprite_effect = self.initial_sprite_state["effect"]
            self.sprite_layer_front = self.initial_sprite_state["layer_front"]
            self.backdrop_index = self.initial_sprite_state["backdrop_index"]
            self.speech = ""
            self.variables = {}
            self.sound_pitch = 0
            self.sound_volume = 100
        try:
            control_jumps = validate_control_flow(actions)
        except ValueError as error:
            messagebox.showerror("Game script error", str(error), parent=self.root)
            self._sprite_script_queue.clear()
            self._running_sprite_queue = False
            return
        executed_steps = 0
        self.running = True
        self.draw_stage()

        def stop_game(status, failed=False):
            """End the running script and display a message for failures."""
            self.running = False
            self.draw_stage()
            if failed:
                self._sprite_script_queue.clear()
                self._running_sprite_queue = False
                messagebox.showerror("Game stopped", status, parent=self.root)
            elif self._running_sprite_queue:
                self.run_next_sprite_script()

        def check_memory():
            """Measure memory before continuing, enforcing the game's limit."""
            try:
                memory_mb = get_process_memory_mb()
            except OSError as error:
                stop_game(f"Could not read app memory usage: {error}", failed=True)
                return False

            if memory_mb > self.memory_limit_mb:
                stop_game(
                    f"Game stopped: memory use ({memory_mb:.1f} MB) exceeded "
                    f"the {self.memory_limit_mb} MB limit.",
                    failed=True,
                )
                return False
            return True

        def schedule_next(index, remaining_ms, clear_speech=False):
            """Use Tkinter timers to wait while keeping the game window responsive."""
            interval = min(250, remaining_ms)

            def resume():
                """Continue the delay or move to the next action."""
                if not self.running or not check_memory():
                    return
                remaining = remaining_ms - interval
                if remaining <= 0:
                    if clear_speech:
                        self.speech = ""
                        self.draw_stage()
                    run_action(index)
                else:
                    schedule_next(index, remaining, clear_speech)

            self.root.after(interval, resume)

        def wait_for_imported_audio(index):
            """Continue a Play Sound block once its imported audio ends."""
            if not self.running or not check_memory():
                return
            if imported_audio_is_playing():
                self.root.after(30, wait_for_imported_audio, index)
            else:
                run_action(index)

        def animate_glide(index, start_x, start_y, target_x, target_y, duration_ms):
            """Animate movement toward a target over several timed frames."""
            steps = max(1, min(60, round(duration_ms / 40)))

            def move_step(step):
                """Draw the current position, then schedule the next glide frame."""
                if not self.running or not check_memory():
                    return
                fraction = step / steps
                self.sprite_x = start_x + (target_x - start_x) * fraction
                self.sprite_y = start_y + (target_y - start_y) * fraction
                self.draw_stage()
                if step >= steps:
                    run_action(index + 1)
                else:
                    # Let Tkinter process window events between animation frames.
                    self.root.after(
                        max(1, round(duration_ms / steps)),
                        move_step,
                        step + 1,
                    )

            move_step(1)

        def run_action(index):
            """Execute one block action and decide when the next action runs."""
            nonlocal executed_steps
            if index >= len(actions):
                stop_game("Game finished.")
                return
            executed_steps += 1
            if executed_steps > 5000:
                stop_game(
                    "Game stopped after 5,000 steps. Check for a loop that never ends.",
                    failed=True,
                )
                return

            action = actions[index]
            kind = action["kind"]
            wait_for_audio = False
            delay = 0 if action.get("_run_without_refresh") else 160
            next_index = index + 1
            if kind != "memory_limit" and not check_memory():
                return

            try:
                # Each branch applies one block's behavior to the game state.
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
                    if self.opengl_stage is not None:
                        pointer_x, pointer_y = self.opengl_stage.pointer_position()
                        mouse_x = pointer_x - self.stage.winfo_width() / 2 + 200
                        mouse_y = self.stage.winfo_height() / 2 - pointer_y + 205
                    elif isinstance(self.stage, tk.Canvas):
                        pointer_x, pointer_y = self.root.winfo_pointerxy()
                        mouse_x = (
                            pointer_x - self.stage.winfo_rootx()
                            - self.stage.winfo_width() / 2 + 200
                        )
                        mouse_y = (
                            self.stage.winfo_rooty() + self.stage.winfo_height() / 2
                            - pointer_y + 205
                        )
                    else:
                        raise RuntimeError("The game stage is not available.")
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
                    height = max(self.stage.winfo_height(), 360)
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
                elif kind == "change_size":
                    self.sprite_size = max(
                        10, min(300, self.sprite_size + action["amount"]),
                    )
                elif kind == "set_size":
                    self.sprite_size = max(10, min(300, action["value"]))
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
                elif kind == "layer_front":
                    self.sprite_layer_front = True
                elif kind == "layer_back":
                    self.sprite_layer_front = False
                elif kind == "print":
                    self.speech = str(parse_value(action["value"], self.variables))
                    delay = 2000
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
                    delay = 1500
                elif kind == "input":
                    answer = simpledialog.askstring(
                        "Game input", action["prompt"], parent=self.root,
                    )
                    self.variables[action["name"]] = answer or ""
                elif kind in ("read_file", "write_file"):
                    if not self.read_write_blocks_enabled:
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
                elif kind == "if":
                    if not evaluate_condition(
                        action, self.variables, self.sensing_reporter,
                    ):
                        alternate = control_jumps.get((index, "else"))
                        next_index = (
                            alternate + 1 if alternate is not None
                            else control_jumps[index] + 1
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
                    if not check_memory():
                        return
                elif kind == "memory_result":
                    get_process_memory_mb()
                elif kind == "rendering":
                    pass
            except (ArithmeticError, KeyError, OSError, RuntimeError, TypeError, ValueError) as error:
                stop_game(f"Game block error: {error}", failed=True)
                return

            if not action.get("_run_without_refresh"):
                self.draw_stage()
            if kind in ("if", "else", "end_if", "while", "end_while"):
                self.root.after(1, run_action, next_index)
            elif wait_for_audio:
                self.root.after(30, wait_for_imported_audio, next_index)
            else:
                schedule_next(
                    next_index, delay,
                    clear_speech=kind in ("say", "think", "print", "volume"),
                )

        run_action(0)


def run_game(blocks, settings=None):
    """Create the game window and start Tkinter's event loop."""
    root = tk.Tk()
    player = None
    try:
        player = GamePlayer(root, blocks, settings)
    except (ImportError, KeyError, OSError, RuntimeError, TypeError, ValueError) as error:
        root.withdraw()
        messagebox.showerror("Game could not start", str(error), parent=root)
        root.destroy()
        return
    try:
        root.mainloop()
    finally:
        if player is not None and player.opengl_stage is not None:
            player.opengl_stage.close()
            if player.opengl_error:
                messagebox.showerror(
                    "OpenGL game stopped",
                    player.opengl_error,
                    parent=root,
                )
        if root.winfo_exists():
            root.destroy()
