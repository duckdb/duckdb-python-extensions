"""Build pip wheels from DuckDB extension binaries."""

from .binary import (
    PLATFORMS,
    Extension,
    Footer,
    WheelError,
    extension_from_bytes,
    inspect_extension,
    read_footer,
)
from .wheel import WheelMetadata, build_wheels, dist_name, wheel_filename, write_wheel

__all__ = [
    "WheelError",
    "Footer",
    "read_footer",
    "PLATFORMS",
    "Extension",
    "inspect_extension",
    "extension_from_bytes",
    "WheelMetadata",
    "write_wheel",
    "build_wheels",
    "dist_name",
    "wheel_filename",
]
