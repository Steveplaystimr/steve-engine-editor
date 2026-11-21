import unittest

from engine_editor.blocks import block_label, create_block, expand_script


class PhysicsBlockTests(unittest.TestCase):
    def test_physics_blocks_have_editable_defaults_and_execute_as_actions(self):
        gravity = create_block("physics_gravity")
        body_type = create_block("physics_body_type")
        force = create_block("physics_force")
        impulse = create_block("physics_impulse")
        velocity = create_block("physics_velocity")
        material = create_block("physics_material")

        self.assertEqual(gravity["y"], -9.8)
        self.assertEqual(body_type["body_type"], "dynamic")
        self.assertEqual(force["part"], "Body")
        self.assertEqual(impulse["part"], "Body")
        self.assertEqual(velocity["part"], "Body")
        self.assertEqual(material["friction"], 0.3)
        self.assertEqual(material["restitution"], 0.0)
        self.assertEqual(
            block_label(material),
            "set friction 0.3 bounce 0.0 for Body",
        )
        self.assertEqual(
            block_label(gravity),
            "set physics gravity x: 0.0 y: -9.8",
        )
        self.assertEqual(
            [action["kind"] for action in expand_script(
                [gravity, body_type, force, impulse, velocity, material],
            )],
            [
                "physics_gravity",
                "physics_body_type",
                "physics_force",
                "physics_impulse",
                "physics_velocity",
                "physics_material",
            ],
        )


if __name__ == "__main__":
    unittest.main()
