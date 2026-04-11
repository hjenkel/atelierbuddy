from pathlib import Path

from belegmanager.utils.storage import is_supported_receipt


def test_supported_receipt_extensions() -> None:
    assert is_supported_receipt(Path("beleg.pdf"))
    assert is_supported_receipt(Path("scan.JPG"))
    assert is_supported_receipt(Path("foto.heic"))
    assert not is_supported_receipt(Path("note.txt"))
