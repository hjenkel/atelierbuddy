from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from importlib import metadata
from pathlib import Path
from typing import Any

from .config import settings

APP_LICENSE_ID = "AGPL-3.0-or-later"
APP_COPYRIGHT = "Copyright (c) 2026 Hanno Jenkel"
THIRD_PARTY_CACHE_FILENAME = "third_party_notices.json"
UNKNOWN_LICENSE = "Unknown (manuelle Prüfung nötig)"
_LICENSE_FILE_MARKERS = ("license", "copying", "notice")
_MAX_LICENSE_TEXT_CHARS = 50000
_MAX_LICENSE_FILES_PER_PACKAGE = 4


@dataclass(frozen=True)
class ThirdPartyLicenseFile:
    path: str
    text: str


@dataclass(frozen=True)
class ThirdPartyNotice:
    name: str
    version: str
    license: str
    homepage: str
    license_files: list[ThirdPartyLicenseFile]


def _extract_homepage(meta: metadata.PackageMetadata) -> str:
    homepage = (meta.get("Home-page") or "").strip()
    if homepage:
        return homepage
    project_urls = meta.get_all("Project-URL") or []
    for entry in project_urls:
        raw = str(entry or "")
        if "," in raw:
            _, url = raw.split(",", 1)
            candidate = url.strip()
            if candidate:
                return candidate
        elif raw.strip():
            return raw.strip()
    return ""


def _extract_license(meta: metadata.PackageMetadata) -> str:
    value = (meta.get("License") or "").strip()
    if value and value.lower() not in {"unknown", "n/a", "none"}:
        return value
    classifiers = [str(item) for item in (meta.get_all("Classifier") or [])]
    license_classifiers = sorted({item for item in classifiers if item.startswith("License ::")})
    if license_classifiers:
        return "; ".join(license_classifiers)
    return UNKNOWN_LICENSE


def _looks_like_license_file(path: str) -> bool:
    name = Path(path).name.casefold()
    return any(marker in name for marker in _LICENSE_FILE_MARKERS)


def _read_license_files(dist: metadata.Distribution) -> list[ThirdPartyLicenseFile]:
    collected: list[ThirdPartyLicenseFile] = []
    files = dist.files or []
    for entry in files:
        entry_path = str(entry)
        if not _looks_like_license_file(entry_path):
            continue
        absolute = Path(dist.locate_file(entry))
        if not absolute.is_file():
            continue
        try:
            text = absolute.read_text(encoding="utf-8", errors="ignore").strip()
        except OSError:
            continue
        if not text:
            continue
        if len(text) > _MAX_LICENSE_TEXT_CHARS:
            text = text[:_MAX_LICENSE_TEXT_CHARS] + "\n\n[gekürzt]"
        collected.append(ThirdPartyLicenseFile(path=entry_path, text=text))
        if len(collected) >= _MAX_LICENSE_FILES_PER_PACKAGE:
            break
    return collected


def build_third_party_notices() -> list[ThirdPartyNotice]:
    notices: list[ThirdPartyNotice] = []
    distributions = sorted(
        metadata.distributions(),
        key=lambda dist: (str(dist.metadata.get("Name") or "").casefold(), str(dist.version or "")),
    )
    for dist in distributions:
        meta = dist.metadata
        name = (meta.get("Name") or "").strip() or getattr(dist, "name", "") or "unknown"
        version = str(getattr(dist, "version", "") or "")
        notices.append(
            ThirdPartyNotice(
                name=name,
                version=version,
                license=_extract_license(meta),
                homepage=_extract_homepage(meta),
                license_files=_read_license_files(dist),
            )
        )
    return notices


def _notice_to_dict(notice: ThirdPartyNotice) -> dict[str, Any]:
    return asdict(notice)


def _notice_from_dict(data: dict[str, Any]) -> ThirdPartyNotice:
    file_items = data.get("license_files") or []
    files = [
        ThirdPartyLicenseFile(path=str(item.get("path") or ""), text=str(item.get("text") or ""))
        for item in file_items
    ]
    return ThirdPartyNotice(
        name=str(data.get("name") or ""),
        version=str(data.get("version") or ""),
        license=str(data.get("license") or UNKNOWN_LICENSE),
        homepage=str(data.get("homepage") or ""),
        license_files=files,
    )


def get_third_party_notices(force_refresh: bool = False, cache_path: Path | None = None) -> list[ThirdPartyNotice]:
    path = cache_path or (settings.assets_dir / THIRD_PARTY_CACHE_FILENAME)
    if not force_refresh and path.exists():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, list):
                return [_notice_from_dict(item) for item in payload if isinstance(item, dict)]
        except (OSError, json.JSONDecodeError):
            pass

    notices = build_third_party_notices()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps([_notice_to_dict(item) for item in notices], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError:
        pass
    return notices
