import io
import os
import unittest
import wave
from unittest.mock import Mock, patch

from engine_editor.editor import SteveEngine
from engine_editor.game_bundle import append_game_payload, read_game_payload
from engine_editor.sound import play_project_sound, stop_sounds


def make_wav_bytes():
    """Create a short valid WAV payload without external test assets."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(22050)
        output.writeframes(b"\x00\x00" * 2205)
    return buffer.getvalue()


class AssetTests(unittest.TestCase):
    def setUp(self):
        self.previous_audio_driver = os.environ.get("SDL_AUDIODRIVER")
        os.environ["SDL_AUDIODRIVER"] = "dummy"

    def tearDown(self):
        stop_sounds()
        try:
            import pygame

            pygame.mixer.quit()
        except ImportError:
            pass
        if self.previous_audio_driver is None:
            os.environ.pop("SDL_AUDIODRIVER", None)
        else:
            os.environ["SDL_AUDIODRIVER"] = self.previous_audio_driver

    def test_imported_wav_sound_plays_from_project_asset(self):
        import base64

        asset = {
            "name": "Jump",
            "format": "wav",
            "audio_data": base64.b64encode(make_wav_bytes()).decode("ascii"),
        }
        self.assertIsNone(play_project_sound("jump", [asset]))
        stop_sounds()

    def test_sprite_deletion_keeps_at_least_one_sprite(self):
        editor = SteveEngine.__new__(SteveEngine)
        editor.running = False
        editor.editing_custom_block_id = None
        editor.sprites = [{"name": "Only sprite"}]
        editor.active_sprite_index = 0
        editor.root = Mock()
        with patch("engine_editor.editor.messagebox.showinfo") as showinfo:
            editor.delete_sprite()
        showinfo.assert_called_once()
        self.assertEqual(len(editor.sprites), 1)

    def test_backdrop_deletion_repairs_all_sprite_indices(self):
        editor = SteveEngine.__new__(SteveEngine)
        editor.running = False
        editor.editing_custom_block_id = None
        editor.backdrops = [
            {"name": "One"}, {"name": "Two"}, {"name": "Three"},
        ]
        editor.backdrop_index = 1
        editor.sprites = [
            {"name": "A", "backdrop_index": 0, "blocks": []},
            {"name": "B", "backdrop_index": 1, "blocks": []},
            {"name": "C", "backdrop_index": 2, "blocks": []},
        ]
        editor.active_sprite_index = 1
        editor.blocks = []
        editor.root = Mock()
        editor.save_active_sprite = Mock()
        editor.load_sprite_project_data = Mock()
        editor.update_asset_status = Mock()
        editor.draw_stage = Mock()
        editor.status = Mock()
        with patch(
            "engine_editor.editor.messagebox.askyesno", return_value=True,
        ):
            editor.delete_backdrop()
        self.assertEqual([item["name"] for item in editor.backdrops], ["One", "Three"])
        self.assertEqual(
            [sprite["backdrop_index"] for sprite in editor.sprites],
            [0, 1, 1],
        )
        self.assertEqual(editor.backdrop_index, 1)

    def test_game_payload_copies_frozen_onedir_runtime(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as folder:
            root = Path(folder)
            editor_exe = root / "editor.exe"
            editor_exe.write_bytes(b"editor executable")
            runtime_dir = root / "_internal"
            runtime_dir.mkdir()
            (runtime_dir / "python313.dll").write_bytes(b"runtime dll")
            game_exe = root / "games" / "sample" / "sample.exe"

            append_game_payload(
                editor_exe, game_exe, [], {"window_title": "Sample"},
                runtime_directory=runtime_dir,
            )

            self.assertEqual(
                read_game_payload(game_exe),
                {
                    "version": 1,
                    "blocks": [],
                    "settings": {"window_title": "Sample"},
                },
            )
            copied_runtime = game_exe.parent / "_internal" / "python313.dll"
            self.assertEqual(copied_runtime.read_bytes(), b"runtime dll")


if __name__ == "__main__":
    unittest.main()
