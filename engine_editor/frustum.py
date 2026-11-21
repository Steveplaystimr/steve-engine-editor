"""Batched 2D frustum checks with an optional native C++ implementation."""

import ctypes
import sys
from pathlib import Path


def _native_library_path():
    if sys.platform == "win32":
        filenames = ("engine_physics_native.dll", "engine_native.dll")
    elif sys.platform == "darwin":
        filenames = ("engine_physics_native.dylib", "libengine_native.dylib")
    else:
        filenames = ("engine_physics_native.so", "libengine_native.so")

    package_dir = Path(__file__).resolve().parent
    bundle_dir = Path(getattr(sys, "_MEIPASS", package_dir))
    for filename in filenames:
        for path in (bundle_dir / "engine_editor" / filename, package_dir / filename):
            if path.is_file():
                return path
    return None


def _load_native_culler():
    library_path = _native_library_path()
    if library_path is None:
        return None
    try:
        library = ctypes.CDLL(str(library_path))
        function = library.engine_cull_rectangles
        float_pointer = ctypes.POINTER(ctypes.c_float)
        byte_pointer = ctypes.POINTER(ctypes.c_uint8)
        function.argtypes = (
            float_pointer,
            ctypes.c_size_t,
            ctypes.c_float,
            ctypes.c_float,
            ctypes.c_float,
            ctypes.c_float,
            byte_pointer,
        )
        function.restype = ctypes.c_int
        return library, function
    except (AttributeError, OSError):
        return None


_NATIVE_CULLER = _load_native_culler()


class FrustumCuller:
    """Reuse native input/output buffers while culling a stage's scene items."""

    def __init__(self):
        self._capacity = 0
        self._bounds = None
        self._visible = None

    def cull(self, rectangles, viewport_width, viewport_height):
        """Return visibility for screen-space AABBs against a 2D viewport."""
        count = len(rectangles)
        if count == 0:
            return []

        if _NATIVE_CULLER is not None:
            self._ensure_capacity(count)
            offset = 0
            for rectangle in rectangles:
                for value in rectangle:
                    self._bounds[offset] = float(value)
                    offset += 1
            _library, function = _NATIVE_CULLER
            result = function(
                self._bounds,
                count,
                0.0,
                0.0,
                float(viewport_width),
                float(viewport_height),
                self._visible,
            )
            if result == 1:
                return [bool(self._visible[index]) for index in range(count)]

        return [
            right >= 0
            and bottom >= 0
            and left <= viewport_width
            and top <= viewport_height
            for left, top, right, bottom in rectangles
        ]

    def release(self):
        """Drop reusable native buffers when a game or stage closes."""
        self._bounds = None
        self._visible = None
        self._capacity = 0

    def _ensure_capacity(self, count):
        if count <= self._capacity:
            return
        self._capacity = max(count, self._capacity * 2, 64)
        self._bounds = (ctypes.c_float * (4 * self._capacity))()
        self._visible = (ctypes.c_uint8 * self._capacity)()
