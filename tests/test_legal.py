from __future__ import annotations

from pathlib import Path

from belegmanager.legal import (
    APP_COPYRIGHT,
    APP_LICENSE_ID,
    ThirdPartyLicenseFile,
    UNKNOWN_LICENSE,
    ThirdPartyNotice,
    get_third_party_notices,
)


def test_legal_constants() -> None:
    assert APP_LICENSE_ID == "AGPL-3.0-or-later"
    assert APP_COPYRIGHT == "Copyright (c) 2026 Hanno Jenkel"


def test_notices_scan_and_cache_write(tmp_path: Path) -> None:
    cache_path = tmp_path / "third_party_notices.json"
    notices = get_third_party_notices(force_refresh=True, cache_path=cache_path)
    assert notices
    assert cache_path.exists()
    assert any(item.name.lower() == "belegmanager" for item in notices)


def test_notices_cache_read(tmp_path: Path) -> None:
    cache_path = tmp_path / "third_party_notices.json"
    cache_path.write_text(
        """
[
  {
    "name": "demo",
    "version": "1.0.0",
    "license": "MIT",
    "homepage": "https://example.com",
    "license_files": [
      {
        "path": "demo-1.0.0.dist-info/LICENSE",
        "text": "demo license text"
      }
    ]
  }
]
""".strip(),
        encoding="utf-8",
    )
    notices = get_third_party_notices(force_refresh=False, cache_path=cache_path)
    assert notices == [
        ThirdPartyNotice(
            name="demo",
            version="1.0.0",
            license="MIT",
            homepage="https://example.com",
            license_files=[ThirdPartyLicenseFile(path="demo-1.0.0.dist-info/LICENSE", text="demo license text")],
        )
    ]


def test_unknown_license_fallback(tmp_path: Path) -> None:
    cache_path = tmp_path / "third_party_notices.json"
    cache_path.write_text(
        """
[{"name":"x","version":"0","license":"","homepage":"","license_files":[]}]
""".strip(),
        encoding="utf-8",
    )
    notices = get_third_party_notices(force_refresh=False, cache_path=cache_path)
    assert notices[0].license == UNKNOWN_LICENSE
