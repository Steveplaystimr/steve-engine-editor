"""Small ctypes wrapper around the native Box2D world used by game sprites."""

import ctypes
import sys
from pathlib import Path


_BODY_TYPES = {"static": 0, "kinematic": 1, "dynamic": 2}


def _native_library_path():
    if sys.platform == "win32":
        filename = "engine_physics_native.dll"
    elif sys.platform == "darwin":
        filename = "engine_physics_native.dylib"
    else:
        filename = "engine_physics_native.so"

    package_dir = Path(__file__).resolve().parent
    bundle_dir = Path(getattr(sys, "_MEIPASS", package_dir))
    for path in (bundle_dir / "engine_editor" / filename, package_dir / filename):
        if path.is_file():
            return path
    return None


class Box2DWorld:
    """Own a native Box2D world and its stable body handles."""

    def __init__(self, gravity_x=0.0, gravity_y=-9.8):
        library_path = _native_library_path()
        if library_path is None:
            raise RuntimeError(
                "Physics blocks need the native Box2D helper. Run "
                "'python build_native.py' to build it."
            )
        self._library = ctypes.CDLL(str(library_path))
        self._configure_functions()
        self._handle = self._library.engine_physics_world_create(
            float(gravity_x), float(gravity_y),
        )
        if not self._handle:
            raise RuntimeError("Box2D could not create a physics world.")
        self._closed = False

    def _configure_functions(self):
        pointer = ctypes.c_void_p
        floating = ctypes.c_float
        integer = ctypes.c_int
        self._library.engine_physics_world_create.argtypes = (floating, floating)
        self._library.engine_physics_world_create.restype = pointer
        self._library.engine_physics_world_destroy.argtypes = (pointer,)
        self._library.engine_physics_world_destroy.restype = None
        self._library.engine_physics_world_set_gravity.argtypes = (
            pointer, floating, floating,
        )
        self._library.engine_physics_world_set_gravity.restype = None
        self._library.engine_physics_world_step.argtypes = (
            pointer, floating, integer,
        )
        self._library.engine_physics_world_step.restype = None
        self._library.engine_physics_body_create.argtypes = (
            pointer, integer, floating, floating, floating, floating, floating,
            floating, floating, floating,
        )
        self._library.engine_physics_body_create.restype = pointer
        self._library.engine_physics_body_get_state.argtypes = (
            pointer,
            ctypes.POINTER(floating), ctypes.POINTER(floating),
            ctypes.POINTER(floating), ctypes.POINTER(floating),
            ctypes.POINTER(floating),
        )
        self._library.engine_physics_body_get_state.restype = integer
        self._library.engine_physics_body_destroy.argtypes = (pointer, pointer)
        self._library.engine_physics_body_destroy.restype = None
        self._library.engine_physics_body_set_velocity.argtypes = (
            pointer, floating, floating,
        )
        self._library.engine_physics_body_set_velocity.restype = None
        self._library.engine_physics_body_set_type.argtypes = (pointer, integer)
        self._library.engine_physics_body_set_type.restype = None
        self._library.engine_physics_body_set_material.argtypes = (
            pointer, floating, floating,
        )
        self._library.engine_physics_body_set_material.restype = None
        self._library.engine_physics_body_apply_force.argtypes = (
            pointer, floating, floating,
        )
        self._library.engine_physics_body_apply_force.restype = None
        self._library.engine_physics_body_apply_impulse.argtypes = (
            pointer, floating, floating,
        )
        self._library.engine_physics_body_apply_impulse.restype = None
        self._library.engine_physics_body_create_revolute_joint.argtypes = (
            pointer, pointer, pointer, floating, floating,
        )
        self._library.engine_physics_body_create_revolute_joint.restype = (
            ctypes.c_uint64
        )

    @staticmethod
    def body_type_id(body_type):
        try:
            return _BODY_TYPES[body_type]
        except KeyError as error:
            raise ValueError(
                "Body type must be 'static', 'kinematic', or 'dynamic'."
            ) from error

    def create_box(
        self, body_type, x, y, angle, width, height, density=1.0,
        friction=0.3, restitution=0.0,
    ):
        if self._closed:
            raise RuntimeError("The Box2D world has already been closed.")
        if width <= 0 or height <= 0:
            raise ValueError("Physics box width and height must be positive.")
        if not 0 <= friction <= 1 or not 0 <= restitution <= 1:
            raise ValueError("Physics friction and bounce values must be 0 to 1.")
        body = self._library.engine_physics_body_create(
            self._handle,
            self.body_type_id(body_type),
            float(x), float(y), float(angle),
            float(width) / 2, float(height) / 2,
            float(density), float(friction), float(restitution),
        )
        if not body:
            raise RuntimeError("Box2D could not create a physics body.")
        return body

    def create_revolute_joint(self, body_a, body_b, anchor_x, anchor_y):
        joint = self._library.engine_physics_body_create_revolute_joint(
            self._handle, body_a, body_b, float(anchor_x), float(anchor_y),
        )
        if not joint:
            raise RuntimeError("Box2D could not create a revolute joint.")
        return joint

    def body_state(self, body):
        values = [ctypes.c_float() for _ in range(5)]
        result = self._library.engine_physics_body_get_state(
            body, *(ctypes.byref(value) for value in values),
        )
        if result != 1:
            raise RuntimeError("Box2D could not read a physics body.")
        return tuple(value.value for value in values)

    def destroy_body(self, body):
        if not self._closed:
            self._library.engine_physics_body_destroy(self._handle, body)

    def set_gravity(self, gravity_x, gravity_y):
        self._library.engine_physics_world_set_gravity(
            self._handle, float(gravity_x), float(gravity_y),
        )

    def step(self, time_step=1 / 60, sub_steps=4):
        if time_step <= 0 or sub_steps < 1:
            raise ValueError("Physics steps need positive time and substep values.")
        self._library.engine_physics_world_step(
            self._handle, float(time_step), int(sub_steps),
        )

    def set_velocity(self, body, velocity_x, velocity_y):
        self._library.engine_physics_body_set_velocity(
            body, float(velocity_x), float(velocity_y),
        )

    def set_body_type(self, body, body_type):
        self._library.engine_physics_body_set_type(
            body, self.body_type_id(body_type),
        )

    def set_material(self, body, friction, restitution):
        if not 0 <= friction <= 1 or not 0 <= restitution <= 1:
            raise ValueError("Physics friction and bounce values must be 0 to 1.")
        self._library.engine_physics_body_set_material(
            body, float(friction), float(restitution),
        )

    def apply_force(self, body, force_x, force_y):
        self._library.engine_physics_body_apply_force(
            body, float(force_x), float(force_y),
        )

    def apply_impulse(self, body, impulse_x, impulse_y):
        self._library.engine_physics_body_apply_impulse(
            body, float(impulse_x), float(impulse_y),
        )

    def close(self):
        if not self._closed:
            self._library.engine_physics_world_destroy(self._handle)
            self._handle = None
            self._closed = True
