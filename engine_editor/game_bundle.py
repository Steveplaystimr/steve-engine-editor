"""Attach and read game-project data at the end of a Windows game EXE."""

import json
import os
import shutil
import tempfile
import zipfile
from pathlib import Path


_PAYLOAD_MARKER = b"\nENGINE_EDITOR_GAME_PAYLOAD_V1\n"
_PROJECT_FORMATS = {
    "engine-editor-project",
    "steve-engine-editor-project",
}
# Continue loading game executables produced by earlier Steve Engine Editor builds.
_LEGACY_PAYLOAD_MARKERS = (b"\nSTEVEENGINEEDITOR_GAME_PAYLOAD_V1\n",)
# A fixed-width trailer records the JSON size so it can be found from EOF.
_LENGTH_BYTES = 8


def append_game_payload(
    editor_executable: Path,
    game_executable: Path,
    blocks: list[dict],
    settings: dict | None = None,
    runtime_directory: Path | None = None,
) -> None:
    """Copy the editor EXE and its optional onedir runtime, then append game data."""
    game_data = blocks if settings is None else {
        "version": 1,
        "blocks": blocks,
        "settings": settings,
    }
    payload = json.dumps(game_data, ensure_ascii=False).encode("utf-8")
    game_executable.parent.mkdir(parents=True, exist_ok=True)
    if runtime_directory is not None:
        if not runtime_directory.is_dir():
            raise FileNotFoundError(
                f"The packaged game runtime was not found: {runtime_directory}"
            )
        shutil.copytree(
            runtime_directory,
            game_executable.parent / "_internal",
            dirs_exist_ok=True,
        )

    # Build beside the destination so os.replace can install it atomically.
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix="engine-editor-game-",
            suffix=".tmp",
            dir=game_executable.parent,
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            with editor_executable.open("rb") as source:
                shutil.copyfileobj(source, temporary_file)
            temporary_file.write(_PAYLOAD_MARKER)
            temporary_file.write(payload)
            temporary_file.write(len(payload).to_bytes(_LENGTH_BYTES, "big"))
            temporary_file.flush()
            os.fsync(temporary_file.fileno())

        os.replace(temporary_path, game_executable)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def read_game_payload(executable: Path) -> list[dict] | dict[str, object] | None:
    """Return embedded game data, or None when this EXE has no game payload."""
    file_size = executable.stat().st_size
    if file_size < _LENGTH_BYTES:
        return None

    with executable.open("rb") as game_file:
        game_file.seek(-_LENGTH_BYTES, os.SEEK_END)
        payload_size = int.from_bytes(game_file.read(_LENGTH_BYTES), "big")
        if payload_size <= 0:
            return None

        marker = None
        for candidate in (_PAYLOAD_MARKER, *_LEGACY_PAYLOAD_MARKERS):
            trailer_size = len(candidate) + payload_size + _LENGTH_BYTES
            if trailer_size > file_size:
                continue
            game_file.seek(-trailer_size, os.SEEK_END)
            if game_file.read(len(candidate)) == candidate:
                marker = candidate
                break
        if marker is None:
            return None

        payload = json.loads(game_file.read(payload_size).decode("utf-8"))

    if isinstance(payload, list):
        if any(not isinstance(block, dict) for block in payload):
            raise ValueError("The embedded game script has an invalid block list.")
    elif isinstance(payload, dict):
        blocks = payload.get("blocks")
        settings = payload.get("settings", {})
        if not isinstance(blocks, list) or any(not isinstance(block, dict) for block in blocks):
            raise ValueError("The embedded game script has an invalid block list.")
        if not isinstance(settings, dict):
            raise ValueError("The embedded game settings have an invalid format.")
    else:
        raise ValueError("The embedded game file has an invalid format.")
    return payload


def read_sse_game_data(project_path: Path) -> tuple[list[dict], dict[str, object]]:
    """Load runtime data from a compressed or legacy JSON .sse project."""
    if zipfile.is_zipfile(project_path):
        with zipfile.ZipFile(project_path, mode="r") as archive:
            try:
                serialized_project = archive.read("project.json")
            except KeyError as error:
                raise ValueError(
                    "The compressed .sse file is missing project.json."
                ) from error
        project = json.loads(serialized_project.decode("utf-8"))
    else:
        project = json.loads(project_path.read_text(encoding="utf-8"))

    if (
        not isinstance(project, dict)
        or project.get("format") not in _PROJECT_FORMATS
        or not isinstance(project.get("blocks"), list)
    ):
        raise ValueError("This file is not a supported .sse project.")

    project_settings = project.get("game_settings", {})
    backdrops = project.get("backdrops", [])
    custom_blocks = project.get("custom_blocks", [])
    sounds = project.get("sounds", [])
    mod_sources = project.get("mod_sources", {})
    if not isinstance(project_settings, dict):
        raise ValueError("The project contains invalid game settings.")
    if not isinstance(backdrops, list) or not isinstance(custom_blocks, list):
        raise ValueError("The project contains invalid media or custom blocks.")
    if not isinstance(sounds, list) or not isinstance(mod_sources, dict):
        raise ValueError("The project contains invalid sounds or mods.")

    sprites = project.get("sprites")
    if sprites is None:
        sprite = project.get("sprite", {})
        if not isinstance(sprite, dict):
            raise ValueError("The project contains invalid sprite data.")
        sprites = [{**sprite, "blocks": project["blocks"]}]
        active_sprite_index = 0
    else:
        active_sprite_index = project.get("active_sprite_index", 0)
        if (
            not isinstance(sprites, list)
            or not sprites
            or isinstance(active_sprite_index, bool)
            or not isinstance(active_sprite_index, int)
            or not 0 <= active_sprite_index < len(sprites)
        ):
            raise ValueError("The project contains an invalid sprite selection.")

    selected_sprite = sprites[active_sprite_index]
    if not isinstance(selected_sprite, dict):
        raise ValueError("The project contains invalid sprite data.")
    blocks = selected_sprite.get("blocks")
    if not isinstance(blocks, list) or any(
        not isinstance(block, dict) for block in blocks
    ):
        raise ValueError("The project contains an invalid sprite script.")

    settings = {
        **project_settings,
        "sprite": selected_sprite,
        "backdrops": backdrops,
        "sprites": sprites,
        "active_sprite_index": active_sprite_index,
        "custom_blocks": custom_blocks,
        "sounds": sounds,
        "mod_sources": mod_sources,
    }
    return blocks, settings
