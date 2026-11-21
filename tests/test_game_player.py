import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

from engine_editor.editor import SteveEngine
from engine_editor.game_bundle import read_sse_game_data


class GamePlayerTests(unittest.TestCase):
    def test_reads_active_sprite_and_game_resources_from_compressed_sse(self):
        project = {
            "format": "engine-editor-project",
            "version": 1,
            "blocks": [{"kind": "move", "steps": 5}],
            "game_settings": {"width": 1280, "height": 720},
            "sprites": [
                {"name": "First", "blocks": [{"kind": "turn", "degrees": 15}]},
                {"name": "Second", "blocks": [{"kind": "move", "steps": 25}]},
            ],
            "active_sprite_index": 1,
            "backdrops": [{"name": "Room", "image_data": ""}],
            "custom_blocks": [],
            "sounds": [],
            "mod_sources": {"sample": "source"},
        }
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder, "game.sse")
            with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("project.json", json.dumps(project))

            blocks, settings = read_sse_game_data(path)

        self.assertEqual(blocks, [{"kind": "move", "steps": 25}])
        self.assertEqual(settings["width"], 1280)
        self.assertEqual(settings["sprites"][1]["name"], "Second")
        self.assertEqual(settings["active_sprite_index"], 1)
        self.assertEqual(settings["backdrops"][0]["name"], "Room")
        self.assertEqual(settings["mod_sources"], {"sample": "source"})

    def test_reads_legacy_json_sse_with_single_sprite(self):
        project = {
            "format": "steve-engine-editor-project",
            "blocks": [{"kind": "flag"}],
            "sprite": {"name": "Legacy"},
        }
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder, "legacy.sse")
            path.write_text(json.dumps(project), encoding="utf-8")

            blocks, settings = read_sse_game_data(path)

        self.assertEqual(blocks, [{"kind": "flag"}])
        self.assertEqual(settings["sprite"]["name"], "Legacy")
        self.assertEqual(settings["sprites"][0]["blocks"], blocks)

    def test_rejects_invalid_selected_sprite(self):
        project = {
            "format": "engine-editor-project",
            "blocks": [],
            "sprites": [{"blocks": []}],
            "active_sprite_index": 4,
        }
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder, "invalid.sse")
            with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("project.json", json.dumps(project))

            with self.assertRaisesRegex(ValueError, "sprite selection"):
                read_sse_game_data(path)

    def test_export_is_blocked_without_admin_access(self):
        editor = SteveEngine.__new__(SteveEngine)
        editor.root = Mock()
        with (
            patch.object(SteveEngine, "can_export_game", return_value=False),
            patch("engine_editor.editor.messagebox.showinfo") as showinfo,
            patch("engine_editor.editor.tk.Toplevel") as top_level,
        ):
            editor.start_build()

        showinfo.assert_called_once()
        top_level.assert_not_called()

    def test_admin_export_check_is_windows_only(self):
        with (
            patch("engine_editor.editor.sys.platform", "win32"),
            patch.object(SteveEngine, "_is_administrator", return_value=True),
        ):
            self.assertTrue(SteveEngine.can_export_game())
        with (
            patch("engine_editor.editor.sys.platform", "win32"),
            patch.object(SteveEngine, "_is_administrator", return_value=False),
        ):
            self.assertFalse(SteveEngine.can_export_game())
        with (
            patch("engine_editor.editor.sys.platform", "linux"),
            patch.object(SteveEngine, "_is_administrator", return_value=True),
        ):
            self.assertFalse(SteveEngine.can_export_game())


if __name__ == "__main__":
    unittest.main()
