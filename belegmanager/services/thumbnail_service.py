from __future__ import annotations

from pathlib import Path

import pypdfium2
from PIL import Image, ImageOps


class ThumbnailService:
    def generate(self, source_file: Path, destination: Path) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source_file.suffix.lower() == ".pdf":
            self._from_pdf(source_file, destination)
        else:
            self._from_image(source_file, destination)
        return destination

    def _from_pdf(self, source_file: Path, destination: Path) -> None:
        pdf = pypdfium2.PdfDocument(str(source_file))
        try:
            page = pdf[0]
            bitmap = page.render(scale=1.3)
            pil_image = bitmap.to_pil()
            pil_image.thumbnail((360, 360))
            pil_image.convert("RGB").save(destination, format="JPEG", quality=82)
        finally:
            pdf.close()

    def _from_image(self, source_file: Path, destination: Path) -> None:
        with Image.open(source_file) as image:
            image = ImageOps.exif_transpose(image)
            image.thumbnail((360, 360))
            image.convert("RGB").save(destination, format="JPEG", quality=82)
