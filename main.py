"""Start either the block editor or a game stored inside a packaged EXE."""

import sys
import tkinter as tk
from pathlib import Path
from tkinter import messagebox

from scratch_engine.editor import ScratchEditor
from scratch_engine.game import run_game
from scratch_engine.game_bundle import read_game_payload


def main():
    """Choose which app to launch, then start its Tkinter event loop."""
    if getattr(sys, "frozen", False):
        try:
            # Generated games store their blocks after the editor EXE itself.
            game_blocks = read_game_payload(Path(sys.executable))
        except (OSError, UnicodeDecodeError, ValueError) as error:
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror("Game could not start", str(error), parent=root)
            root.destroy()
            return
        if game_blocks is not None:
            if isinstance(game_blocks, dict):
                blocks = game_blocks.get("blocks")
                settings = game_blocks.get("settings", {})
                if not isinstance(blocks, list) or not isinstance(settings, dict):
                    raise ValueError("The embedded game data has an invalid format.")
                run_game(blocks, settings)
            else:
                run_game(game_blocks)
            return

    root = tk.Tk()
    # PyInstaller unpacks bundled files into _MEIPASS while the EXE is running.
    bundle_dir = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    window_icon = bundle_dir / "icon.ico"
    if window_icon.is_file():
        root.iconbitmap(str(window_icon))
    ScratchEditor(root)
    root.mainloop()


if __name__ == "__main__":
    # sys.exit returns the app's exit status to Windows when the window closes.
    main()
    sys.exit(0)
