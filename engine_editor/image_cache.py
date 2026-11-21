"""Bounded cache for decoded and transformed stage images."""

import base64
import io
from collections import OrderedDict

from PIL import Image


class ImageCache:
    """Keep recently used image resources under a fixed memory budget."""

    def __init__(self, max_bytes=64 * 1024 * 1024):
        self.max_bytes = max(0, int(max_bytes))
        self._entries = OrderedDict()
        self._used_bytes = 0

    @property
    def used_bytes(self):
        """Approximate the pixel memory currently retained by cached resources."""
        return self._used_bytes

    def get(self, key, factory):
        """Return a cached resource or create and retain it within the budget."""
        entry = self._entries.pop(key, None)
        if entry is not None:
            self._entries[key] = entry
            return entry[0]

        resource = factory()
        size_bytes = self._resource_size(resource)
        if size_bytes <= self.max_bytes:
            self._entries[key] = (resource, size_bytes)
            self._used_bytes += size_bytes
            self._trim()
        return resource

    def clear(self):
        """Drop cached references so Python and Tk can release unused pixels."""
        self._entries.clear()
        self._used_bytes = 0

    def set_budget_for_memory_limit(self, memory_limit_mb):
        """Reserve a bounded fraction of the configured process memory limit."""
        cache_budget_mb = max(8, min(64, int(memory_limit_mb * 0.125)))
        self.max_bytes = cache_budget_mb * 1024 * 1024
        self._trim()

    def discard_asset(self, image_data):
        """Remove cached transforms whose key refers to a removed image asset."""
        for key in list(self._entries):
            if len(key) > 1 and key[1] is image_data:
                _resource, size_bytes = self._entries.pop(key)
                self._used_bytes -= size_bytes

    def _trim(self):
        while self._used_bytes > self.max_bytes and self._entries:
            _key, (_resource, size_bytes) = self._entries.popitem(last=False)
            self._used_bytes -= size_bytes

    @staticmethod
    def _resource_size(resource):
        size = getattr(resource, "size", None)
        if not isinstance(size, tuple) or len(size) != 2:
            width = getattr(resource, "width", None)
            height = getattr(resource, "height", None)
            if callable(width):
                width = width()
            if callable(height):
                height = height()
            size = (width, height)
        try:
            width, height = (int(dimension) for dimension in size)
        except (TypeError, ValueError):
            return 0
        return max(0, width) * max(0, height) * 4


def decode_image_data(image_data):
    """Decode project base64 image data into a detached RGBA Pillow image."""
    image_bytes = base64.b64decode(image_data, validate=True)
    with Image.open(io.BytesIO(image_bytes)) as source:
        return source.convert("RGBA")
