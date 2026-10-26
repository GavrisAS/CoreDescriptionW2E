"""Version is frozen at build time; runtime never consults environment variables."""

try:
    from ._build_version import VERSION as __version__  # ty: ignore[unresolved-import]
except ImportError:
    __version__ = "0.1.0"
