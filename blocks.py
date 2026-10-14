"""Define block types and translate them into actions the runtimes can execute."""

import ast
import base64
import io
import math
import operator
import re

from PIL import Image, ImageColor

# Each category maps a stored block name to the text shown in the palette.
BLOCKS = {
    "Events": [
        ("flag", "when the green run buttonclicked"),
        ("key", "when key pressed: space"),
    ],
    "Motion": [
        ("move", "move 10 steps"),
        ("turn", "turn 15 degrees"),
        ("turn_counterclockwise", "turn counterclockwise 15 degrees"),
        ("goto_random", "go to random position"),
        ("goto", "go to x: 0 y: 0"),
        ("glide_random", "glide 1 second to random position"),
        ("glide", "glide 1 second to x: 0 y: 0"),
        ("point_direction", "point in direction 90"),
        ("point_mouse", "point towards mouse-pointer"),
        ("change_x", "change x by 10"),
        ("set_x", "set x to 0"),
        ("change_y", "change y by 10"),
        ("set_y", "set y to 0"),
        ("bounce", "if on edge, bounce"),
        ("rotation_style", "set rotation style left-right"),
    ],
    "Sensing": [
        ("touching", "touching mouse-pointer?"),
        ("touching_color", "touching color #8000a0?"),
        ("color_touching", "color #00cdb4 is touching #ddd1ad?"),
        ("distance_to", "distance to mouse-pointer"),
    ],
    "Looks": [
        ("say", 'say "Hello!" for 2 seconds'),
        ("say_forever", 'say "Hello!"'),
        ("think", 'think "Hmm..." for 2 seconds'),
        ("think_forever", 'think "Hmm..."'),
        ("switch_costume", "switch costume to Costume1"),
        ("next_costume", "next costume"),
        ("switch_backdrop", "switch backdrop to Backdrop1"),
        ("next_backdrop", "next backdrop"),
        ("change_size", "change size by 10"),
        ("set_size", "set size to 100%"),
        ("change_effect", "change color effect by 25"),
        ("set_effect", "set color effect to 0"),
        ("clear_effects", "clear graphic effects"),
        ("show", "show"),
        ("hide", "hide"),
        ("layer_front", "go to front layer"),
        ("layer_back", "go to back layer"),
    ],
    "Output": [
        ("print", 'print("Hello, world!")'),
        ("input", 'input("What is your name?")'),
    ],
    "Sound": [
        ("play_sound", "play sound Meow until done"),
        ("start_sound", "start sound Meow"),
        ("stop_sounds", "stop all sounds"),
        ("change_pitch", "change pitch effect by 10"),
        ("set_pitch", "set pitch effect to 100"),
        ("clear_sound_effects", "clear sound effects"),
        ("change_volume", "change volume by -10"),
        ("set_volume", "set volume to 100%"),
        ("volume", "volume"),
    ],
    "Variables": [
        ("set_variable", "set variable value as a data type"),
        ("calculate", "store a calculation in a variable"),
    ],
    "Logic": [
        ("if", "if value equals another value"),
        ("else", "else"),
        ("end_if", "end if"),
        ("while", "while condition is true"),
        ("end_while", "end while"),
    ],
    "Drawing": [
        ("image", "show imported image"),
    ],
    "Control": [
        ("repeat", "repeat 5 times"),
        ("end_repeat", "end repeat"),
        ("wait", "wait 1 second"),
    ],
    "System": [
        ("memory_limit", "set app memory limit to 512 MB"),
        ("memory_result", "show app memory usage"),
        ("rendering", "set game rendering to software"),
    ],
    "Modded Blocks": [],
    "Defined Blocks": [],
    "Files": [
        ("read_file", "read text file into variable"),
        ("write_file", "write text to a file"),
    ],
}

# Palette colors are shared by the editor buttons and workspace blocks.
COLORS = {
    "Events": "#ffbf00",
    "Motion": "#4c97ff",
    "Sensing": "#5cb1d6",
    "Output": "#9966ff",
    "Looks": "#9966ff",
    "Sound": "#d65bc9",
    "Variables": "#a65ce6",
    "Logic": "#e36b5d",
    "Drawing": "#0fbd8c",
    "Control": "#ffab19",
    "System": "#607d8b",
    "Modded Blocks": "#8e44ad",
    "Files": "#a55b35",
    "Defined Blocks": "#ff6680",
}
# Limit expanded scripts so very large repeat blocks cannot exhaust memory.
MAX_ACTIONS = 5000
KNOWN_BLOCK_KINDS = {
    kind for category_blocks in BLOCKS.values()
    for kind, _label in category_blocks
} | {"custom_call"}
REPORTER_BLOCK_KINDS = {
    "touching", "touching_color", "color_touching", "distance_to",
}


def apply_color_effect(image, effect):
    """Shift an image's hue while leaving its transparency unchanged."""
    if not effect:
        return image.convert("RGBA")
    rgba = image.convert("RGBA")
    hue, saturation, value = rgba.convert("HSV").split()
    shift = round(effect * 255 / 200)
    hue = hue.point(lambda channel: (channel + shift) % 256)
    result = Image.merge("HSV", (hue, saturation, value)).convert("RGBA")
    result.putalpha(rgba.getchannel("A"))
    return result


def movement_delta(steps, direction):
    """Return the movement vector for steps along the sprite's direction."""
    angle = math.radians(direction)
    return steps * math.sin(angle), -steps * math.cos(angle)


def create_block(kind):
    """Make a new block with default fields for the requested block type."""
    # Keeping defaults in one place lets palette clicks and project loading agree.
    defaults = {
        "flag": {"kind": "flag"},
        "key": {"kind": "key", "key": "space"},
        "move": {"kind": "move", "steps": 10},
        "turn": {"kind": "turn", "degrees": 15},
        "turn_counterclockwise": {"kind": "turn_counterclockwise", "degrees": 15},
        "goto_random": {"kind": "goto_random"},
        "goto": {"kind": "goto", "x": 0, "y": 0},
        "glide_random": {"kind": "glide_random", "seconds": 1},
        "glide": {"kind": "glide", "seconds": 1, "x": 0, "y": 0},
        "point_direction": {"kind": "point_direction", "degrees": 90},
        "point_mouse": {"kind": "point_mouse"},
        "touching": {
            "kind": "touching",
            "target": "mouse-pointer",
        },
        "touching_color": {
            "kind": "touching_color",
            "color": "#8000a0",
        },
        "color_touching": {
            "kind": "color_touching",
            "color": "#00cdb4",
            "other_color": "#ddd1ad",
        },
        "distance_to": {
            "kind": "distance_to",
            "target": "mouse-pointer",
        },
        "change_x": {"kind": "change_x", "amount": 10},
        "set_x": {"kind": "set_x", "value": 0},
        "change_y": {"kind": "change_y", "amount": 10},
        "set_y": {"kind": "set_y", "value": 0},
        "bounce": {"kind": "bounce"},
        "rotation_style": {"kind": "rotation_style", "style": "left-right"},
        "say_forever": {"kind": "say_forever", "text": "Hello!"},
        "think": {"kind": "think", "text": "Hmm...", "seconds": 2},
        "think_forever": {"kind": "think_forever", "text": "Hmm..."},
        "switch_costume": {"kind": "switch_costume", "name": "Costume1"},
        "next_costume": {"kind": "next_costume"},
        "switch_backdrop": {"kind": "switch_backdrop", "name": "Backdrop1"},
        "next_backdrop": {"kind": "next_backdrop"},
        "change_size": {"kind": "change_size", "amount": 10},
        "set_size": {"kind": "set_size", "value": 100},
        "change_effect": {"kind": "change_effect", "effect": "color", "amount": 25},
        "set_effect": {"kind": "set_effect", "effect": "color", "value": 0},
        "clear_effects": {"kind": "clear_effects"},
        "show": {"kind": "show"},
        "hide": {"kind": "hide"},
        "layer_front": {"kind": "layer_front"},
        "layer_back": {"kind": "layer_back"},
        "play_sound": {"kind": "play_sound", "sound": "Meow"},
        "start_sound": {"kind": "start_sound", "sound": "Meow"},
        "stop_sounds": {"kind": "stop_sounds"},
        "change_pitch": {"kind": "change_pitch", "amount": 10},
        "set_pitch": {"kind": "set_pitch", "value": 100},
        "clear_sound_effects": {"kind": "clear_sound_effects"},
        "change_volume": {"kind": "change_volume", "amount": -10},
        "set_volume": {"kind": "set_volume", "value": 100},
        "volume": {"kind": "volume"},
        "print": {"kind": "print", "value": "Hello, world!"},
        "input": {
            "kind": "input",
            "name": "user_input",
            "prompt": "What is your name?",
        },
        "say": {"kind": "say", "text": "Hello!", "seconds": 2},
        "set_variable": {
            "kind": "set_variable",
            "name": "score",
            "data_type": "int",
            "value": "0",
        },
        "calculate": {
            "kind": "calculate",
            "name": "result",
            "left": "0",
            "operator": "+",
            "right": "0",
        },
        "if": {"kind": "if", "left": "score", "operator": "==", "right": "0"},
        "else": {"kind": "else"},
        "end_if": {"kind": "end_if"},
        "while": {"kind": "while", "left": "score", "operator": "<", "right": "10"},
        "end_while": {"kind": "end_while"},
        "image": {
            "kind": "image",
            "name": "Image",
            "image_data": "",
            "x": 0,
            "y": 0,
        },
        "repeat": {"kind": "repeat", "count": 5},
        "end_repeat": {"kind": "end_repeat"},
        "wait": {"kind": "wait", "seconds": 1},
        "memory_limit": {"kind": "memory_limit", "megabytes": 512},
        "memory_result": {"kind": "memory_result"},
        "rendering": {"kind": "rendering", "mode": "software"},
        "custom_call": {
            "kind": "custom_call",
            "definition_id": "",
            "name": "my block",
            "parts": [],
            "values": {},
            "label": "my block",
        },
        "read_file": {"kind": "read_file", "name": "file_data"},
        "write_file": {"kind": "write_file", "value": "Save data"},
    }
    try:
        return defaults[kind].copy()
    except KeyError as error:
        from sse_modding_tools import MOD_REGISTRY

        if MOD_REGISTRY.get(kind) is not None:
            return MOD_REGISTRY.create_block(kind)
        if isinstance(kind, str) and kind.startswith("mod:"):
            raise ValueError(
                f"Python mod block '{kind}' is unavailable. Enable Python Mods "
                "and install its .py file in the mods folder."
            ) from error
        raise ValueError(f"Unknown block kind: {kind}") from error


def block_label(block):
    """Turn a block's stored values into the readable label on screen."""
    kind = block["kind"]
    if kind == "custom_call":
        return block.get("label", block.get("name", "custom block"))
    from sse_modding_tools import MOD_REGISTRY

    if kind.startswith("mod:") and MOD_REGISTRY.get(kind) is None:
        return block.get("mod_label", f"Unavailable mod block: {kind}")
    if MOD_REGISTRY.get(kind) is not None:
        return MOD_REGISTRY.label(block)
    if kind == "flag":
        return "when green flag clicked"
    if kind == "key":
        return f"when key pressed: {block['key']}"
    if kind == "move":
        return f"move {block['steps']} steps"
    if kind == "turn":
        return f"turn {block['degrees']} degrees"
    if kind == "turn_counterclockwise":
        return f"turn counterclockwise {block['degrees']} degrees"
    if kind == "goto_random":
        return "go to random position"
    if kind == "goto":
        return f"go to x: {block['x']}  y: {block['y']}"
    if kind == "glide_random":
        return f"glide {block['seconds']} sec to random position"
    if kind == "glide":
        return f"glide {block['seconds']} sec to x: {block['x']} y: {block['y']}"
    if kind == "point_direction":
        return f"point in direction {block['degrees']}"
    if kind == "point_mouse":
        return "point towards mouse-pointer"
    if kind == "touching":
        return f"touching {block['target']}?"
    if kind == "touching_color":
        return f"touching color {block['color']}?"
    if kind == "color_touching":
        return f"color {block['color']} is touching {block['other_color']}?"
    if kind == "distance_to":
        return f"distance to {block['target']}"
    if kind == "change_x":
        return f"change x by {block['amount']}"
    if kind == "set_x":
        return f"set x to {block['value']}"
    if kind == "change_y":
        return f"change y by {block['amount']}"
    if kind == "set_y":
        return f"set y to {block['value']}"
    if kind == "bounce":
        return "if on edge, bounce"
    if kind == "rotation_style":
        return f"set rotation style {block['style']}"
    if kind in ("say_forever", "think_forever", "think"):
        text = block["text"]
        if kind == "say_forever":
            return f'say "{text}"'
        if kind == "think_forever":
            return f'think "{text}"'
        return f'think "{text}" for {block["seconds"]} seconds'
    if kind == "switch_costume":
        return f"switch costume to {block['name']}"
    if kind == "next_costume":
        return "next costume"
    if kind == "switch_backdrop":
        return f"switch backdrop to {block['name']}"
    if kind == "next_backdrop":
        return "next backdrop"
    if kind == "change_size":
        return f"change size by {block['amount']}"
    if kind == "set_size":
        return f"set size to {block['value']}%"
    if kind == "change_effect":
        return f"change {block['effect']} effect by {block['amount']}"
    if kind == "set_effect":
        return f"set {block['effect']} effect to {block['value']}"
    if kind == "clear_effects":
        return "clear graphic effects"
    if kind == "show":
        return "show"
    if kind == "hide":
        return "hide"
    if kind == "layer_front":
        return "go to front layer"
    if kind == "layer_back":
        return "go to back layer"
    if kind in ("play_sound", "start_sound"):
        action = "play sound" if kind == "play_sound" else "start sound"
        suffix = " until done" if kind == "play_sound" else ""
        return f"{action} {block['sound']}{suffix}"
    if kind == "stop_sounds":
        return "stop all sounds"
    if kind == "change_pitch":
        return f"change pitch effect by {block['amount']}"
    if kind == "set_pitch":
        return f"set pitch effect to {block['value']}"
    if kind == "clear_sound_effects":
        return "clear sound effects"
    if kind == "change_volume":
        return f"change volume by {block['amount']}"
    if kind == "set_volume":
        return f"set volume to {block['value']}%"
    if kind == "volume":
        return "volume (%)"
    if kind == "print":
        return f"print {block['value']!r}"
    if kind == "input":
        return f'{block["name"]} = input("{block["prompt"]}")'
    if kind == "say":
        return f'say "{block["text"]}" for {block["seconds"]} seconds'
    if kind == "set_variable":
        return f"{block['name']} = {block['value']} ({block['data_type']})"
    if kind == "calculate":
        return (
            f"{block['name']} = {block['left']} "
            f"{block['operator']} {block['right']}"
        )
    if kind in ("if", "while"):
        return (
            f"{'if' if kind == 'if' else 'while'} {block['left']} "
            f"{block['operator']} {block['right']}"
        )
    if kind == "else":
        return "else"
    if kind == "end_if":
        return "end if"
    if kind == "end_while":
        return "end while"
    if kind == "image":
        return f"show image: {block['name']}"
    if kind == "repeat":
        return f"repeat {block['count']} times"
    if kind == "end_repeat":
        return "end repeat"
    if kind == "wait":
        return f"wait {block['seconds']} second(s)"
    if kind == "memory_limit":
        return f"set app memory limit to {block['megabytes']} MB"
    if kind == "memory_result":
        return "show app memory usage"
    if kind == "rendering":
        mode = block["mode"]
        if mode not in ("software", "opengl"):
            raise ValueError(f"Unknown rendering mode: {mode}")
        renderer = "OpenGL (GPU)" if mode == "opengl" else "Software (CPU)"
        return f"set game rendering to {renderer}"
    if kind == "read_file":
        return f"read text file into {block['name']}"
    if kind == "write_file":
        return f"write {block['value']!r} to a text file"
    raise ValueError(f"Unknown block kind: {kind}")


def custom_block_label(definition, values=None):
    """Build the visible label for a custom block and its input slots."""
    values = values or {}
    parts = [definition.get("name", "custom block")]
    for part in definition.get("parts", []):
        if part.get("kind") == "label":
            parts.append(str(part.get("text", "")))
        elif part.get("kind") == "input":
            name = part.get("name", "input")
            value = values.get(name, "")
            parts.append(f"[{name}: {value}]" if value != "" else f"[{name}]")
    return " ".join(part for part in parts if part)


def is_known_block_kind(kind):
    """Check built-in and currently loaded mod block kinds."""
    if kind in KNOWN_BLOCK_KINDS:
        return True
    from sse_modding_tools import MOD_REGISTRY

    return MOD_REGISTRY.get(kind) is not None


def expand_custom_blocks(blocks, definitions, call_stack=()):
    """Inline custom procedure calls, substituting typed inputs into their bodies."""
    definition_map = {}
    if not isinstance(definitions, list):
        raise ValueError("Custom block definitions must be a list.")
    for definition in definitions:
        if not isinstance(definition, dict):
            raise ValueError("A custom block definition is malformed.")
        definition_id = definition.get("id")
        name = definition.get("name")
        inputs = definition.get("parts")
        body = definition.get("body")
        if (
            not isinstance(definition_id, str) or not definition_id
            or not isinstance(name, str) or not name.strip()
            or not isinstance(inputs, list) or not isinstance(body, list)
            or not isinstance(definition.get("warp", False), bool)
        ):
            raise ValueError("A custom block definition has invalid fields.")
        if definition_id in definition_map:
            raise ValueError(f"Duplicate custom block definition: {name}.")
        names = set()
        for part in inputs:
            if not isinstance(part, dict) or part.get("kind") not in ("input", "label"):
                raise ValueError(f"Custom block '{name}' has an invalid part.")
            if part["kind"] == "input":
                input_name = part.get("name")
                input_type = part.get("type")
                if (
                    not isinstance(input_name, str)
                    or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", input_name)
                    or input_name in names
                    or input_type not in ("text", "number", "boolean")
                ):
                    raise ValueError(f"Custom block '{name}' has an invalid input.")
                names.add(input_name)
            elif not isinstance(part.get("text"), str):
                raise ValueError(f"Custom block '{name}' has an invalid label.")
        if any(
            not isinstance(block, dict)
            or not is_known_block_kind(block.get("kind"))
            for block in body
        ):
            raise ValueError(f"Custom block '{name}' has an invalid script.")
        definition_map[definition_id] = definition

    input_pattern = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)")

    def coerce_input(value, input_type, name):
        if input_type == "text":
            return str(value)
        if input_type == "boolean":
            if isinstance(value, bool):
                return value
            normalized = str(value).strip().lower()
            if normalized in ("true", "yes", "1"):
                return True
            if normalized in ("false", "no", "0"):
                return False
            raise ValueError(f"Input '{name}' must be true or false.")
        if isinstance(value, bool):
            raise ValueError(f"Input '{name}' must be a number.")
        if isinstance(value, (int, float)):
            number = value
        else:
            try:
                number = ast.literal_eval(str(value).strip())
            except (SyntaxError, ValueError) as error:
                raise ValueError(f"Input '{name}' must be a number.") from error
        if (
            not isinstance(number, (int, float))
            or isinstance(number, bool)
            or (isinstance(number, float) and not math.isfinite(number))
        ):
            raise ValueError(f"Input '{name}' must be a finite number.")
        return number

    def substitute(value, parameters):
        if isinstance(value, dict):
            return {key: substitute(item, parameters) for key, item in value.items()}
        if isinstance(value, list):
            return [substitute(item, parameters) for item in value]
        if not isinstance(value, str):
            return value
        exact = input_pattern.fullmatch(value)
        if exact and exact.group(1) in parameters:
            return parameters[exact.group(1)]
        return input_pattern.sub(
            lambda match: str(parameters[match.group(1)])
            if match.group(1) in parameters else match.group(0),
            value,
        )

    def inline(source_blocks, active_stack, inherited_warp=False):
        actions = []
        for block in source_blocks:
            if not isinstance(block, dict):
                raise ValueError("A script contains a malformed block.")
            kind = block.get("kind")
            if not is_known_block_kind(kind):
                raise ValueError(f"Unknown block kind: {kind}")
            if kind != "custom_call":
                action = block.copy()
                if inherited_warp:
                    action["_run_without_refresh"] = True
                actions.append(action)
            else:
                definition_id = block.get("definition_id")
                definition = definition_map.get(definition_id)
                if definition is None:
                    raise ValueError(
                        f"Custom block '{block.get('name', 'unknown')}' no longer exists."
                    )
                if definition_id in active_stack or len(active_stack) >= 32:
                    raise ValueError(
                        f"Custom block '{definition['name']}' calls itself too deeply."
                    )
                values = block.get("values", {})
                if not isinstance(values, dict):
                    raise ValueError(f"Inputs for '{definition['name']}' are malformed.")
                parameters = {}
                for part in definition["parts"]:
                    if part["kind"] != "input":
                        continue
                    input_name = part["name"]
                    default = "false" if part["type"] == "boolean" else (
                        "0" if part["type"] == "number" else ""
                    )
                    parameters[input_name] = coerce_input(
                        values.get(input_name, default), part["type"], input_name,
                    )
                body = substitute(definition["body"], parameters)
                actions.extend(
                    inline(
                        body,
                        (*active_stack, definition_id),
                        inherited_warp or definition.get("warp", False),
                    )
                )
            if len(actions) > MAX_ACTIONS:
                raise ValueError(
                    f"Script is too large to run (limit: {MAX_ACTIONS} actions)."
                )
        return actions

    return inline(blocks, call_stack)


def parse_value(value, variables, reporter_resolver=None):
    """Resolve a literal or variable name without evaluating arbitrary code."""
    if not isinstance(value, str):
        return value
    text = value.strip()
    if text.startswith("@"):
        if reporter_resolver is None:
            raise ValueError("This value needs a live sensing reporter.")
        return reporter_resolver(text)
    if text.startswith("$"):
        name = text[1:]
        if name not in variables:
            raise ValueError(f"Variable '{name}' has not been set.")
        return variables[name]
    if text in variables:
        return variables[text]
    try:
        return ast.literal_eval(text)
    except (SyntaxError, ValueError):
        return value


def parse_reporter_expression(expression):
    """Parse one typed sensing reporter token used in a condition or calculation."""
    match = re.fullmatch(
        r"@(touching|touching_color|color_touching|distance_to)\((.*)\)",
        expression.strip(),
    )
    if match is None:
        raise ValueError(
            "Use @touching(target), @touching_color(#rrggbb), "
            "@color_touching(#rrggbb;#rrggbb), or @distance_to(target)."
        )
    kind, arguments_text = match.groups()
    separator = ";" if kind == "color_touching" else ","
    arguments = tuple(part.strip() for part in arguments_text.split(separator))
    expected = 2 if kind == "color_touching" else 1
    if len(arguments) != expected or any(not argument for argument in arguments):
        raise ValueError(f"Reporter '{kind}' needs {expected} argument(s).")
    return kind, arguments


def evaluate_spatial_reporter(
    kind, arguments, sprite_x, sprite_y, sprite_size, stage_width,
    stage_height, pointer_x, pointer_y, sprites, active_sprite_index,
    backdrop_data=None, actor_image_data=None,
):
    """Evaluate pointer, edge, and other-sprite reporters in stage coordinates."""
    target = arguments[0]
    if kind in ("touching_color", "color_touching"):
        target_x, target_y = sprite_x, sprite_y
        target_radius = 0
    elif target == "mouse-pointer":
        target_x, target_y = pointer_x, pointer_y
        target_radius = 0
    elif target == "edge" and kind == "touching":
        half_width = stage_width / 2
        half_height = stage_height / 2
        radius = 18 * sprite_size / 100
        return (
            sprite_x - radius <= 200 - half_width
            or sprite_x + radius >= 200 + half_width
            or sprite_y - radius <= 205 - half_height
            or sprite_y + radius >= 205 + half_height
        )
    else:
        match = next(
            (
                (index, sprite) for index, sprite in enumerate(sprites)
                if index != active_sprite_index
                and sprite.get("name", f"Sprite {index + 1}") == target
            ),
            None,
        )
        if match is None:
            raise ValueError(f"Sensing target '{target}' was not found.")
        _, sprite = match
        target_x = 200 + sprite.get("x", 0)
        target_y = 205 - sprite.get("y", 0)
        target_radius = 18 * sprite.get("size", 100) / 100

    distance = math.hypot(sprite_x - target_x, sprite_y - target_y)
    if kind == "distance_to":
        return distance
    if kind == "touching":
        own_radius = 18 * sprite_size / 100
        return distance <= own_radius + target_radius
    backdrop_has_color = (
        (
            backdrop_data is None
            and ImageColor.getrgb(arguments[-1])[:3] == (255, 255, 255)
        )
        or (
            backdrop_data is not None
            and image_has_color_near_stage_point(
                backdrop_data, arguments[-1], sprite_x, sprite_y,
                stage_width, stage_height, 18 * sprite_size / 100,
            )
        )
    )
    if kind == "touching_color":
        if backdrop_has_color:
            return True
        return any(
            index != active_sprite_index
            and math.hypot(
                sprite_x - (200 + sprite.get("x", 0)),
                sprite_y - (205 - sprite.get("y", 0)),
            ) <= 18 * (
                sprite_size + sprite.get("size", 100)
            ) / 100
            and any(
                image_contains_color(costume.get("image_data"), arguments[0])
                for costume in sprite.get("costumes", [])
            )
            for index, sprite in enumerate(sprites)
        )
    if kind == "color_touching":
        if actor_image_data is None or not image_contains_color(
            actor_image_data, arguments[0],
        ):
            return False
        if backdrop_has_color:
            return True
        return any(
            index != active_sprite_index
            and math.hypot(
                sprite_x - (200 + sprite.get("x", 0)),
                sprite_y - (205 - sprite.get("y", 0)),
            ) <= 18 * (
                sprite_size + sprite.get("size", 100)
            ) / 100
            and any(
                image_contains_color(costume.get("image_data"), arguments[1])
                for costume in sprite.get("costumes", [])
            )
            for index, sprite in enumerate(sprites)
        )
    raise ValueError(f"Unknown sensing reporter: {kind}.")


def image_contains_color(image_data, color):
    """Check whether an imported image contains a selected RGB color."""
    if not isinstance(image_data, str) or not image_data:
        return False
    try:
        selected = ImageColor.getrgb(color)[:3]
        image_bytes = base64.b64decode(image_data, validate=True)
        with Image.open(io.BytesIO(image_bytes)) as source:
            image = source.convert("RGB")
            image.thumbnail((256, 256), Image.Resampling.NEAREST)
            return any(pixel == selected for pixel in image.getdata())
    except (OSError, ValueError) as error:
        raise ValueError(f"Could not inspect sensing image color: {error}") from error


def image_has_color_near_stage_point(
    image_data, color, sprite_x, sprite_y, stage_width, stage_height, radius,
):
    """Look for a color in the backdrop area currently covered by a sprite."""
    if not isinstance(image_data, str) or not image_data:
        return False
    try:
        selected = ImageColor.getrgb(color)[:3]
        image_bytes = base64.b64decode(image_data, validate=True)
        with Image.open(io.BytesIO(image_bytes)) as source:
            backdrop = source.convert("RGB").resize(
                (stage_width, stage_height), Image.Resampling.BILINEAR,
            )
            center_x = round(stage_width / 2 + sprite_x - 200)
            center_y = round(stage_height / 2 + sprite_y - 205)
            area = backdrop.crop((
                max(0, center_x - round(radius)),
                max(0, center_y - round(radius)),
                min(stage_width, center_x + round(radius) + 1),
                min(stage_height, center_y + round(radius) + 1),
            ))
            return any(pixel == selected for pixel in area.getdata())
    except (OSError, ValueError) as error:
        raise ValueError(f"Could not inspect backdrop color: {error}") from error


def typed_value(data_type, value, variables):
    """Convert a user-entered value to one of the supported Python data types."""
    resolved = parse_value(value, variables)
    try:
        if data_type == "int":
            return int(resolved)
        if data_type == "float":
            return float(resolved)
        if data_type == "str":
            return str(resolved)
        if data_type == "bool":
            if isinstance(resolved, str):
                normalized = resolved.strip().lower()
                if normalized not in ("true", "false"):
                    raise ValueError("Use true or false for a bool value.")
                return normalized == "true"
            return bool(resolved)
        if data_type == "list":
            if not isinstance(resolved, list):
                raise ValueError("List values must use Python list syntax, e.g. [1, 2].")
            return resolved
        if data_type == "dict":
            if not isinstance(resolved, dict):
                raise ValueError('Dictionary values must use syntax like {"name": "Ada"}.')
            return resolved
        if data_type == "None":
            return None
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError(f"Cannot convert {value!r} to {data_type}: {error}") from error
    raise ValueError(f"Unknown data type: {data_type}")


def compare_values(left, operator_name, right):
    """Apply one supported comparison and report incompatible values clearly."""
    operations = {
        "==": operator.eq,
        "!=": operator.ne,
        "<": operator.lt,
        "<=": operator.le,
        ">": operator.gt,
        ">=": operator.ge,
        "contains": lambda first, second: second in first,
    }
    if operator_name == "truthy":
        return bool(left)
    if operator_name == "falsy":
        return not left
    try:
        comparison = operations[operator_name]
    except KeyError as error:
        raise ValueError(f"Unknown comparison: {operator_name}") from error
    try:
        return comparison(left, right)
    except (TypeError, ValueError) as error:
        raise ValueError(f"Cannot compare {left!r} {operator_name} {right!r}: {error}") from error


def evaluate_condition(block, variables, reporter_resolver=None):
    """Resolve both sides of a condition block and return its truth value."""
    left = parse_value(block["left"], variables, reporter_resolver)
    right = parse_value(block["right"], variables, reporter_resolver)
    return compare_values(left, block["operator"], right)


def perform_calculation(block, variables, reporter_resolver=None):
    """Calculate a result and save it in the named variable."""
    left = parse_value(block["left"], variables, reporter_resolver)
    right = parse_value(block["right"], variables, reporter_resolver)
    operations = {
        "+": operator.add,
        "-": operator.sub,
        "*": operator.mul,
        "/": operator.truediv,
        "//": operator.floordiv,
        "%": operator.mod,
        "**": operator.pow,
    }
    try:
        result = operations[block["operator"]](left, right)
    except KeyError as error:
        raise ValueError(f"Unknown math operator: {block['operator']}") from error
    except (ArithmeticError, TypeError) as error:
        raise ValueError(f"Could not calculate {left!r} {block['operator']} {right!r}: {error}") from error
    variables[block["name"]] = result
    return result


def validate_control_flow(actions):
    """Return matching control-block indices or raise for malformed stacks."""
    # A stack pairs each opening if/while block with its else/end block.
    stack = []
    matches = {}
    for index, action in enumerate(actions):
        kind = action["kind"]
        if kind in ("if", "while"):
            stack.append((kind, index, None))
        elif kind == "else":
            if not stack or stack[-1][0] != "if" or stack[-1][2] is not None:
                raise ValueError("An 'else' block needs one open 'if' block.")
            opened = stack[-1]
            stack[-1] = (opened[0], opened[1], index)
        elif kind in ("end_if", "end_while"):
            expected = "if" if kind == "end_if" else "while"
            if not stack or stack[-1][0] != expected:
                raise ValueError(f"'{kind}' does not match an open '{expected}' block.")
            _, start, alternate = stack.pop()
            matches[start] = index
            matches[index] = start
            if alternate is not None:
                matches[alternate] = index
                matches[start, "else"] = alternate
    if stack:
        kind, _, _ = stack[-1]
        raise ValueError(f"A '{kind}' block is missing its matching end block.")
    return matches


def expand_script(blocks):
    """Expand repeat blocks while preserving runtime conditional blocks."""
    def parse(index, nested=False, depth=0):
        """Read a repeat body recursively and return its flat action list."""
        if depth > 64:
            raise ValueError("Repeat blocks can only be nested 64 levels deep.")

        actions = []
        while index < len(blocks):
            block = blocks[index]
            kind = block["kind"]
            if kind == "end_repeat":
                if not nested:
                    raise ValueError("There is an 'end repeat' without a matching repeat.")
                return actions, index + 1
            if kind == "repeat":
                body, index = parse(index + 1, nested=True, depth=depth + 1)
                count = block["count"]
                if not isinstance(count, int) or count < 0:
                    raise ValueError("Repeat count must be a non-negative whole number.")
                if body and count > (MAX_ACTIONS - len(actions)) // len(body):
                    raise ValueError(f"Script is too large to run (limit: {MAX_ACTIONS} actions).")
                # Expand repeats up front; cap the result to protect the runtime.
                actions.extend(body * count)
                continue
            if kind not in ("flag", "key", "end_repeat") and kind not in REPORTER_BLOCK_KINDS:
                actions.append(block.copy())
                if len(actions) > MAX_ACTIONS:
                    raise ValueError(f"Script is too large to run (limit: {MAX_ACTIONS} actions).")
            index += 1

        if nested:
            raise ValueError("A repeat block is missing its matching 'end repeat'.")
        return actions, index

    result, _ = parse(0)
    validate_control_flow(result)
    return result


def split_event_scripts(blocks):
    """Split scripts in workspace column order, then in top-to-bottom block order."""
    blocks = list(blocks)
    if blocks and all(
        isinstance(block, dict)
        and "workspace_x" in block
        and "workspace_y" in block
        for block in blocks
    ):
        blocks.sort(
            key=lambda block: (
                block["workspace_x"],
                block["workspace_y"],
            )
        )
    # Event blocks only begin a new script at the outer level, not inside a loop.
    if not any(block["kind"] in ("flag", "key") for block in blocks):
        return [{"event": "flag", "key": None, "blocks": list(blocks)}]

    scripts = []
    current_event = "flag"
    current_key = None
    current_blocks = []
    # Track open containers so event blocks inside them stay part of the script.
    stack = []
    saw_event = False

    for block in blocks:
        kind = block["kind"]
        if not stack and kind in ("flag", "key"):
            if saw_event or current_blocks:
                scripts.append({
                    "event": current_event,
                    "key": current_key,
                    "blocks": current_blocks,
                })
            current_event = "key" if kind == "key" else "flag"
            current_key = block.get("key") if kind == "key" else None
            current_blocks = []
            saw_event = True
            continue

        current_blocks.append(block)
        if kind in ("repeat", "if", "while"):
            stack.append(kind)
        elif kind in ("end_repeat", "end_if", "end_while"):
            if stack:
                stack.pop()

    if saw_event or current_blocks:
        scripts.append({
            "event": current_event,
            "key": current_key,
            "blocks": current_blocks,
        })
    return scripts
