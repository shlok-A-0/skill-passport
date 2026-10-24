import math
import sys
from pathlib import Path
from types import SimpleNamespace

import fitz
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import resume_parse


def _image_bytes(image_format: str = "png") -> bytes:
    pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 4, 4), 0)
    pixmap.clear_with(255)
    for x in range(4):
        pixmap.set_pixel(x, 2, (0, 0, 0))
    return pixmap.tobytes(image_format)


def _pdf(*page_types: str, width: float = 300, height: float = 200) -> bytes:
    image = _image_bytes()
    with fitz.open() as document:
        for page_type in page_types:
            page = document.new_page(width=width, height=height)
            if page_type == "text":
                page.insert_text((20, 40), "Python AWS")
            elif page_type == "scan":
                page.insert_image(page.rect, stream=image)
            elif page_type == "vector":
                # Draw a letter-shaped path rather than text or an embedded image.
                page.draw_line((30, 90), (50, 30), width=4)
                page.draw_line((50, 30), (70, 90), width=4)
                page.draw_line((38, 65), (62, 65), width=4)
        return document.tobytes()


def test_certificate_image_uploads_use_local_tesseract(monkeypatch) -> None:
    received = []
    monkeypatch.setattr(
        resume_parse.subprocess, "run",
        lambda command, **kwargs: received.append((command, kwargs["input"])) or SimpleNamespace(stdout=b"Python AWS"),
    )

    for image_format in ("png", "jpeg"):
        image = _image_bytes(image_format)
        skills = resume_parse.parse_certificate(image)
        assert {skill["skill_id"] for skill in skills} >= {"python", "aws"}
        assert received[-1][0] == ["tesseract", "stdin", "stdout"]
        assert received[-1][1] == image


def test_scanned_pdf_ocr_only_sends_pages_with_embedded_images(monkeypatch) -> None:
    received = []
    monkeypatch.setattr(resume_parse, "_document_ocr", lambda image: received.append(image) or "Python AWS")

    skills = resume_parse.parse_certificate(_pdf("scan", "blank"))

    assert {skill["skill_id"] for skill in skills} >= {"python", "aws"}
    assert len(received) == 1
    assert received[0].startswith(b"\xff\xd8\xff")


def test_vector_only_certificate_page_is_sent_to_ocr(monkeypatch) -> None:
    received = []
    monkeypatch.setattr(resume_parse, "_document_ocr", lambda image: received.append(image) or "Python AWS")

    skills = resume_parse.parse_certificate(_pdf("vector"))

    assert {skill["skill_id"] for skill in skills} >= {"python", "aws"}
    assert len(received) == 1


def test_text_pdf_with_blank_page_does_not_call_ocr(monkeypatch) -> None:
    monkeypatch.setattr(resume_parse, "_document_ocr", lambda _image: pytest.fail("OCR called for text-only PDF"))
    skills = resume_parse.parse_certificate(_pdf("text", "blank"))

    assert {skill["skill_id"] for skill in skills} >= {"python", "aws"}


def test_certificate_ocr_page_cap_rejects_before_calling_ocr(monkeypatch) -> None:
    monkeypatch.setattr(resume_parse, "_document_ocr", lambda _image: pytest.fail("OCR called beyond page cap"))
    pdf_bytes = _pdf(*(["scan"] * (resume_parse.MAX_CERTIFICATE_OCR_PAGES + 1)))

    with pytest.raises(ValueError, match="Scanned certificates are limited"):
        resume_parse.parse_certificate(pdf_bytes)


def test_tesseract_errors_are_reported_for_image_uploads(monkeypatch) -> None:
    def fail_ocr(_image: bytes) -> str:
        raise RuntimeError("Local Tesseract OCR could not process this image.")

    monkeypatch.setattr(resume_parse, "_document_ocr", fail_ocr)
    with pytest.raises(RuntimeError, match="Local Tesseract OCR"):
        resume_parse.parse_certificate(_image_bytes())


def test_missing_tesseract_binary_has_actionable_error(monkeypatch) -> None:
    def missing_binary(*_args, **_kwargs):
        raise FileNotFoundError("tesseract")

    monkeypatch.setattr(resume_parse.subprocess, "run", missing_binary)

    with pytest.raises(RuntimeError, match="installed and available on PATH"):
        resume_parse._document_ocr(_image_bytes())


def test_image_ocr_rejects_oversized_images_before_recognition(monkeypatch) -> None:
    image = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 4, 4), 0)
    image.clear_with(255)
    image_bytes = image.tobytes("png")
    monkeypatch.setattr(resume_parse, "MAX_CERTIFICATE_OCR_PIXELS", 4)
    monkeypatch.setattr(resume_parse.subprocess, "run", lambda *_args, **_kwargs: pytest.fail("OCR called"))

    with pytest.raises(ValueError, match="too large for OCR"):
        resume_parse._document_ocr(image_bytes)


def test_oversized_pdf_page_is_scaled_before_pixmap_allocation() -> None:
    captured = {}

    class FakePage:
        rect = SimpleNamespace(width=100_000, height=100_000)

        def get_pixmap(self, *, matrix, alpha):
            captured["scale"] = matrix.a
            captured["alpha"] = alpha
            return SimpleNamespace(tobytes=lambda *_args, **_kwargs: b"bounded-render")

    assert resume_parse._render_certificate_page(FakePage()) == b"bounded-render"
    width = math.ceil(100_000 * captured["scale"])
    height = math.ceil(100_000 * captured["scale"])
    assert width * height <= resume_parse.MAX_CERTIFICATE_OCR_PIXELS
    assert max(width, height) <= resume_parse.MAX_CERTIFICATE_OCR_SIDE
    assert captured["alpha"] is False
