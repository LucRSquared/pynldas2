"""Top-level package."""
from importlib.metadata import PackageNotFoundError, version

from .exceptions import InputRangeError, InputTypeError, InputValueError, NLDASServiceError
from .print_versions import show_versions
from .pynldas2 import get_bycoords, get_bygeom, get_grid_mask

try:
    __version__ = version("pynldas2")
except PackageNotFoundError:
    __version__ = "999"

__all__ = [
    "get_bycoords",
    "get_grid_mask",
    "get_bygeom",
    "InputTypeError",
    "InputValueError",
    "InputRangeError",
    "show_versions",
    "InputTypeError",
    "InputValueError",
    "InputRangeError",
    "NLDASServiceError",
]
