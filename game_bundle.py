"""Attach and read game-project data at the end of a Windows game EXE."""

import json
import os
import shutil
import tempfile
from pathlib import Path


_PAYLOAD_MARKER = b"\nSTEVE_SCRATCH_GAME_PAYLOAD_V1\n"
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
            prefix="steve-game-",
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
        trailer_size = len(_PAYLOAD_MARKER) + payload_size + _LENGTH_BYTES
        if payload_size <= 0 or trailer_size > file_size:
            return None

        game_file.seek(-trailer_size, os.SEEK_END)
        if game_file.read(len(_PAYLOAD_MARKER)) != _PAYLOAD_MARKER:
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
