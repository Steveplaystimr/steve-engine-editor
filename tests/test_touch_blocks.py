import unittest
from types import SimpleNamespace

from engine_editor.blocks import (
    block_label,
    create_block,
    expand_script,
    parse_reporter_expression,
    split_event_scripts,
    validate_control_flow,
)
from engine_editor.game import GamePlayer


class TouchBlockTests(unittest.TestCase):
    def test_touch_events_split_into_separate_scripts(self):
        scripts = split_event_scripts([
            create_block("touch_start"),
            create_block("if_touched"),
            create_block("move"),
            create_block("end_if"),
            create_block("touch_end"),
            create_block("touch_count"),
        ])
        self.assertEqual(
            [script["event"] for script in scripts],
            ["touch_start", "touch_end"],
        )
        self.assertEqual(
            [action["kind"] for action in expand_script(scripts[0]["blocks"])],
            ["if_touched", "move", "end_if"],
        )
        self.assertEqual(
            [action["kind"] for action in expand_script(scripts[1]["blocks"])],
            [],
        )

    def test_if_touched_supports_else_and_matches_end_if(self):
        actions = expand_script([
            create_block("if_touched"),
            create_block("move"),
            create_block("else"),
            create_block("turn"),
            create_block("end_if"),
        ])
        jumps = validate_control_flow(actions)
        self.assertEqual(jumps[0], 4)
        self.assertEqual(jumps[(0, "else")], 2)
        self.assertEqual(block_label(create_block("if_touched")), "if this sprite is touched")

    def test_touch_reporter_expressions_are_parsed(self):
        self.assertEqual(parse_reporter_expression("@touch_x()"), ("touch_x", ()))
        self.assertEqual(
            parse_reporter_expression("@touch_count()"),
            ("touch_count", ()),
        )
        self.assertEqual(
            parse_reporter_expression("@touching_screen()"),
            ("touching_screen", ()),
        )

    def test_opengl_es_is_a_supported_game_renderer_choice(self):
        rendering = create_block("rendering")
        rendering["mode"] = "opengl_es"
        self.assertEqual(
            block_label(rendering),
            "set game rendering to OpenGL ES 2",
        )

    def test_sprite_touch_check_uses_active_fingers_and_sprite_visibility(self):
        player = SimpleNamespace(
            sprite_visible=True,
            sprite_x=200,
            sprite_y=205,
            sprite_size=100,
            sprite_costumes=[],
            touch_points={1: (505, 370)},
            stage=SimpleNamespace(winfo_width=lambda: 960, winfo_height=lambda: 720),
        )
        self.assertTrue(GamePlayer.current_sprite_touched(player))
        player.touch_points = {1: (700, 500)}
        self.assertFalse(GamePlayer.current_sprite_touched(player))
        player.sprite_visible = False
        player.touch_points = {1: (480, 360)}
        self.assertFalse(GamePlayer.current_sprite_touched(player))

    def test_touch_reporters_read_latest_finger_and_multitouch_count(self):
        stage = SimpleNamespace(
            winfo_width=lambda: 960,
            winfo_height=lambda: 720,
            pointer_position=lambda: (0, 0),
        )
        player = SimpleNamespace(
            opengl_stage=stage,
            stage=stage,
            touch_points={1: (480, 360), 2: (600, 400)},
            last_touch_position=(600, 400),
            sprite_x=200,
            sprite_y=205,
            sprite_size=100,
            sprite_costumes=[],
            sprite_costume_index=0,
            backdrops=[],
            game_sprites=[],
            active_game_sprite_index=0,
        )
        self.assertEqual(
            GamePlayer.sensing_reporter(player, "@touch_count()"),
            2,
        )
        self.assertAlmostEqual(
            GamePlayer.sensing_reporter(player, "@touch_x()"),
            320,
        )
        self.assertAlmostEqual(
            GamePlayer.sensing_reporter(player, "@touch_y()"),
            165,
        )
        self.assertTrue(
            GamePlayer.sensing_reporter(player, "@touching_screen()"),
        )

    def test_pygame_touch_callbacks_track_fingers_and_trigger_events(self):
        stage = SimpleNamespace(
            winfo_width=lambda: 960,
            winfo_height=lambda: 720,
        )
        player = SimpleNamespace(
            opengl_stage=stage,
            stage=stage,
            touch_points={},
            last_touch_position=(0, 0),
            events=[],
            run_script=lambda **kwargs: player.events.append(kwargs),
        )
        GamePlayer.on_opengl_touch(player, "touch_start", 1, 0.5, 0.5)
        GamePlayer.on_opengl_touch(player, "motion", 2, 0.75, 0.25)
        GamePlayer.on_opengl_touch(player, "touch_end", 1, 0.5, 0.5)
        self.assertEqual(player.touch_points, {2: (720.0, 180.0)})
        self.assertEqual(
            player.events,
            [{"event_name": "touch_start"}, {"event_name": "touch_end"}],
        )


if __name__ == "__main__":
    unittest.main()
