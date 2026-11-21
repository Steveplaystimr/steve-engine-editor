"""Build the native renderer and Box2D helpers with CMake."""

import os
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def main():
    cmake = shutil.which("cmake")
    if not cmake:
        raise SystemExit(
            "CMake is required to build the native helpers. Install CMake and a C/C++ "
            "compiler, then rerun this script."
        )

    build_dir = ROOT / "build" / "native"
    subprocess.run(
        [cmake, "-S", str(ROOT), "-B", str(build_dir),
         "-DCMAKE_BUILD_TYPE=Release"],
        cwd=ROOT,
        check=True,
    )
    subprocess.run(
        [cmake, "--build", str(build_dir), "--config", "Release"],
        cwd=ROOT,
        check=True,
    )
    output = ROOT / "engine_editor" / "engine_physics_native"
    suffix = ".dll" if os.name == "nt" else ".dylib" if sys.platform == "darwin" else ".so"
    print(f"Built native renderer and Box2D helper: {output}{suffix}")


if __name__ == "__main__":
    main()
