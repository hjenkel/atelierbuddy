from __future__ import annotations

from importlib import metadata
from pathlib import Path
import tomllib

PACKAGE_NAME = "belegmanager"
FALLBACK_VERSION = "0.0.0-dev"


def get_app_version() -> str:
    """Return app version from package metadata, with pyproject fallback.

    Single source of truth is ``pyproject.toml`` (project.version).
    Package metadata is preferred at runtime and mirrors pyproject when installed.
    """
    try:
        version = metadata.version(PACKAGE_NAME)
        if version:
            return version
    except metadata.PackageNotFoundError:
        pass

    pyproject_path = Path(__file__).resolve().parents[1] / "pyproject.toml"
    try:
        parsed = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
        return str(parsed.get("project", {}).get("version") or FALLBACK_VERSION)
    except Exception:
        return FALLBACK_VERSION
