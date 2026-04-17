from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("yaya")
except PackageNotFoundError:  # pragma: no cover
    __version__ = "0.0.0+unknown"

__all__ = ["__version__"]
