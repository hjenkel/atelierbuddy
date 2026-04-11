from pathlib import Path

from PIL import Image
import pytest

from belegmanager.utils.storage import is_supported_receipt, validate_receipt_file


def test_supported_receipt_extensions() -> None:
    assert is_supported_receipt(Path("beleg.pdf"))
    assert is_supported_receipt(Path("scan.JPG"))
    assert is_supported_receipt(Path("foto.heic"))
    assert not is_supported_receipt(Path("note.txt"))


def test_validate_receipt_file_accepts_valid_pdf(tmp_path: Path) -> None:
    candidate = tmp_path / "valid.pdf"
    candidate.write_bytes(b"%PDF-1.4\n%test")
    validate_receipt_file(candidate)


def test_validate_receipt_file_rejects_invalid_pdf_payload(tmp_path: Path) -> None:
    candidate = tmp_path / "invalid.pdf"
    candidate.write_bytes(b"not a real pdf")
    with pytest.raises(ValueError):
        validate_receipt_file(candidate)


def test_validate_receipt_file_rejects_invalid_image_payload(tmp_path: Path) -> None:
    candidate = tmp_path / "invalid.png"
    candidate.write_bytes(b"not an image")
    with pytest.raises(ValueError):
        validate_receipt_file(candidate)


def test_validate_receipt_file_rejects_oversized_file(tmp_path: Path) -> None:
    candidate = tmp_path / "huge.pdf"
    candidate.write_bytes(b"%PDF-1.4\n")
    with candidate.open("ab") as file_handle:
        file_handle.truncate(26 * 1024 * 1024)
    with pytest.raises(ValueError):
        validate_receipt_file(candidate)


def test_validate_receipt_file_rejects_symlink(tmp_path: Path) -> None:
    target = tmp_path / "source.pdf"
    target.write_bytes(b"%PDF-1.4\n")
    link = tmp_path / "link.pdf"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlink not supported in this environment")
    with pytest.raises(ValueError):
        validate_receipt_file(link)


def test_validate_receipt_file_accepts_valid_image(tmp_path: Path) -> None:
    candidate = tmp_path / "valid.png"
    Image.new("RGB", (10, 10), "white").save(candidate, format="PNG")
    validate_receipt_file(candidate)
