"""Render game stages with the OpenGL ES 2 programmable pipeline."""

from __future__ import annotations

import array
import ctypes
import ctypes.util
import math
import sys
from collections import OrderedDict
from pathlib import Path

import pygame
from PIL import Image, ImageColor

from .frustum import FrustumCuller
from .opengl_stage import OpenGLStage


_VERTEX_SHADER = """
attribute vec2 a_position;
attribute vec2 a_uv;
uniform vec2 u_viewport;
varying vec2 v_uv;
void main() {
    vec2 clip = vec2(
        (a_position.x / u_viewport.x) * 2.0 - 1.0,
        1.0 - (a_position.y / u_viewport.y) * 2.0
    );
    gl_Position = vec4(clip, 0.0, 1.0);
    v_uv = a_uv;
}
"""

_FRAGMENT_SHADER = """
precision mediump float;
uniform vec4 u_color;
uniform sampler2D u_texture;
uniform int u_textured;
varying vec2 v_uv;
void main() {
    gl_FragColor = u_textured == 1
        ? texture2D(u_texture, v_uv) * u_color
        : u_color;
}
"""


class _GLES2API:
    """Resolve GLES2 functions through SDL's current context.
    these numbers are from the opengl es 2.0 spec and are used to avoid importing the entire OpenGL ES header files."""

    GL_VERTEX_SHADER = 0x8B31
    GL_FRAGMENT_SHADER = 0x8B30
    GL_COMPILE_STATUS = 0x8B81
    GL_LINK_STATUS = 0x8B82
    GL_INFO_LOG_LENGTH = 0x8B84
    GL_FLOAT = 0x1406
    GL_FALSE = 0
    GL_TRUE = 1
    GL_TEXTURE0 = 0x84C0
    GL_TEXTURE_2D = 0x0DE1
    GL_TEXTURE_MIN_FILTER = 0x2801
    GL_TEXTURE_MAG_FILTER = 0x2800
    GL_TEXTURE_WRAP_S = 0x2802
    GL_TEXTURE_WRAP_T = 0x2803
    GL_LINEAR = 0x2601
    GL_CLAMP_TO_EDGE = 0x812F
    GL_RGBA = 0x1908
    GL_UNSIGNED_BYTE = 0x1401
    GL_UNPACK_ALIGNMENT = 0x0CF5
    GL_TRIANGLES = 0x0004
    GL_TRIANGLE_FAN = 0x0006
    GL_LINE_LOOP = 0x0002
    GL_COLOR_BUFFER_BIT = 0x4000
    GL_BLEND = 0x0BE2
    GL_SRC_ALPHA = 0x0302
    GL_ONE_MINUS_SRC_ALPHA = 0x0303

    def __init__(self):
        if sys.platform == "win32":
            candidates = [Path(pygame.__file__).with_name("SDL2.dll")]
        else:
            candidates = []
            for name in ("SDL2", "SDL2-2.0"):
                discovered = ctypes.util.find_library(name)
                if discovered:
                    candidates.append(Path(discovered))
            pygame_libs = Path(pygame.__file__).parent.parent / "pygame.libs"
            candidates.extend(sorted(pygame_libs.glob("*SDL2*")))
        library = None
        load_errors = []
        for candidate in candidates:
            try:
                library = ctypes.CDLL(str(candidate))
                break
            except OSError as error:
                load_errors.append(str(error)) 
        if library is None:  
            try:
                library = ctypes.CDLL(None)
            except OSError as error:
                load_errors.append(str(error))
        if library is None:
            raise RuntimeError(
                "Could not load the SDL library for OpenGL ES function lookup: "
                + "; ".join(load_errors)
            )
        try:
            get_proc_address = library.SDL_GL_GetProcAddress
        except AttributeError as error:
            raise RuntimeError(
                "The Pygame SDL runtime does not expose SDL_GL_GetProcAddress."
            ) from error
        get_proc_address.argtypes = (ctypes.c_char_p,)
        get_proc_address.restype = ctypes.c_void_p
        self._get_proc_address = get_proc_address
        self._uint = ctypes.c_uint
        self._int = ctypes.c_int
        self._sizei = ctypes.c_int
        self._float = ctypes.c_float
        self._void = ctypes.c_void_p
        self._functions = {}
        self._bind_functions()

    def _bind(self, name, result, *arguments):
        address = self._get_proc_address(name.encode("ascii"))
        if not address:
            raise RuntimeError(
                f"The current OpenGL ES context does not provide {name}."
            )
        function_type_factory = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)
        function_type = function_type_factory(result, *arguments)
        function = function_type(address)
        self._functions[name] = function
        return function

    def _bind_functions(self):
        uint, integer, sizei, float_type, void = (
            self._uint, self._int, self._sizei, self._float, self._void,
        )
        function_specs = {
            "glCreateShader": (uint, (uint,)),
            "glCompileShader": (None, (uint,)),
            "glDeleteShader": (None, (uint,)),
            "glCreateProgram": (uint, ()),
            "glAttachShader": (None, (uint, uint)),
            "glLinkProgram": (None, (uint,)),
            "glDeleteProgram": (None, (uint,)),
            "glUseProgram": (None, (uint,)),
            "glViewport": (None, (integer, integer, sizei, sizei)),
            "glClearColor": (None, (float_type, float_type, float_type, float_type)),
            "glClear": (None, (uint,)),
            "glEnable": (None, (uint,)),
            "glBlendFunc": (None, (uint, uint)),
            "glUniform2f": (None, (integer, float_type, float_type)),
            "glUniform4f": (
                None, (integer, float_type, float_type, float_type, float_type),
            ),
            "glUniform1i": (None, (integer, integer)),
            "glActiveTexture": (None, (uint,)),
            "glBindTexture": (None, (uint, uint)),
            "glEnableVertexAttribArray": (None, (uint,)),
            "glDisableVertexAttribArray": (None, (uint,)),
            "glVertexAttribPointer": (
                None, (uint, integer, uint, ctypes.c_ubyte, sizei, void),
            ),
            "glDrawArrays": (None, (uint, integer, sizei)),
            "glTexParameteri": (None, (uint, uint, integer)),
            "glPixelStorei": (None, (uint, integer)),
        }
        for name, (result, arguments) in function_specs.items():
            setattr(self, name, self._bind(name, result, *arguments))
        self._bind("glShaderSource", None, uint, sizei,
                   ctypes.POINTER(ctypes.c_char_p), ctypes.POINTER(integer))
        self._bind(
            "glGetShaderiv", None, uint, uint, ctypes.POINTER(integer),
        )
        self._bind(
            "glGetProgramiv", None, uint, uint, ctypes.POINTER(integer),
        )
        self._bind(
            "glGetShaderInfoLog", None, uint, sizei,
            ctypes.POINTER(sizei), ctypes.POINTER(ctypes.c_char),
        )
        self._bind(
            "glGetProgramInfoLog", None, uint, sizei,
            ctypes.POINTER(sizei), ctypes.POINTER(ctypes.c_char),
        )
        self._bind(
            "glBindAttribLocation", None, uint, uint, ctypes.c_char_p,
        )
        self._bind(
            "glGetUniformLocation", integer, uint, ctypes.c_char_p,
        )
        self._bind("glGenTextures", None, sizei, ctypes.POINTER(uint))
        self._bind("glDeleteTextures", None, sizei, ctypes.POINTER(uint))
        self._bind(
            "glTexImage2D", None, uint, integer, integer, sizei, sizei,
            integer, uint, uint, void,
        )

    def glShaderSource(self, shader, source):
        encoded = source.encode("utf-8")
        source_pointer = ctypes.c_char_p(encoded)
        self._functions["glShaderSource"](
            shader, 1, ctypes.byref(source_pointer), None,
        )

    def _get_info(self, function_name, object_id):
        get_parameter = self.glGetShaderiv if "Shader" in function_name else self.glGetProgramiv
        length = self._int()
        get_parameter(object_id, self.GL_INFO_LOG_LENGTH, ctypes.byref(length))
        if length.value <= 1:
            return ""
        buffer = ctypes.create_string_buffer(length.value)
        written = self._sizei()
        self._functions[function_name](
            object_id, length.value, ctypes.byref(written), buffer,
        )
        return buffer.value.decode("utf-8", errors="replace")

    def glGetShaderiv(self, shader, parameter):
        result = self._int()
        self._functions["glGetShaderiv"](shader, parameter, ctypes.byref(result))
        return result.value

    def glGetProgramiv(self, program, parameter):
        result = self._int()
        self._functions["glGetProgramiv"](program, parameter, ctypes.byref(result))
        return result.value

    def glGetShaderInfoLog(self, shader):
        return self._get_info("glGetShaderInfoLog", shader)

    def glGetProgramInfoLog(self, program):
        return self._get_info("glGetProgramInfoLog", program)

    def glBindAttribLocation(self, program, index, name):
        self._functions["glBindAttribLocation"](program, index, name.encode("ascii"))

    def glGetUniformLocation(self, program, name):
        return self._functions["glGetUniformLocation"](program, name.encode("ascii"))

    def glGenTextures(self, count):
        texture = self._uint()
        self._functions["glGenTextures"](count, ctypes.byref(texture))
        return texture.value

    def glDeleteTextures(self, textures):
        texture_values = (self._uint * len(textures))(*textures)
        self._functions["glDeleteTextures"](len(textures), texture_values)

    def glTexImage2D(
        self, target, level, internal_format, width, height, border,
        image_format, data_type, pixels,
    ):
        buffer = ctypes.create_string_buffer(pixels)
        self._functions["glTexImage2D"](
            target, level, internal_format, width, height, border,
            image_format, data_type, buffer,
        )

    def glVertexAttribPointer(
        self, index, size, data_type, normalized, stride, pointer,
    ):
        pointer = ctypes.cast(pointer, self._void)
        self._functions["glVertexAttribPointer"](
            index, size, data_type, normalized, stride, pointer,
        )


class OpenGLESStage:
    """Canvas-like Pygame surface rendered with OpenGL ES 2."""

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
            pygame.display.gl_set_attribute(
                pygame.GL_CONTEXT_PROFILE_MASK, pygame.GL_CONTEXT_PROFILE_ES,
            )
            pygame.display.gl_set_attribute(pygame.GL_CONTEXT_MAJOR_VERSION, 2)
            pygame.display.gl_set_attribute(pygame.GL_CONTEXT_MINOR_VERSION, 0)
            self.window = pygame.display.set_mode(
                (width, height),
                pygame.OPENGL | pygame.DOUBLEBUF | pygame.RESIZABLE,
            )
            self.gl = _GLES2API()
            pygame.display.set_caption(title)
            pygame.font.init()
            self._create_program()
            self._configure_projection()
        except (OSError, pygame.error, RuntimeError) as error:
            pygame.quit()
            raise RuntimeError(
                f"Could not initialize the OpenGL ES 2 game window: {error}"
            ) from error

    def _create_program(self):
        gl = self.gl
        vertex_shader = self._compile_shader(gl.GL_VERTEX_SHADER, _VERTEX_SHADER)
        fragment_shader = self._compile_shader(gl.GL_FRAGMENT_SHADER, _FRAGMENT_SHADER)
        self.program = gl.glCreateProgram()
        gl.glAttachShader(self.program, vertex_shader)
        gl.glAttachShader(self.program, fragment_shader)
        gl.glBindAttribLocation(self.program, 0, "a_position")
        gl.glBindAttribLocation(self.program, 1, "a_uv")
        gl.glLinkProgram(self.program)
        if not gl.glGetProgramiv(self.program, gl.GL_LINK_STATUS):
            message = gl.glGetProgramInfoLog(self.program)
            raise RuntimeError(f"Could not link the OpenGL ES renderer: {message}")
        gl.glDeleteShader(vertex_shader)
        gl.glDeleteShader(fragment_shader)
        self.viewport_location = gl.glGetUniformLocation(self.program, "u_viewport")
        self.color_location = gl.glGetUniformLocation(self.program, "u_color")
        self.texture_location = gl.glGetUniformLocation(self.program, "u_texture")
        self.textured_location = gl.glGetUniformLocation(self.program, "u_textured")
        gl.glUseProgram(self.program)
        gl.glUniform1i(self.texture_location, 0)

    def _compile_shader(self, shader_type, source):
        gl = self.gl
        shader = gl.glCreateShader(shader_type)
        gl.glShaderSource(shader, source)
        gl.glCompileShader(shader)
        if not gl.glGetShaderiv(shader, gl.GL_COMPILE_STATUS):
            message = gl.glGetShaderInfoLog(shader)
            gl.glDeleteShader(shader)
            raise RuntimeError(f"Could not compile the OpenGL ES renderer: {message}")
        return shader

    def pack(self, **_kwargs):
        """Match the Tk widget API; Pygame owns its own native window."""

    def bind(self, _event, _callback):
        """Accept Tk-style bindings; Pygame events are polled by poll_events."""

    def winfo_width(self):
        """Return the live native window width."""
        return self.width

    def winfo_height(self):
        """Return the live native window height."""
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

    def _reset_context_resources(self):
        self.textures.clear()
        self.texture_bytes = 0
        self._create_program()
        self._configure_projection()

    def set_fullscreen(self, fullscreen):
        """Switch between fullscreen and windowed native window modes."""
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
            self._reset_context_resources()
        except (OSError, pygame.error, RuntimeError) as error:
            raise RuntimeError(
                f"Could not change the OpenGL ES window mode: {error}"
            ) from error

    def poll_events(self):
        """Process native window, keyboard, and multitouch events."""
        try:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    self.close_callback()
                elif event.type == pygame.VIDEORESIZE and not self.fullscreen:
                    self.width = max(360, event.w)
                    self.height = max(360, event.h)
                    self.windowed_size = (self.width, self.height)
                    self._configure_projection()
                elif event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_F11:
                        self.key_callback("F11")
                    elif event.key == pygame.K_ESCAPE:
                        self.key_callback("Escape")
                    else:
                        self.key_callback(pygame.key.name(event.key))
                elif self.touch_callback is not None and event.type == pygame.FINGERDOWN:
                    self.touch_callback(
                        "touch_start", event.finger_id, event.x, event.y,
                    )
                elif self.touch_callback is not None and event.type == pygame.FINGERMOTION:
                    self.touch_callback("motion", event.finger_id, event.x, event.y)
                elif self.touch_callback is not None and event.type == pygame.FINGERUP:
                    self.touch_callback("touch_end", event.finger_id, event.x, event.y)
            self.render()
        except (OSError, pygame.error, RuntimeError) as error:
            raise RuntimeError(f"OpenGL ES rendering failed: {error}") from error

    def _configure_projection(self):
        gl = self.gl
        gl.glViewport(0, 0, self.width, self.height)

    @staticmethod
    def _color(value, fallback=None):
        if value in (None, ""):
            return fallback
        red, green, blue = ImageColor.getrgb(value)[:3]
        return red / 255, green / 255, blue / 255, 1.0

    def _draw_vertices(self, points, color, mode, uvs=None, texture=None):
        if not points or color is None:
            return
        gl = self.gl
        texture_active = texture is not None
        uv_values = uvs or [(0.0, 0.0)] * len(points)
        flattened = array.array(
            "f",
            (
                component
                for point, uv in zip(points, uv_values)
                for component in (*point, *uv)
            ),
        )
        vertices = (ctypes.c_float * len(flattened)).from_buffer(flattened)
        gl.glUseProgram(self.program)
        gl.glUniform2f(self.viewport_location, self.width, self.height)
        gl.glUniform4f(self.color_location, *color)
        gl.glUniform1i(self.textured_location, int(texture_active))
        gl.glActiveTexture(gl.GL_TEXTURE0)
        gl.glBindTexture(gl.GL_TEXTURE_2D, texture or 0)
        gl.glEnableVertexAttribArray(0)
        gl.glEnableVertexAttribArray(1)
        gl.glVertexAttribPointer(0, 2, gl.GL_FLOAT, gl.GL_FALSE, 16, vertices)
        gl.glVertexAttribPointer(
            1, 2, gl.GL_FLOAT, gl.GL_FALSE, 16,
            ctypes.cast(ctypes.byref(vertices, 8), ctypes.c_void_p),
        )
        gl.glDrawArrays(mode, 0, len(points))
        gl.glDisableVertexAttribArray(0)
        gl.glDisableVertexAttribArray(1)
        gl.glBindTexture(gl.GL_TEXTURE_2D, 0)

    def render(self):
        """Draw the queued scene through the OpenGL ES 2 shader pipeline."""
        gl = self.gl
        self.frame_index += 1
        self.used_textures.clear()
        gl.glClearColor(1.0, 1.0, 1.0, 1.0)
        gl.glClear(gl.GL_COLOR_BUFFER_BIT)
        gl.glEnable(gl.GL_BLEND)
        gl.glBlendFunc(gl.GL_SRC_ALPHA, gl.GL_ONE_MINUS_SRC_ALPHA)
        visible_by_index = [True] * len(self.items)
        cullable_indices = []
        bounds = []
        for index, item in enumerate(self.items):
            item_bounds = OpenGLStage._item_bounds(self, item)
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

    def _draw_rectangle(self, coordinates, options):
        gl = self.gl
        x1, y1, x2, y2 = coordinates
        points = ((x1, y1), (x2, y1), (x2, y2), (x1, y2))
        fill = self._color(options.get("fill"))
        if fill:
            triangles = (points[0], points[1], points[2], points[0], points[2], points[3])
            self._draw_vertices(triangles, fill, gl.GL_TRIANGLES)
        outline = self._color(options.get("outline"))
        if outline:
            self._draw_vertices(points, outline, gl.GL_LINE_LOOP)

    def _draw_oval(self, coordinates, options):
        gl = self.gl
        x1, y1, x2, y2 = coordinates
        center_x, center_y = (x1 + x2) / 2, (y1 + y2) / 2
        radius_x, radius_y = abs(x2 - x1) / 2, abs(y2 - y1) / 2
        segments = max(16, min(96, round(max(radius_x, radius_y))))
        points = [
            (
                center_x + radius_x * math.cos(step * 2 * math.pi / segments),
                center_y + radius_y * math.sin(step * 2 * math.pi / segments),
            )
            for step in range(segments)
        ]
        fill = self._color(options.get("fill"))
        if fill:
            self._draw_vertices(
                [(center_x, center_y), *points, points[0]], fill, gl.GL_TRIANGLE_FAN,
            )
        outline = self._color(options.get("outline"))
        if outline:
            self._draw_vertices(points, outline, gl.GL_LINE_LOOP)

    def _draw_polygon(self, coordinates, options):
        gl = self.gl
        points = list(zip(coordinates[::2], coordinates[1::2]))
        if len(points) < 3:
            return
        fill = self._color(options.get("fill"))
        if fill:
            self._draw_vertices(
                [points[0], *points[1:], points[1]], fill, gl.GL_TRIANGLE_FAN,
            )
        outline = self._color(options.get("outline"))
        if outline:
            self._draw_vertices(points, outline, gl.GL_LINE_LOOP)

    def _get_font(self, font_name, font_size, bold):
        key = (font_name, max(1, int(font_size)), bold)
        if key not in self.fonts:
            self.fonts[key] = pygame.font.SysFont(font_name, key[1], bold=bold)
        return self.fonts[key]

    def _draw_text(self, coordinates, options):
        text = str(options.get("text", ""))
        if not text:
            return
        font_options = options.get("font", ("Arial", 10))
        font_name = font_options[0] if font_options else "Arial"
        font_size = int(font_options[1]) if len(font_options) > 1 else 10
        bold = len(font_options) > 2 and font_options[2] == "bold"
        font_key = (font_name, max(1, font_size), bold)
        color = self._color(options.get("fill")) or (0, 0, 0, 1)
        pixel_color = tuple(round(channel * 255) for channel in color[:3])
        texture_key = ("text", font_key, text, pixel_color)
        entry = self.textures.get(texture_key)
        if entry is None:
            surface = self._get_font(font_name, font_size, bold).render(
                text, True, pixel_color,
            )
            width, height = surface.get_size()
            pixel_factory = lambda: pygame.image.tostring(surface, "RGBA", True)
        else:
            width, height = entry[1], entry[2]
            pixel_factory = None
        x, y = coordinates
        anchor = options.get("anchor", "center")
        left = x if anchor in ("nw", "w", "sw") else (
            x - width if anchor in ("ne", "e", "se") else x - width / 2
        )
        top = y if anchor in ("nw", "n", "ne") else (
            y - height if anchor in ("sw", "s", "se") else y - height / 2
        )
        self._draw_texture(
            texture_key, width, height, left, top, pixel_factory,
        )

    def _draw_image(self, coordinates, options):
        image = options.get("image")
        source = getattr(
            image, "_pil_image", image if isinstance(image, Image.Image) else None,
        )
        if source is None:
            raise RuntimeError(
                "OpenGL ES rendering received an image without its PIL source pixels."
            )
        x, y = coordinates
        width, height = source.size
        texture_key = ("image", id(source))
        self._draw_texture(
            texture_key, width, height, x - width / 2, y - height / 2,
            lambda: (
                source if source.mode == "RGBA" else source.convert("RGBA")
            ).tobytes("raw", "RGBA", 0, -1),
        )

    def _draw_texture(
        self, texture_key, width, height, left, top, pixel_factory,
    ):
        gl = self.gl
        if width <= 0 or height <= 0:
            return
        entry = self.textures.get(texture_key)
        self.used_textures.add(texture_key)
        if entry is None:
            texture = gl.glGenTextures(1)
            gl.glBindTexture(gl.GL_TEXTURE_2D, texture)
            gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MIN_FILTER, gl.GL_LINEAR)
            gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MAG_FILTER, gl.GL_LINEAR)
            gl.glTexParameteri(
                gl.GL_TEXTURE_2D, gl.GL_TEXTURE_WRAP_S, gl.GL_CLAMP_TO_EDGE,
            )
            gl.glTexParameteri(
                gl.GL_TEXTURE_2D, gl.GL_TEXTURE_WRAP_T, gl.GL_CLAMP_TO_EDGE,
            )
            gl.glPixelStorei(gl.GL_UNPACK_ALIGNMENT, 1)
            gl.glTexImage2D(
                gl.GL_TEXTURE_2D, 0, gl.GL_RGBA, width, height, 0,
                gl.GL_RGBA, gl.GL_UNSIGNED_BYTE, pixel_factory(),
            )
            entry = (texture, width, height, width * height * 4, self.frame_index)
            self.textures[texture_key] = entry
            self.texture_bytes += entry[3]
        else:
            texture, cached_width, cached_height, size_bytes, _last_used = entry
            self.textures.move_to_end(texture_key)
            entry = (texture, cached_width, cached_height, size_bytes, self.frame_index)
            self.textures[texture_key] = entry
        points = (
            (left, top), (left + width, top), (left + width, top + height),
            (left, top), (left + width, top + height), (left, top + height),
        )
        uvs = ((0, 0), (1, 0), (1, 1), (0, 0), (1, 1), (0, 1))
        self._draw_vertices(
            points, (1.0, 1.0, 1.0, 1.0), gl.GL_TRIANGLES, uvs, entry[0],
        )

    def _trim_texture_cache(self):
        stale = [
            key for key, entry in self.textures.items()
            if key not in self.used_textures
            and self.frame_index - entry[4] >= self.texture_idle_frames
        ]
        gl = self.gl
        for key in stale:
            texture, _width, _height, size_bytes, _last_used = self.textures.pop(key)
            gl.glDeleteTextures([texture])
            self.texture_bytes -= size_bytes
        while self.texture_bytes > self.texture_budget_bytes and self.textures:
            _key, entry = self.textures.popitem(last=False)
            texture, _width, _height, size_bytes, _last_used = entry
            gl.glDeleteTextures([texture])
            self.texture_bytes -= size_bytes

    def close(self):
        """Release textures and shut down the native Pygame window."""
        gl = self.gl
        if self.textures:
            gl.glDeleteTextures([entry[0] for entry in self.textures.values()])
        if getattr(self, "program", None):
            gl.glDeleteProgram(self.program)
        self.textures.clear()
        self.texture_bytes = 0
        self.items.clear()
        self.fonts.clear()
        self.frustum_culler.release()
        pygame.quit()
