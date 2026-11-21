"""Draw game stages through an OpenGL window backed by the GPU."""

from __future__ import annotations

import math
from collections import OrderedDict

import pygame
from OpenGL.error import GLError
from OpenGL.GL import (
    GL_BLEND,
    GL_COLOR_BUFFER_BIT,
    GL_LINE_LOOP,
    GL_MODELVIEW,
    GL_ONE_MINUS_SRC_ALPHA,
    GL_POLYGON,
    GL_PROJECTION,
    GL_QUADS,
    GL_SRC_ALPHA,
    GL_TEXTURE_2D,
    GL_TEXTURE_MAG_FILTER,
    GL_TEXTURE_MIN_FILTER,
    GL_UNSIGNED_BYTE,
    GL_LINEAR,
    GL_UNPACK_ALIGNMENT,
    glBegin,
    glBindTexture,
    glBlendFunc,
    glClear,
    glClearColor,
    glColor4ub,
    glDeleteTextures,
    glDisable,
    glEnable,
    glEnd,
    glGenTextures,
    glLineWidth,
    glLoadIdentity,
    glMatrixMode,
    glOrtho,
    glPixelStorei,
    glTexCoord2f,
    glTexImage2D,
    glTexParameteri,
    glVertex2f,
    glViewport,
)
from PIL import Image, ImageColor

from .frustum import FrustumCuller


class OpenGLStage:
    """Canvas-like drawing surface that renders game items using OpenGL."""

    def __init__(
        self, width, height, title, key_callback, close_callback,
        touch_callback=None,
    ):
        pygame.init()
        self.width = width
        self.height = height
        self.title = title
        self.key_callback = key_callback
        self.close_callback = close_callback
        self.touch_callback = touch_callback
        self.items = []
        self.textures = OrderedDict()
        self.texture_bytes = 0
        self.texture_budget_bytes = 96 * 1024 * 1024
        self.texture_idle_frames = 120
        self.frame_index = 0
        self.used_textures = set()
        self.fonts = {}
        self.fullscreen = False
        self.windowed_size = (width, height)
        self.frustum_culler = FrustumCuller()
        try:

            self.window = pygame.display.set_mode(
                (width, height),
                pygame.OPENGL | pygame.DOUBLEBUF | pygame.RESIZABLE,
            )
            pygame.display.set_caption(title)
            pygame.font.init()
            self._configure_projection()
        except (pygame.error, GLError, RuntimeError) as error:
            pygame.quit()
            raise RuntimeError(f"Could not initialize the OpenGL game window: {error}") from error

    def pack(self, **_kwargs):
        """Match the Tk widget API; the OpenGL window owns its own layout."""

    def bind(self, _event, _callback):
        """Accept Tk-style bindings; Pygame events are polled by poll_events."""

    def winfo_width(self):
        """Return the live OpenGL window width."""
        return self.width

    def winfo_height(self):
        """Return the live OpenGL window height."""
        return self.height

    @staticmethod
    def pointer_position():
        """Return the mouse position relative to the native game window."""
        return pygame.mouse.get_pos()

    def delete(self, _target):
        """Clear all queued draw calls, matching Canvas.delete('all')."""
        self.items.clear()

    def create_rectangle(self, x1, y1, x2, y2, **options):
        """Queue a filled rectangle and optional border."""
        self.items.append(("rectangle", (x1, y1, x2, y2), options))

    def create_oval(self, x1, y1, x2, y2, **options):
        """Queue a filled ellipse and optional border."""
        self.items.append(("oval", (x1, y1, x2, y2), options))

    def create_polygon(self, *coordinates, **options):
        """Queue a polygon using the supplied flat coordinate list."""
        self.items.append(("polygon", coordinates, options))

    def create_text(self, x, y, **options):
        """Queue a text label using Tk-compatible anchor names."""
        self.items.append(("text", (x, y), options))

    def create_image(self, x, y, **options):
        """Queue a PIL-backed image for GPU texture rendering."""
        self.items.append(("image", (x, y), options))

    def set_fullscreen(self, fullscreen):
        """Switch the game window between fullscreen and windowed modes."""
        if self.fullscreen == fullscreen:
            return
        self.fullscreen = fullscreen
        if fullscreen:
            display_info = pygame.display.Info()
            size = (display_info.current_w, display_info.current_h)
            flags = pygame.OPENGL | pygame.DOUBLEBUF | pygame.FULLSCREEN
        else:
            size = self.windowed_size
            flags = pygame.OPENGL | pygame.DOUBLEBUF | pygame.RESIZABLE
        try:
            self.window = pygame.display.set_mode(size, flags)
            self.width, self.height = size
            self._configure_projection()
        except (pygame.error, GLError) as error:
            raise RuntimeError(f"Could not change the OpenGL window mode: {error}") from error

    def poll_events(self):
        """Process native window events and key presses without blocking Tk."""
        try:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    self.close_callback()
                elif event.type == pygame.VIDEORESIZE and not self.fullscreen:
                    self.width = max(360, event.w)
                    self.height = max(360, event.h)
                    self.windowed_size = (self.width, self.height)
                    self.window = pygame.display.set_mode(
                        (self.width, self.height),
                        pygame.OPENGL | pygame.DOUBLEBUF | pygame.RESIZABLE,
                    )
                    self._configure_projection()
                elif event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_F11:
                        self.key_callback("F11")
                    elif event.key == pygame.K_ESCAPE:
                        self.key_callback("Escape")
                    else:
                        self.key_callback(pygame.key.name(event.key))
                elif self.touch_callback is not None and event.type == pygame.FINGERDOWN:
                    self.touch_callback("touch_start", event.finger_id, event.x, event.y)
                elif self.touch_callback is not None and event.type == pygame.FINGERMOTION:
                    self.touch_callback("motion", event.finger_id, event.x, event.y)
                elif self.touch_callback is not None and event.type == pygame.FINGERUP:
                    self.touch_callback("touch_end", event.finger_id, event.x, event.y)
            self.render()
        except (pygame.error, GLError) as error:
            raise RuntimeError(f"OpenGL game rendering failed: {error}") from error

    def render(self):
        """Render the queued scene using the active OpenGL context."""
        self.frame_index += 1
        self.used_textures.clear()
        glClearColor(1.0, 1.0, 1.0, 1.0)
        glClear(GL_COLOR_BUFFER_BIT)
        glMatrixMode(GL_MODELVIEW)
        glLoadIdentity()
        glEnable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        glDisable(GL_TEXTURE_2D)
        visible_by_index = [True] * len(self.items)
        cullable_indices = []
        bounds = []
        for index, item in enumerate(self.items):
            item_bounds = self._item_bounds(item)
            if item_bounds is not None:
                cullable_indices.append(index)
                bounds.append(item_bounds)
        for index, visible in zip(
            cullable_indices,
            self.frustum_culler.cull(bounds, self.width, self.height),
        ):
            visible_by_index[index] = visible

        for index, (kind, coordinates, options) in enumerate(self.items):
            if not visible_by_index[index]:
                continue
            if kind == "rectangle":
                self._draw_rectangle(coordinates, options)
            elif kind == "oval":
                self._draw_oval(coordinates, options)
            elif kind == "polygon":
                self._draw_polygon(coordinates, options)
            elif kind == "text":
                self._draw_text(coordinates, options)
            elif kind == "image":
                self._draw_image(coordinates, options)
        self._trim_texture_cache()
        pygame.display.flip()

    def _item_bounds(self, item):
        """Return a conservative screen-space box, or None for uncullable text."""
        kind, coordinates, options = item
        try:
            if kind in ("rectangle", "oval"):
                x1, y1, x2, y2 = (float(value) for value in coordinates)
                padding = (
                    max(0.0, float(options.get("width", 1))) / 2
                    if options.get("outline") else 0.0
                )
                return (
                    min(x1, x2) - padding,
                    min(y1, y2) - padding,
                    max(x1, x2) + padding,
                    max(y1, y2) + padding,
                )
            if kind == "polygon" and len(coordinates) >= 6:
                xs = [float(value) for value in coordinates[::2]]
                ys = [float(value) for value in coordinates[1::2]]
                padding = (
                    max(0.0, float(options.get("width", 1))) / 2
                    if options.get("outline") else 0.0
                )
                return (
                    min(xs) - padding,
                    min(ys) - padding,
                    max(xs) + padding,
                    max(ys) + padding,
                )
            if kind == "image":
                image = options.get("image")
                source = getattr(
                    image,
                    "_pil_image",
                    image if isinstance(image, Image.Image) else None,
                )
                if source is None:
                    return None
                center_x, center_y = (float(value) for value in coordinates)
                half_width = source.width / 2
                half_height = source.height / 2
                return (
                    center_x - half_width,
                    center_y - half_height,
                    center_x + half_width,
                    center_y + half_height,
                )
            if kind == "text":
                text = str(options.get("text", ""))
                if not text:
                    return (0.0, 0.0, 0.0, 0.0)
                font_options = options.get("font", ("Arial", 10))
                font_name = font_options[0] if font_options else "Arial"
                font_size = int(font_options[1]) if len(font_options) > 1 else 10
                bold = len(font_options) > 2 and font_options[2] == "bold"
                font = self._get_font(font_name, font_size, bold)
                text_width, text_height = font.size(text)
                x, y = (float(value) for value in coordinates)
                anchor = options.get("anchor", "center")
                if anchor in ("nw", "w", "sw"):
                    left = x
                elif anchor in ("ne", "e", "se"):
                    left = x - text_width
                else:
                    left = x - text_width / 2
                if anchor in ("nw", "n", "ne"):
                    top = y
                elif anchor in ("sw", "s", "se"):
                    top = y - text_height
                else:
                    top = y - text_height / 2
                return left - 1, top - 1, left + text_width + 1, top + text_height + 1
        except (TypeError, ValueError):
            return None
        return None

    def _configure_projection(self):
        """Set a top-left-origin 2D coordinate system for stage drawing."""
        glViewport(0, 0, self.width, self.height)
        glMatrixMode(GL_PROJECTION)
        glLoadIdentity()
        glOrtho(0, self.width, self.height, 0, -1, 1)
        glMatrixMode(GL_MODELVIEW)
        glLoadIdentity()

    @staticmethod
    def _color(value, fallback=None):
        if value in (None, ""):
            return fallback
        red, green, blue = ImageColor.getrgb(value)[:3]
        return red, green, blue, 255

    @staticmethod
    def _vertex(x, y):
        glVertex2f(float(x), float(y))

    def _draw_rectangle(self, coordinates, options):
        x1, y1, x2, y2 = coordinates
        fill = self._color(options.get("fill"))
        if fill:
            glColor4ub(*fill)
            glBegin(GL_QUADS)
            for point in ((x1, y1), (x2, y1), (x2, y2), (x1, y2)):
                self._vertex(*point)
            glEnd()
        outline = self._color(options.get("outline"))
        if outline:
            glColor4ub(*outline)
            glLineWidth(max(1, float(options.get("width", 1))))
            glBegin(GL_LINE_LOOP)
            for point in ((x1, y1), (x2, y1), (x2, y2), (x1, y2)):
                self._vertex(*point)
            glEnd()

    def _draw_oval(self, coordinates, options):
        x1, y1, x2, y2 = coordinates
        center_x, center_y = (x1 + x2) / 2, (y1 + y2) / 2
        radius_x, radius_y = abs(x2 - x1) / 2, abs(y2 - y1) / 2
        segments = max(16, min(96, round(max(radius_x, radius_y))))
        fill = self._color(options.get("fill"))
        if fill:
            glColor4ub(*fill)
            glBegin(GL_POLYGON)
            for step in range(segments):
                angle = step * 2 * math.pi / segments
                self._vertex(
                    center_x + radius_x * math.cos(angle),
                    center_y + radius_y * math.sin(angle),
                )
            glEnd()
        outline = self._color(options.get("outline"))
        if outline:
            glColor4ub(*outline)
            glLineWidth(max(1, float(options.get("width", 1))))
            glBegin(GL_LINE_LOOP)
            for step in range(segments):
                angle = step * 2 * math.pi / segments
                self._vertex(
                    center_x + radius_x * math.cos(angle),
                    center_y + radius_y * math.sin(angle),
                )
            glEnd()

    def _draw_polygon(self, coordinates, options):
        points = list(zip(coordinates[::2], coordinates[1::2]))
        if len(points) < 3:
            return
        fill = self._color(options.get("fill"))
        if fill:
            glColor4ub(*fill)
            glBegin(GL_POLYGON)
            for point in points:
                self._vertex(*point)
            glEnd()
        outline = self._color(options.get("outline"))
        if outline:
            glColor4ub(*outline)
            glLineWidth(max(1, float(options.get("width", 1))))
            glBegin(GL_LINE_LOOP)
            for point in points:
                self._vertex(*point)
            glEnd()

    def _draw_text(self, coordinates, options):
        text = str(options.get("text", ""))
        if not text:
            return
        font_options = options.get("font", ("Arial", 10))
        font_name = font_options[0] if font_options else "Arial"
        font_size = int(font_options[1]) if len(font_options) > 1 else 10
        bold = len(font_options) > 2 and font_options[2] == "bold"
        font_key = (font_name, max(1, font_size), bold)
        font = self._get_font(font_name, font_size, bold)
        color = self._color(options.get("fill")) or (0, 0, 0, 255)
        texture_key = ("text", font_key, text, color[:3])
        entry = self.textures.get(texture_key)
        if entry is None:
            surface = font.render(text, True, color[:3])
            width, height = surface.get_size()
            pixel_factory = lambda: pygame.image.tostring(surface, "RGBA", True)
        else:
            width, height = entry[2], entry[3]
            pixel_factory = None
        anchor = options.get("anchor", "center")
        x, y = coordinates
        if anchor in ("nw", "w", "sw"):
            left = x
        elif anchor in ("ne", "e", "se"):
            left = x - width
        else:
            left = x - width / 2
        if anchor in ("nw", "n", "ne"):
            top = y
        elif anchor in ("sw", "s", "se"):
            top = y - height
        else:
            top = y - height / 2
        self._draw_texture(
            texture_key, None, width, height, left, top, pixel_factory,
        )

    def _get_font(self, font_name, font_size, bold):
        font_key = (font_name, max(1, int(font_size)), bold)
        font = self.fonts.get(font_key)
        if font is None:
            font = pygame.font.SysFont(font_name, font_key[1], bold=bold)
            self.fonts[font_key] = font
        return font

    def _draw_image(self, coordinates, options):
        image = options.get("image")
        source = getattr(
            image,
            "_pil_image",
            image if isinstance(image, Image.Image) else None,
        )
        if source is None:
            raise RuntimeError(
                "OpenGL rendering received an image without its PIL source pixels."
            )
        x, y = coordinates
        width, height = source.size
        texture_key = ("image", id(source))
        self._draw_texture(
            texture_key,
            source,
            width,
            height,
            x - width / 2,
            y - height / 2,
            lambda: (
                source if source.mode == "RGBA" else source.convert("RGBA")
            ).tobytes("raw", "RGBA", 0, -1),
        )

    def _draw_texture(
        self, texture_key, owner, width, height, left, top, pixel_factory,
    ):
        if width <= 0 or height <= 0:
            return
        entry = self.textures.get(texture_key)
        self.used_textures.add(texture_key)
        if entry is None:
            pixels = pixel_factory()
            texture = glGenTextures(1)
            glBindTexture(GL_TEXTURE_2D, texture)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
            glPixelStorei(GL_UNPACK_ALIGNMENT, 1)
            glTexImage2D(
                GL_TEXTURE_2D, 0, 4, width, height, 0,
                0x1908, GL_UNSIGNED_BYTE,
                pixels,
            )
            self.textures[texture_key] = (
                owner, texture, width, height, width * height * 4,
                self.frame_index,
            )
            self.texture_bytes += width * height * 4
        else:
            _owner, texture, cached_width, cached_height, size_bytes, _last_used = entry
            self.textures.move_to_end(texture_key)
            self.textures[texture_key] = (
                _owner, texture, cached_width, cached_height, size_bytes,
                self.frame_index,
            )
            glBindTexture(GL_TEXTURE_2D, texture)
        glEnable(GL_TEXTURE_2D)
        glColor4ub(255, 255, 255, 255)
        glBegin(GL_QUADS)
        glTexCoord2f(0, 0)
        self._vertex(left, top)
        glTexCoord2f(1, 0)
        self._vertex(left + width, top)
        glTexCoord2f(1, 1)
        self._vertex(left + width, top + height)
        glTexCoord2f(0, 1)
        self._vertex(left, top + height)
        glEnd()
        glBindTexture(GL_TEXTURE_2D, 0)
        glDisable(GL_TEXTURE_2D)

    def _trim_texture_cache(self):
        """Evict least-recently-used GPU textures when over the memory budget."""
        stale_keys = [
            key for key, entry in self.textures.items()
            if key not in self.used_textures
            and self.frame_index - entry[5] >= self.texture_idle_frames
        ]
        for key in stale_keys:
            _owner, texture, _width, _height, size_bytes, _last_used = (
                self.textures.pop(key)
            )
            glDeleteTextures([texture])
            self.texture_bytes -= size_bytes

        while self.texture_bytes > self.texture_budget_bytes and self.textures:
            _key, (_owner, texture, _width, _height, size_bytes, _last_used) = (
                self.textures.popitem(last=False)
            )
            glDeleteTextures([texture])
            self.texture_bytes -= size_bytes

    def close(self):
        """Release GPU textures and the Pygame display cleanly."""
        if self.textures:
            glDeleteTextures([
                texture for _owner, texture, _width, _height, _size, _last_used
                in self.textures.values()
            ])
            self.textures.clear()
            self.texture_bytes = 0
        self.items.clear()
        self.fonts.clear()
        self.frustum_culler.release()
        pygame.quit()
