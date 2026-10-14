# Python block mods

Steve Scratch Engine can load trusted Python block mods from the `mods` folder unlike minecraft that doesnt have it using like .jar lol.
In the source project, that folder is next to `sse_modding_tools.py`. For the
packaged editor, put it next to `steve-scratch-engine.exe`.

1. Enable **Enable Python Mods** in Settings.
2. Choose **Modded Blocks** in the category list and click **Open mods folder**.
3. Add or edit a `.py` file, then click **Reload mods**.
4. Click or drag a mod block into the workspace like any other block.

Each mod file must define `register(api)` and register at least one block. The
filename (without `.py`) becomes the mod ID; keep it unchanged after using its
blocks in a project. Block IDs and input names use letters, numbers, and underscores. Input types are
`text`, `number`, and `boolean`. The callback receives a `ModContext`, followed
by each declared input as a keyword argument
you have this exmaple of the python mod:
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
  Position uses Scratch-style coordinates, where positive `y` points upward.
- `context.variables`: the current mutable project variable dictionary.
- `context.get_variable(name, default=None)` and
  `context.set_variable(name, value)`.
- `context.move(steps)` and `context.turn(degrees)`.

The starter `mods/example_mod.py` demonstrates variable and sprite blocks. Mods
are ordinary Python code, not sandboxed scripts: enabling one can run arbitrary
code with the user's permissions. Only enable mods from sources you trust.

When a game EXE is built, the loaded mod source files are embedded with the
game, so the EXE does not need a separate `mods` folder. A `.sse` project stores
block data but not third-party mod files; share the required `.py` mod files
alongside a project so it can be edited on another computer.

## Installer build

The Windows installer uses a PyInstaller onedir build, so the editor EXE,
Python runtime DLLs, and support files stay together in an application folder.
The Inno Setup wizard packages that folder with the `mods` folder and this
guide into one compressed installer.

From the project root, build the application folder and then the installer:

```powershell
python -m PyInstaller --noconfirm --clean steve-scratch-engine.spec
ISCC.exe installer.iss
```

The installer is written to `dist/Steve-Scratch-Engine-Setup.exe`. It installs
per-user under `%LOCALAPPDATA%\Programs\Steve Scratch Engine`, keeping the
installed `mods` folder writable without administrator access.

Games built from the installed onedir editor are placed under its `games`
folder. Each generated game folder includes the matching `_internal` runtime
files next to its EXE.
