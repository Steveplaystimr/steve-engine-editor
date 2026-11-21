"""Example mod: a block that changes a variable and a block that moves a sprite."""


def register(api):
    @api.block(
        "add_health",
        "Add {amount} to health",
        inputs={"amount": ("number", 10)},
    )
    def add_health(context, amount):
        health = context.get_variable("health", 0)
        context.set_variable("health", health + amount)

    @api.block(
        "dash",
        "Dash {steps} steps",
        inputs={"steps": ("number", 40)},
    )
    def dash(context, steps):
        context.move(steps)
