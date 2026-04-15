import asyncio
from pathlib import Path
import shutil

from PIL import Image
import pytest

from belegmanager.config import settings
from belegmanager.utils.storage import is_supported_receipt, save_uploaded_order_invoice, validate_receipt_file


class _FakeUpload:
    def __init__(self, *, name: str, payload: bytes) -> None:
        self.name = name
        self._payload = payload

    def size(self) -> int:
        return len(self._payload)

    async def save(self, destination: Path) -> None:
        destination.write_bytes(self._payload)


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


def test_save_uploaded_order_invoice_stores_supported_file(tmp_path: Path) -> None:
    old_invoice_dir = settings.order_invoices_dir
    object.__setattr__(settings, "order_invoices_dir", tmp_path / "order_invoices")
    try:
        upload = _FakeUpload(name="rechnung.pdf", payload=b"%PDF-1.4\n%test")

        saved_path = asyncio.run(save_uploaded_order_invoice(upload, order_id=17))

        assert saved_path.exists()
        assert saved_path.read_bytes() == b"%PDF-1.4\n%test"
        assert saved_path.name.startswith("order_17_")
        assert saved_path.suffix == ".pdf"
        saved_path.relative_to(settings.order_invoices_dir)
    finally:
        shutil.rmtree(settings.order_invoices_dir, ignore_errors=True)
        object.__setattr__(settings, "order_invoices_dir", old_invoice_dir)


def test_save_uploaded_order_invoice_rejects_invalid_payload(tmp_path: Path) -> None:
    old_invoice_dir = settings.order_invoices_dir
    object.__setattr__(settings, "order_invoices_dir", tmp_path / "order_invoices")
    try:
        upload = _FakeUpload(name="rechnung.pdf", payload=b"not a real pdf")

        with pytest.raises(ValueError):
            asyncio.run(save_uploaded_order_invoice(upload, order_id=17))

        assert not [path for path in settings.order_invoices_dir.rglob("*") if path.is_file()]
    finally:
        shutil.rmtree(settings.order_invoices_dir, ignore_errors=True)
        object.__setattr__(settings, "order_invoices_dir", old_invoice_dir)
