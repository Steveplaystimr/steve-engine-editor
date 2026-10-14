"""Public API and loader for trusted Steve Scratch Engine Python mods."""

import ast
import hashlib
import math
import re
import string
import sys
import traceback
import types
from dataclasses import dataclass
from pathlib import Path


_MOD_ID = re.compile(r"[a-z][a-z0-9_]*\Z")
_INPUT_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_INPUT_TYPES = {"text", "number", "boolean"}


@dataclass(frozen=True)
class ModInput:
    """Describe one editable input shown on a modded block."""

    name: str
    input_type: str
    default: object


@dataclass(frozen=True)
class ModBlock:
    """A registered block definition and its Python callback."""

    kind: str
    mod_id: str
    block_id: str
    label: str
    inputs: tuple[ModInput, ...]
    callback: object


class ModContext:
    """Mutable sprite and variable data provided to a mod block callback."""

    def __init__(self, sprite, variables):
        self.sprite = sprite
        self.variables = variables

    def get_variable(self, name, default=None):
        """Return a variable, or a caller-provided default when unset."""
        return self.variables.get(name, default)

    def set_variable(self, name, value):
        """Set a project variable."""
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Variable names must be non-empty text.")
        self.variables[name] = value

    def move(self, steps):
        """Move the sprite along its current direction in Scratch coordinates."""
        steps = _finite_number(steps, "steps")
        direction = math.radians(_finite_number(
            self.sprite.get("direction", 90), "sprite direction",
        ))
        self.sprite["x"] = _finite_number(self.sprite.get("x", 0), "sprite x")
        self.sprite["y"] = _finite_number(self.sprite.get("y", 0), "sprite y")
        self.sprite["x"] += steps * math.sin(direction)
        self.sprite["y"] += steps * math.cos(direction)

    def turn(self, degrees):
        """Turn the sprite clockwise by the requested degrees."""
        self.sprite["direction"] = (
            _finite_number(self.sprite.get("direction", 90), "sprite direction")
            + _finite_number(degrees, "turn amount")
        ) % 360

    def validate(self):
        """Reject invalid sprite data before a runtime applies mod changes."""
        for name in ("x", "y", "direction", "size"):
            default = {"x": 0, "y": 0, "direction": 90, "size": 100}[name]
            self.sprite[name] = _finite_number(
                self.sprite.get(name, default),
                f"sprite {name}",
            )
        if not 10 <= self.sprite["size"] <= 300:
            raise ValueError("Modded sprite size must be between 10 and 300.")
        if not isinstance(self.sprite.get("visible", True), bool):
            raise ValueError("Modded sprite visibility must be true or false.")
        self.sprite["direction"] %= 360


class ModdingAPI:
    """Collect block declarations made by a single mod file."""

    def __init__(self, mod_id):
        self.mod_id = mod_id
        self.blocks = []

    def block(self, block_id, label=None, inputs=None):
        """Register a block with a decorator-style callback declaration."""
        def decorate(callback):
            self.register_block(block_id, label, inputs, callback)
            return callback

        return decorate

    def register_block(self, block_id, label, inputs, callback):
        """Register a block directly with its callback function."""
        if not isinstance(block_id, str) or not _MOD_ID.fullmatch(block_id):
            raise ValueError(
                "Block IDs must start with a lowercase letter and contain only "
                "lowercase letters, numbers, or underscores."
            )
        if not callable(callback):
            raise ValueError(f"Callback for block '{block_id}' must be callable.")
        if any(block.block_id == block_id for block in self.blocks):
            raise ValueError(f"Mod '{self.mod_id}' registered '{block_id}' twice.")

        parsed_inputs = _parse_inputs({} if inputs is None else inputs)
        visible_label = label if label is not None else block_id.replace("_", " ")
        if not isinstance(visible_label, str) or not visible_label.strip():
            raise ValueError(f"Block '{block_id}' needs a visible text label.")
        fields = {
            field_name
            for _, field_name, _, _ in string.Formatter().parse(visible_label)
            if field_name
        }
        input_names = {item.name for item in parsed_inputs}
        unknown_fields = fields - input_names
        if unknown_fields:
            raise ValueError(
                f"Block '{block_id}' label refers to unknown input(s): "
                f"{', '.join(sorted(unknown_fields))}."
            )

        self.blocks.append(ModBlock(
            kind=f"mod:{self.mod_id}:{block_id}",
            mod_id=self.mod_id,
            block_id=block_id,
            label=visible_label.strip(),
            inputs=parsed_inputs,
            callback=callback,
        ))


class ModRegistry:
    """Load mod files and provide validated block metadata and callbacks."""

    def __init__(self):
        self.blocks = {}
        self.sources = {}
        self.errors = []
        self.module_names = []

    def clear(self):
        """Remove all previously loaded mod registrations."""
        for module_name in self.module_names:
            sys.modules.pop(module_name, None)
        self.blocks.clear()
        self.sources.clear()
        self.errors.clear()
        self.module_names.clear()

    def load_directory(self, directory):
        """Load Python mods from a directory, reporting each failed mod."""
        self.clear()
        folder = Path(directory)
        folder.mkdir(parents=True, exist_ok=True)
        for path in sorted(folder.glob("*.py"), key=lambda item: item.name.lower()):
            if path.name.startswith("_"):
                continue
            mod_id = path.stem.lower()
            try:
                source = path.read_text(encoding="utf-8")
                self._load_source(mod_id, source, path)
            except Exception as error:
                self.errors.append(
                    f"{path.name}: {error}\n{traceback.format_exc()}"
                )
        return tuple(self.errors)

    def load_sources(self, sources):
        """Load source strings embedded into a generated game executable."""
        self.clear()
        if not isinstance(sources, dict):
            raise ValueError("Embedded mod sources must be a filename-to-source map.")
        for filename, source in sorted(sources.items()):
            if (
                not isinstance(filename, str)
                or Path(filename).name != filename
                or not filename.lower().endswith(".py")
                or Path(filename).stem.lower() in ("", "__init__")
                or not isinstance(source, str)
            ):
                raise ValueError(f"Invalid embedded mod entry: {filename!r}.")
            mod_id = Path(filename).stem.lower()
            try:
                self._load_source(mod_id, source, Path(filename))
            except Exception as error:
                self.errors.append(
                    f"{filename}: {error}\n{traceback.format_exc()}"
                )
        return tuple(self.errors)

    def _load_source(self, mod_id, source, origin):
        if not _MOD_ID.fullmatch(mod_id):
            raise ValueError(
                "Mod filenames must start with a lowercase letter and contain "
                "only lowercase letters, numbers, or underscores."
            )
        digest = hashlib.sha256(str(origin).encode("utf-8")).hexdigest()[:12]
        module_name = f"_sse_mod_{mod_id}_{digest}"
        module = types.ModuleType(module_name)
        module.__file__ = str(origin)
        api = ModdingAPI(mod_id)
        module.__dict__.update({
            "__name__": module_name,
            "__file__": str(origin),
            "__package__": "",
            "api": api,
        })
        sys.modules[module_name] = module
        self.module_names.append(module_name)
        try:
            exec(compile(source, str(origin), "exec"), module.__dict__)
            register = module.__dict__.get("register")
            if not callable(register):
                raise ValueError("A mod must define register(api).")
            register(api)
            if not api.blocks:
                raise ValueError("The mod did not register any blocks.")
            duplicates = [
                block.kind for block in api.blocks if block.kind in self.blocks
            ]
            if duplicates:
                raise ValueError(
                    f"Duplicate block ID(s): {', '.join(duplicates)}."
                )
            for block in api.blocks:
                self.blocks[block.kind] = block
            self.sources[f"{mod_id}.py"] = source
        except Exception:
            sys.modules.pop(module_name, None)
            raise

    def get(self, kind):
        """Return a block definition, or None when the kind is not a mod."""
        return self.blocks.get(kind)

    def create_block(self, kind):
        """Create stored block data populated with declared input defaults."""
        definition = self.get(kind)
        if definition is None:
            raise ValueError(f"Unknown modded block: {kind}.")
        return {
            "kind": kind,
            "inputs": {item.name: item.default for item in definition.inputs},
        }

    def label(self, block):
        """Format a saved block's label with its current input values."""
        definition = self.get(block.get("kind"))
        if definition is None:
            raise ValueError(f"Unknown modded block: {block.get('kind')}.")
        values = block.get("inputs", {})
        if not isinstance(values, dict):
            raise ValueError(f"Inputs for '{definition.block_id}' are malformed.")
        formatted = {
            item.name: coerce_input(values.get(item.name, item.default), item)
            for item in definition.inputs
        }
        try:
            return definition.label.format_map(formatted)
        except (KeyError, ValueError) as error:
            raise ValueError(f"Invalid label for modded block '{definition.block_id}'.") from error

    def execute(self, block, context):
        """Validate block inputs and invoke the registered Python callback."""
        definition = self.get(block.get("kind"))
        if definition is None:
            raise ValueError(f"Mod for block '{block.get('kind')}' is not loaded.")
        values = block.get("inputs", {})
        if not isinstance(values, dict):
            raise ValueError(f"Inputs for '{definition.block_id}' are malformed.")
        expected = {item.name for item in definition.inputs}
        unknown = set(values) - expected
        if unknown:
            raise ValueError(
                f"Block '{definition.block_id}' has unknown input(s): "
                f"{', '.join(sorted(str(name) for name in unknown))}."
            )
        arguments = {
            item.name: coerce_input(values.get(item.name, item.default), item)
            for item in definition.inputs
        }
        try:
            result = definition.callback(context, **arguments)
        except Exception as error:
            raise ValueError(
                f"Python mod block '{definition.mod_id}:{definition.block_id}' "
                f"failed: {error}"
            ) from error
        context.validate()
        if result is not None:
            raise ValueError(
                f"Mod block '{definition.block_id}' returned a value; "
                "statement blocks must return None."
            )


def _parse_inputs(inputs):
    if not isinstance(inputs, dict):
        raise ValueError("Block inputs must be declared in a dictionary.")
    result = []
    for name, specification in inputs.items():
        if not isinstance(name, str) or not _INPUT_NAME.fullmatch(name):
            raise ValueError(f"Invalid block input name: {name!r}.")
        if isinstance(specification, str):
            input_type, default = specification, _default_for(specification)
        elif isinstance(specification, (tuple, list)) and len(specification) == 2:
            input_type, default = specification
        elif isinstance(specification, dict):
            input_type = specification.get("type")
            default = specification.get("default", _default_for(input_type))
        else:
            raise ValueError(
                f"Input '{name}' must use a type or a (type, default) pair."
            )
        if input_type not in _INPUT_TYPES:
            raise ValueError(
                f"Input '{name}' type must be text, number, or boolean."
            )
        item = ModInput(name, input_type, default)
        default = coerce_input(default, item)
        result.append(ModInput(name, input_type, default))
    return tuple(result)


def _default_for(input_type):
    return {"text": "", "number": 0, "boolean": False}.get(input_type)


def _finite_number(value, label):
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a finite number.")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError(f"{label} must be a finite number.") from error
    if not math.isfinite(result):
        raise ValueError(f"{label} must be a finite number.")
    return result


def coerce_input(value, definition):
    """Convert and validate one saved or user-entered mod input."""
    if definition.input_type == "text":
        if not isinstance(value, (str, int, float, bool)):
            raise ValueError(f"Input '{definition.name}' must be simple text.")
        return str(value)
    if definition.input_type == "boolean":
        if isinstance(value, bool):
            return value
        normalized = str(value).strip().lower()
        if normalized in ("true", "yes", "1"):
            return True
        if normalized in ("false", "no", "0"):
            return False
        raise ValueError(f"Input '{definition.name}' must be true or false.")
    number = _finite_number(value, f"Input '{definition.name}'")
    if isinstance(definition.default, int) and not isinstance(definition.default, bool):
        if not number.is_integer():
            raise ValueError(f"Input '{definition.name}' must be a whole number.")
        return int(number)
    return number


MOD_REGISTRY = ModRegistry()


def mod_import_names(sources):
    """Return imports referenced by mod sources for PyInstaller analysis."""
    names = set()
    for filename, source in sources.items():
        try:
            tree = ast.parse(source, filename=filename)
        except (SyntaxError, TypeError) as error:
            raise ValueError(f"Could not analyze imports in {filename}: {error}") from error
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names.add(node.module)
    return tuple(sorted(names))
