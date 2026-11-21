import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

from engine_editor.editor import PROJECT_FORMAT, SteveEngine


class ProjectCompressionTests(unittest.TestCase):
    def make_editor(self, project_path=None):
        editor = SteveEngine.__new__(SteveEngine)
        editor.root = Mock()
        editor.project_path = project_path
        editor.sprites = [{"blocks": []}]
        editor.active_sprite_index = 0
        editor.blocks = []
        editor.game_settings = {"width": 960, "height": 720}
        editor.custom_blocks = []
        editor.backdrops = []
        editor.sounds = []
        editor.category = "Events"
        editor.language = "en"
        editor.status = Mock()
        editor.save_active_sprite = Mock()
        editor.sprite_project_data = Mock(return_value={})
        editor.image_cache = Mock()
        editor.load_sprite_project_data = Mock()
        editor.finish_custom_edit_button = Mock()
        editor.script_workspace_title = Mock()
        editor.refresh_sprite_tabs = Mock()
        editor.sound_status = Mock()
        editor.show_category = Mock()
        editor.draw_workspace = Mock()
        editor.draw_stage = Mock()
        return editor

    @staticmethod
    def empty_project_data():
        return {
            "format": PROJECT_FORMAT,
            "version": 1,
            "blocks": [],
            "game_settings": {},
            "sprite": {},
            "backdrops": [],
            "custom_blocks": [],
        }

    def test_save_writes_compressed_sse_and_open_restores_it(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder, "compressed.sse")
            editor = self.make_editor(path)
            editor.game_settings = {"width": 1280, "height": 720}
            editor.sounds = [{
                "name": "Jump",
                "format": "wav",
                "audio_data": "YWJj",
            }]
            editor.sprites = [{"blocks": [{"kind": "move", "steps": 10}]}]
            editor.blocks = editor.sprites[0]["blocks"]

            editor.save_project()

            self.assertTrue(zipfile.is_zipfile(path))
            with zipfile.ZipFile(path) as archive:
                self.assertEqual(archive.namelist(), ["project.json"])
                self.assertEqual(archive.testzip(), None)
                self.assertEqual(
                    archive.getinfo("project.json").compress_type,
                    zipfile.ZIP_DEFLATED,
                )
                saved_data = json.loads(archive.read("project.json"))
            self.assertEqual(saved_data["game_settings"]["width"], 1280)
            self.assertEqual(saved_data["sounds"][0]["name"], "Jump")

            loaded_editor = self.make_editor()
            with patch(
                "engine_editor.editor.filedialog.askopenfilename",
                return_value=str(path),
            ):
                loaded_editor.open_project()

            self.assertEqual(loaded_editor.project_path, path)
            self.assertEqual(loaded_editor.game_settings["width"], 1280)
            self.assertEqual(loaded_editor.sounds[0]["name"], "Jump")
            self.assertEqual(loaded_editor.blocks, [{"kind": "move", "steps": 10}])

    def test_open_still_accepts_legacy_uncompressed_json(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder, "legacy.sse")
            path.write_text(
                json.dumps(self.empty_project_data()),
                encoding="utf-8",
            )
            editor = self.make_editor()

            with patch(
                "engine_editor.editor.filedialog.askopenfilename",
                return_value=str(path),
            ):
                editor.open_project()

            self.assertEqual(editor.project_path, path)
            editor.status.set.assert_called_once_with("Opened legacy.sse")


if __name__ == "__main__":
    unittest.main()
