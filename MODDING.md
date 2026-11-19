# Python block mods

Steve Engine Editor can load trusted Python block mods from the `mods` folder.
In the source project, that folder is next to `sse_modding_tools.py`. For the
packaged editor, put it next to `engine-editor.exe`.

1. Enable **Enable Python Mods** in Settings.
2. Choose **Modded Blocks** in the category list and click **Open mods folder**.
3. Add or edit a `.py` file, then click **Reload mods**.
4. Select its named category (such as **Physics**) and click or drag a block
   into the workspace like any other block.

Each mod file must define `register(api)` and register at least one block. The
filename (without `.py`) becomes the mod ID; keep it unchanged after using its
blocks in a project. Block IDs and input names use letters, numbers, and
underscores. Input types are `text`, `number`, and `boolean`. The callback
receives a `ModContext`, followed by each declared input as a keyword argument.
Blocks may pass `category="Physics"` to `@api.block(...)` to add a separate
palette category. Blocks without a category stay under **Modded Blocks**.
Call `api.category(name, label=..., color="#rrggbb", icon=...)` before
registering blocks to customize a category's palette button. Mod blocks may
also pass `color="#rrggbb"` and a short text `icon` to `@api.block(...)` to
customize their own palette buttons. The `mods/test_category.py` file is a
small example that exercises category labels, colors, and icons.
Here is an example Python mod:
```python
def register(api):
    @api.block(
        "add_score",
        "Add {amount} points",
        inputs={"amount": ("number", 1)},
    )
    def add_score(context, amount):
        context.set_variable(
            "score",
            context.get_variable("score", 0) + amount,
        )
```

The context exposes:

- `context.sprite`: mutable `x`, `y`, `direction`, `size`, and `visible` values.
  Physics mods also have persistent per-sprite `velocity_x`, `velocity_y`, and
  `gravity` values. Sprite positions use the editor's coordinate system, where
  positive `y` points upward.
- `context.variables`: the current mutable project variable dictionary.
- `context.get_variable(name, default=None)` and
  `context.set_variable(name, value)`.
- `context.move(steps)` and `context.turn(degrees)`.

The starter `mods/example_mod.py` demonstrates variable and sprite blocks. Mods
are ordinary Python code, not sandboxed scripts: enabling one can run arbitrary
code with the user's permissions. Only enable mods from sources you trust.

When a game EXE is built, the loaded mod source files are embedded with the
game, so the EXE does not need a separate `mods` folder. A `.sse` project stores
compressed project data but not third-party mod files; older uncompressed
`.sse` projects are still supported. Share the required `.py` mod files
alongside a project so it can be edited on another computer.

## Touch blocks and OpenGL ES

OpenGL and OpenGL ES game rendering support Pygame finger-down, finger-motion,
and finger-up events. The Events palette contains **when screen touched** and
**when screen touch released**. The Sensing palette contains `touching screen?`,
touch X/Y position reporters, and the active touch count. Use their expression
forms (`@touching_screen()`, `@touch_x()`, `@touch_y()`, and `@touch_count()`)
inside existing value fields. X/Y report the most recently received finger
position, and the count includes currently pressed fingers. The Logic palette
contains **if this sprite is touched**, which checks active fingers against the
current sprite's visible bounds.

Set the System **rendering** block to `opengl_es` to request an OpenGL ES 2
context; the normal `opengl` mode continues to use desktop OpenGL. The ES
renderer requires a platform/driver that supplies an ES 2 context. The current
Tkinter launcher and Windows EXE build are not an Android app or package.

## Box2D physics

Import a sprite costume, then click **Physics hitboxes** in the sprite
properties. Drag rectangles over image parts to create physics bodies. Choose a
body type for each part; choose a hinge parent and click **Set hinge pivot** to
connect parts with a revolute joint. The selected image regions are drawn as
independent parts, so a static torso can stay in place while a hinged dynamic
hand moves.

The hitbox editor lets you set friction and bounce (restitution) from 0 to 1
for each part. The **Physics** palette provides gravity, body-type, force,
impulse, velocity, and material blocks. Physics vector values use Box2D units
(meters, meters per second, or SI force/impulse); stage coordinates use 50
pixels per meter, with positive Y pointing up. Static hitboxes can be used for
floors and walls.

The native helper fetches the official Box2D 3.2.0 source on its first build.
Building requires CMake, a C/C++ compiler, and network access on that first
run:

```powershell
python build_native.py
```

The native Box2D simulation uses a fixed 1/60-second step with four substeps.
The existing C++ frustum culler is still used to avoid rendering off-screen
sprites.

## Runtime components

The editor uses Tkinter, Pygame, PyOpenGL, and Pillow. The optional native
frustum and physics helpers are C++ code compiled into native libraries.
OpenGL ES 2 is available as a game-rendering option for compatible runtimes.
The current editor and game launcher still use Tkinter, and the installer
builds Windows applications; this option does not package an Android app.

## Installer build

The Windows installer uses a PyInstaller onedir build, so the editor EXE,
Python runtime DLLs, and support files stay together in an application folder.
The Inno Setup wizard packages that folder with the `mods` folder and this
guide into one compressed installer.

From the project root, build the application folder and then the installer:

```powershell
python build_native.py
python -m PyInstaller --noconfirm --clean engine-editor.spec
python -m PyInstaller --noconfirm --clean game-player.spec
ISCC.exe installer.iss
```

The installer includes **Steve Game Player**, which opens and runs `.sse`
projects without launching the editor. Game EXE export is available only when
the editor is running with Windows administrator access.

`build_native.py` builds the C++ frustum-culling and Box2D helpers. The editor
still runs without them and uses its Python culling fallback; physics projects
require the native Box2D helper. Source-built game EXEs include native helpers
when present; packaged-editor game builds use Steve Game Player's bundled
runtime and copy the matching files with the game.

The installer is written to `dist/Steve-Engine-Editor-Setup.exe`. It installs
under `%ProgramFiles%\Steve Engine Editor` and requests administrator access.

Games built from the installed onedir editor are placed under its `games`
folder. Each generated game folder includes the matching `_internal` runtime
files next to its EXE.

# 
