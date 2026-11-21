import unittest
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

from engine_editor.editor import SteveEngine


class FakeExplorer:
    def __init__(self):
        self.items = {}
        self.selected = []

    def get_children(self, parent=""):
        return [
            item_id for item_id, item in self.items.items()
            if item["parent"] == parent
        ]

    def delete(self, *_item_ids):
        self.items.clear()

    def insert(self, parent, _position, iid=None, **options):
        item_id = iid or f"item-{len(self.items)}"
        self.items[item_id] = {"parent": parent, **options}
        return item_id

    def exists(self, item_id):
        return item_id in self.items

    def selection_set(self, item_id):
        self.selected = [item_id]


class EditorUiTests(unittest.TestCase):
    def test_palette_label_wrap_tracks_available_width(self):
        class FakeButton:
            def __init__(self):
                self.options = {}

            def configure(self, **options):
                self.options.update(options)

        class FakeCanvas:
            def __init__(self):
                self.options = {}

            def winfo_width(self):
                return 196

            def itemconfigure(self, _item, **options):
                self.options.update(options)

        button = FakeButton()
        canvas = FakeCanvas()
        editor = SteveEngine.__new__(SteveEngine)
        editor.palette_canvas = canvas
        editor.palette_window = "palette"
        editor.palette = SimpleNamespace(winfo_children=lambda: [button])

        self.assertEqual(editor.palette_text_wraplength(), 160)
        with patch("engine_editor.editor.tk.Button", FakeButton):
            editor.resize_palette_content(SimpleNamespace(width=196))

        self.assertEqual(canvas.options, {"width": 196})
        self.assertEqual(button.options, {"wraplength": 160})

    def test_workspace_blocks_snap_edge_to_edge_and_add_connected(self):
        editor = SteveEngine.__new__(SteveEngine)
        editor.blocks = [{"kind": "move", "workspace_x": 24, "workspace_y": 30}]
        editor.create_palette_block = lambda kind: {"kind": kind}
        editor.draw_workspace = lambda: None
        editor.status = SimpleNamespace(set=lambda _message: None)
        editor.script_canvas = SimpleNamespace(yview_moveto=lambda _position: None)

        self.assertEqual(editor.workspace_block_bottom(editor.blocks[0], 0), 81)
        self.assertEqual(editor.find_bottom_snap_position(24, 81), (24, 81))

        editor.add_block("move")
        self.assertEqual(editor.blocks[-1]["workspace_y"], 81)

    def test_explorer_rebuilds_sprite_media_hierarchy_and_selection(self):
        editor = SteveEngine.__new__(SteveEngine)
        editor.explorer = FakeExplorer()
        editor.explorer_item_map = {}
        editor.ui_icons = {"image.png": "image-icon", "audio.png": "audio-icon"}
        editor.sprites = [
            {"name": "Hero", "costumes": [{"name": "Blue"}]},
            {"name": "Enemy", "costumes": []},
        ]
        editor.active_sprite_index = 0
        editor.backdrops = [{"name": "Forest"}]
        editor.sounds = [{"name": "Jump"}]

        editor.refresh_explorer()

        self.assertEqual(editor.explorer.items["explorer-sprites"]["text"], "Sprites")
        self.assertEqual(
            editor.explorer.items["explorer-sprite-0"]["text"], "Hero",
        )
        self.assertEqual(
            editor.explorer.items["explorer-backdrop-0"]["text"], "Forest",
        )
        self.assertEqual(
            editor.explorer.items["explorer-sound-0"]["text"], "Jump",
        )
        self.assertEqual(editor.explorer.selected, ["explorer-sprite-0"])
        self.assertEqual(
            editor.explorer_item_map["explorer-backdrop-0"], ("backdrop", 0),
        )
        self.assertFalse(editor._refreshing_explorer)

    def test_workspace_block_art_is_tinted_and_scaled(self):
        editor = SteveEngine.__new__(SteveEngine)
        editor.block_art = {
            "block.png": Image.new("RGBA", (16, 12), (255, 0, 0, 255)),
        }
        editor.block_art_refs = {}

        with patch(
            "engine_editor.editor.ImageTk.PhotoImage",
            side_effect=lambda image: image.copy(),
        ):
            art = editor.workspace_block_art("move", "#4c97ff", 120, 24)

        self.assertEqual(art.size, (120, 24))
        self.assertEqual(art.getpixel((60, 12)), (76, 151, 255, 255))


if __name__ == "__main__":
    unittest.main()
