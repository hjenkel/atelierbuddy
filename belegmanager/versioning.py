from __future__ import annotations

from importlib import metadata
from pathlib import Path
import tomllib

PACKAGE_NAME = "belegmanager"
FALLBACK_VERSION = "0.0.0-dev"


def get_app_version() -> str:
    """Return app version from pyproject, with package metadata fallback.

    Single source of truth is ``pyproject.toml`` (project.version).
    """
    pyproject_path = Path(__file__).resolve().parents[1] / "pyproject.toml"
    try:
        parsed = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
        version = str(parsed.get("project", {}).get("version") or "").strip()
        if version:
            return version
    except Exception:
        pass

    try:
        version = metadata.version(PACKAGE_NAME)
        if version:
            return version
    except metadata.PackageNotFoundError:
        pass

    return FALLBACK_VERSION
