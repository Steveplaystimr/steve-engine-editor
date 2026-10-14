"""Create small sound effects in memory and play them with Windows audio."""

import base64
import binascii
import io
import math
import struct
import wave


_SAMPLE_RATE = 22_050
_SOUNDS = {"Meow", "Pop", "Bell", "Drum"}
_current_audio = None
_pygame_audio_active = False


def _wave_bytes(sound, pitch, volume, duration):
    """Build a mono WAV byte string for one named tone effect."""
    if sound not in _SOUNDS:
        raise ValueError(f"Unknown sound: {sound!r}.")
    if not isinstance(pitch, (int, float)) or not -100 <= pitch <= 100:
        raise ValueError("Sound pitch must be between -100 and 100.")
    if not isinstance(volume, (int, float)) or not 0 <= volume <= 100:
        raise ValueError("Sound volume must be between 0 and 100.")
    frame_count = int(_SAMPLE_RATE * duration)
    amplitude = max(0.0, min(volume, 100.0)) / 100 * 0.35
    pitch_scale = 2 ** (max(-100, min(pitch, 100)) / 100)
    samples = bytearray()

    # Change the tone and its loudness envelope across the sound's lifetime.
    for index in range(frame_count):
        progress = index / max(frame_count - 1, 1)
        if sound == "Meow":
            frequency = (760 - 430 * progress) * pitch_scale
            envelope = min(1.0, progress * 12, (1 - progress) * 7)
            sample = math.sin(2 * math.pi * frequency * index / _SAMPLE_RATE)
            sample += 0.28 * math.sin(4 * math.pi * frequency * index / _SAMPLE_RATE)
            value = int(max(-1.0, min(1.0, sample / 1.28 * envelope * amplitude)) * 32767)
        elif sound == "Pop":
            frequency = 350 * (1 - progress) + 80
            envelope = (1 - progress) ** 3
            value = int(
                math.sin(2 * math.pi * frequency * index / _SAMPLE_RATE)
                * envelope * amplitude * 32767
            )
        elif sound == "Drum":
            frequency = (125 - 65 * progress) * pitch_scale
            envelope = (1 - progress) ** 2
            value = int(
                math.sin(2 * math.pi * frequency * index / _SAMPLE_RATE)
                * envelope * amplitude * 32767
            )
        else:
            frequency = 660 * pitch_scale
            envelope = min(1.0, progress * 25, (1 - progress) * 5)
            value = int(
                math.sin(2 * math.pi * frequency * index / _SAMPLE_RATE)
                * envelope * amplitude * 32767
            )
        samples.extend(struct.pack("<h", value))

    # Store the generated samples in a standard WAV container for winsound.
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(_SAMPLE_RATE)
        output.writeframes(samples)
    return buffer.getvalue()


def play_sound(sound, pitch=0, volume=100):
    """Play one of the built-in synthesized effects asynchronously."""
    import winsound

    if sound not in _SOUNDS:
        raise ValueError(f"Unknown sound: {sound!r}.")
    global _current_audio
    duration = 0.45 if sound == "Meow" else 0.24
    _current_audio = _wave_bytes(sound, pitch, volume, duration)
    winsound.PlaySound(
        _current_audio,
        winsound.SND_MEMORY | winsound.SND_ASYNC | winsound.SND_NODEFAULT,
    )
    return round(duration * 1000)


def play_audio_data(audio_data, file_format, volume=100) -> None:
    """Play an imported WAV, MP3, or OGG asset asynchronously."""
    import pygame

    if not isinstance(audio_data, bytes) or not audio_data:
        raise ValueError("Imported sound data is empty.")
    file_format = str(file_format).lower().lstrip(".")
    if file_format not in ("wav", "mp3", "ogg"):
        raise ValueError("Imported sounds must be WAV, MP3, or OGG files.")
    if isinstance(volume, bool) or not isinstance(volume, (int, float)):
        raise ValueError("Sound volume must be a number between 0 and 100.")
    if not math.isfinite(volume) or not 0 <= volume <= 100:
        raise ValueError("Sound volume must be between 0 and 100.")

    global _current_audio, _pygame_audio_active
    try:
        if not pygame.mixer.get_init():
            pygame.mixer.init()
        _current_audio = io.BytesIO(audio_data)
        pygame.mixer.music.load(_current_audio, namehint=f".{file_format}")
        pygame.mixer.music.set_volume(volume / 100)
        pygame.mixer.music.play()
        _pygame_audio_active = True
    except (pygame.error, OSError) as error:
        _current_audio = None
        _pygame_audio_active = False
        raise RuntimeError(f"Could not play imported {file_format.upper()} sound: {error}") from error


def play_audio_asset(asset, volume=100) -> None:
    """Decode and play one project sound asset."""
    if not isinstance(asset, dict):
        raise ValueError("The selected imported sound is malformed.")
    file_format = asset.get("format")
    encoded_data = asset.get("audio_data")
    if not isinstance(encoded_data, str):
        raise ValueError("The selected imported sound has no audio data.")
    try:
        audio_data = base64.b64decode(encoded_data, validate=True)
    except (ValueError, binascii.Error) as error:
        raise ValueError("The selected imported sound has invalid audio data.") from error
    return play_audio_data(audio_data, file_format, volume)


def play_project_sound(name, assets, pitch=0, volume=100) -> int | None:
    """Play a built-in effect or a sound asset saved in the project."""
    if not isinstance(name, str):
        raise ValueError("The selected sound name must be text.")
    if isinstance(assets, list):
        asset = next(
            (
                item for item in assets
                if isinstance(item, dict)
                and isinstance(item.get("name"), str)
                and item["name"].casefold() == name.casefold()
            ),
            None,
        )
        if asset is not None:
            return play_audio_asset(asset, volume)
    return play_sound(name, pitch, volume)


def imported_audio_is_playing():
    """Report whether the project's imported audio stream is still active."""
    if not _pygame_audio_active:
        return False
    import pygame

    return pygame.mixer.music.get_busy()


def stop_sounds():
    """Stop the current Windows sound and release its in-memory WAV data."""
    global _current_audio, _pygame_audio_active
    try:
        import winsound

        winsound.PlaySound(None, 0)
    except (ImportError, RuntimeError):
        pass
    if _pygame_audio_active:
        import pygame

        pygame.mixer.music.stop()
        _pygame_audio_active = False
    _current_audio = None
