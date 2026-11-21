import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from engine_editor.blocks import (
    block_label,
    create_block,
    expand_custom_blocks,
    expand_script,
)
from engine_editor.editor import SteveEngine
from engine_editor.game import GamePlayer
from engine_editor.game_bundle import append_game_payload, read_game_payload
from sse_modding_tools import MOD_REGISTRY, ModContext, ModRegistry, mod_import_names


EXAMPLE_SOURCE = """
def register(api):
    @api.block(
        "add_score",
        "Add {amount} points",
        inputs={"amount": ("number", 3)},
    )
    def add_score(context, amount):
        context.set_variable("score", context.get_variable("score", 0) + amount)
        context.move(amount)
"""


class ModdingTests(unittest.TestCase):
    def setUp(self):
        MOD_REGISTRY.clear()

    def tearDown(self):
        MOD_REGISTRY.clear()

    def test_mod_block_metadata_and_callback(self):
        errors = MOD_REGISTRY.load_sources({"demo.py": EXAMPLE_SOURCE})
        self.assertEqual(errors, ())

        block = create_block("mod:demo:add_score")
        self.assertEqual(block["inputs"], {"amount": 3})
        self.assertEqual(block_label(block), "Add 3 points")
        self.assertEqual(expand_script(expand_custom_blocks([block], [])), [block])

        context = ModContext(
            {"x": 0, "y": 0, "direction": 90, "size": 100, "visible": True},
            {},
        )
        MOD_REGISTRY.execute(block, context)
        self.assertEqual(context.variables["score"], 3)
        self.assertAlmostEqual(context.sprite["x"], 3)

    def test_category_palette_metadata_and_block_overrides(self):
        source = """
def register(api):
    api.category("Tools", label="My Tools", color="#123ABC", icon="T")
    @api.block(
        "test",
        "Test block",
        category="Tools",
        color="#ABCDEF",
        icon=">",
    )
    def test(context):
        pass
"""
        errors = MOD_REGISTRY.load_sources({"palette.py": source})
        self.assertEqual(errors, ())
        block = create_block("mod:palette:test")
        self.assertIn("Tools", MOD_REGISTRY.categories)
        self.assertEqual(
            MOD_REGISTRY.category_style("Tools"),
            ("My Tools", "#123abc", "T"),
        )
        self.assertEqual(MOD_REGISTRY.get(block["kind"]).color, "#abcdef")
        self.assertEqual(MOD_REGISTRY.palette_label(block), "> Test block")

    def test_editor_and_game_adapters_apply_modded_sprite_state(self):
        MOD_REGISTRY.load_sources({"demo.py": EXAMPLE_SOURCE})
        block = create_block("mod:demo:add_score")
        editor = SimpleNamespace(
            sprite_x=200, sprite_y=205, sprite_angle=90, sprite_size=100,
            sprite_visible=True, variables={},
        )
        SteveEngine.execute_mod_block(editor, block)
        self.assertAlmostEqual(editor.sprite_x, 203)
        self.assertEqual(editor.variables["score"], 3)

        game = SimpleNamespace(
            sprite_x=200, sprite_y=205, sprite_angle=90, sprite_size=100,
            sprite_visible=True, variables={},
        )
        GamePlayer.execute_mod_block(game, block)
        self.assertAlmostEqual(game.sprite_x, 203)
        self.assertEqual(game.variables["score"], 3)

    def test_directory_reports_bad_mod_and_loads_valid_mod(self):
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, "demo.py").write_text(EXAMPLE_SOURCE, encoding="utf-8")
            Path(folder, "broken.py").write_text(
                'def register(api):\n    raise RuntimeError("bad mod")\n',
                encoding="utf-8",
            )
            errors = MOD_REGISTRY.load_directory(folder)

        self.assertEqual(len(errors), 1)
        self.assertIn("bad mod", errors[0])
        self.assertIn("mod:demo:add_score", MOD_REGISTRY.blocks)

    def test_mod_imports_are_reported_for_game_build(self):
        imports = mod_import_names({
            "demo.py": "import math\nfrom pathlib import Path\n"
        })
        self.assertEqual(imports, ("math", "pathlib"))

    def test_mod_context_rejects_invalid_state(self):
        MOD_REGISTRY.load_sources({"demo.py": EXAMPLE_SOURCE})
        block = create_block("mod:demo:add_score")
        block["inputs"]["amount"] = float("inf")
        with self.assertRaisesRegex(ValueError, "finite number"):
            MOD_REGISTRY.execute(
                block,
                ModContext(
                    {"x": 0, "y": 0, "direction": 90, "size": 100, "visible": True},
                    {},
                ),
            )

    def test_python_mod_setting_defaults_off(self):
        self.assertFalse(
            SteveEngine.normalize_app_settings({})["modded_blocks_enabled"]
        )

    def test_python_mod_sources_survive_game_exe_payload_round_trip(self):
        with tempfile.TemporaryDirectory() as folder:
            editor_exe = Path(folder, "editor.exe")
            game_exe = Path(folder, "game.exe")
            editor_exe.write_bytes(b"fake editor executable")
            settings = {"mod_sources": {"demo.py": EXAMPLE_SOURCE}}

            append_game_payload(editor_exe, game_exe, [], settings)
            game_data = read_game_payload(game_exe)

        self.assertIsInstance(game_data, dict)
        self.assertEqual(game_data["settings"]["mod_sources"], settings["mod_sources"])


if __name__ == "__main__":
    unittest.main()
