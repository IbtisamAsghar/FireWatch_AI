"""Windows extended filesystem paths without changing saved artifact identities."""

import os
from pathlib import Path


def filesystem_path(path: Path) -> Path:
    """Use extended paths for I/O, retaining normal paths on other platforms.

    This does not grant trust or perform containment checks. Callers must still
    validate package names, resolved containment and symlinks before reading.
    Never serialize the returned Windows prefix into evidence or manifests.
    """
    if os.name != "nt":
        return path
    absolute = os.path.abspath(path)
    if absolute.startswith("\\\\?\\"):
        return Path(absolute)
    if absolute.startswith("\\\\"):
        return Path("\\\\?\\UNC\\" + absolute[2:])
    return Path("\\\\?\\" + absolute)
