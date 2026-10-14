"""Draw game stages through an OpenGL window backed by the GPU."""

from __future__ import annotations

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


class OpenGLStage:
    """Canvas-like drawing surface that renders game items using OpenGL."""

    def __init__(self, width, height, title, key_callback, close_callback):
        pygame.init()
        self.width = width
        self.height = height
        self.title = title
        self.key_callback = key_callback
        self.close_callback = close_callback
        self.items = []
        self.textures = {}
        self.used_textures = set()
        self.fullscreen = False
        self.windowed_size = (width, height)
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
            self.render()
        except (pygame.error, GLError) as error:
            raise RuntimeError(f"OpenGL game rendering failed: {error}") from error

    def render(self):
        """Render the queued scene using the active OpenGL context."""
        self.used_textures.clear()
        glClearColor(1.0, 1.0, 1.0, 1.0)
        glClear(GL_COLOR_BUFFER_BIT)
        glMatrixMode(GL_MODELVIEW)
        glLoadIdentity()
        glEnable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        glDisable(GL_TEXTURE_2D)
        for kind, coordinates, options in self.items:
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
        stale_textures = [
            texture for key, texture in self.textures.items()
            if key not in self.used_textures
        ]
        if stale_textures:
            glDeleteTextures(stale_textures)
            self.textures = {
                key: texture for key, texture in self.textures.items()
                if key in self.used_textures
            }
        pygame.display.flip()

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
                import math

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
                import math

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
        font = pygame.font.SysFont(font_name, max(1, font_size), bold=bold)
        color = self._color(options.get("fill")) or (0, 0, 0, 255)
        surface = font.render(text, True, color[:3])
        anchor = options.get("anchor", "center")
        x, y = coordinates
        if anchor in ("nw", "w", "sw"):
            left = x
        elif anchor in ("ne", "e", "se"):
            left = x - surface.get_width()
        else:
            left = x - surface.get_width() / 2
        if anchor in ("nw", "n", "ne"):
            top = y
        elif anchor in ("sw", "s", "se"):
            top = y - surface.get_height()
        else:
            top = y - surface.get_height() / 2
        self._draw_surface(surface, left, top)

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
        surface = pygame.image.fromstring(
            source.convert("RGBA").tobytes(), source.size, "RGBA",
        )
        x, y = coordinates
        self._draw_surface(
            surface,
            x - surface.get_width() / 2,
            y - surface.get_height() / 2,
        )

    def _draw_surface(self, surface, left, top):
        width, height = surface.get_size()
        if width <= 0 or height <= 0:
            return
        pixels = pygame.image.tostring(surface, "RGBA", False)
        texture_key = (width, height, pixels)
        self.used_textures.add(texture_key)
        texture = self.textures.get(texture_key)
        if texture is None:
            texture = glGenTextures(1)
            self.textures[texture_key] = texture
            glBindTexture(GL_TEXTURE_2D, texture)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
            glPixelStorei(GL_UNPACK_ALIGNMENT, 1)
            glTexImage2D(
                GL_TEXTURE_2D, 0, 4, width, height, 0,
                0x1908, GL_UNSIGNED_BYTE,
                pygame.image.tostring(surface, "RGBA", True),
            )
        else:
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

    def close(self):
        """Release GPU textures and the Pygame display cleanly."""
        if self.textures:
            glDeleteTextures(list(self.textures.values()))
            self.textures.clear()
        pygame.quit()
