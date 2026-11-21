"""Example mod category for testing palette labels, colors, and icons."""


def register(api):
    api.category(
        "Test Category",
        label="Palette Test",
        color="#3b82f6",
        icon="T",
    )

    @api.block(
        "test_palette",
        "Test block",
        category="Test Category",
        color="#2563eb",
        icon=">",
    )
    def test_palette(context):
        context.set_variable("palette_test", True)
