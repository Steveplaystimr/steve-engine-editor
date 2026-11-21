"""Launch a game directly from a Steve Engine Editor .sse project."""

import sys
import tkinter as tk
import zipfile
from pathlib import Path
from tkinter import filedialog, messagebox

from engine_editor.game import run_game
from engine_editor.game_bundle import read_sse_game_data


def main(argv=None):
    """Choose an .sse project and run it without opening the editor."""
    arguments = sys.argv[1:] if argv is None else list(argv)
    root = tk.Tk()
    root.withdraw()

    if len(arguments) > 1:
        messagebox.showerror(
            "Steve Game Player",
            "Open one .sse project at a time.",
            parent=root,
        )
        root.destroy()
        return 2
    selected = arguments[0] if arguments else filedialog.askopenfilename(
        parent=root,
        title="Open a Steve Engine game",
        filetypes=[("Steve Engine Editor project", "*.sse")],
    )
    if not selected:
        root.destroy()
        return 0

    try:
        blocks, settings = read_sse_game_data(Path(selected))
    except (
        OSError, UnicodeDecodeError, ValueError, zipfile.BadZipFile,
    ) as error:
        messagebox.showerror(
            "Could not open game",
            str(error),
            parent=root,
        )
        root.destroy()
        return 1

    root.destroy()
    run_game(blocks, settings)
    return 0


if __name__ == "__main__":
    sys.exit(main())
