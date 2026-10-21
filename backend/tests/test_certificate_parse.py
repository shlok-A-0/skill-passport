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
    pixmap.clear_with(220)
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
        return document.tobytes()


def test_certificate_image_uploads_use_mocked_vision(monkeypatch) -> None:
    received = []
    monkeypatch.setattr(resume_parse, "_document_ocr", lambda image: received.append(image) or "Python AWS")

    for image_format in ("png", "jpeg"):
        image = _image_bytes(image_format)
        skills = resume_parse.parse_certificate(image)
        assert {skill["skill_id"] for skill in skills} >= {"python", "aws"}
        assert received[-1] == image


def test_scanned_pdf_ocr_only_sends_pages_with_embedded_images(monkeypatch) -> None:
    received = []
    monkeypatch.setattr(resume_parse, "_document_ocr", lambda image: received.append(image) or "Python AWS")

    skills = resume_parse.parse_certificate(_pdf("scan", "blank"))

    assert {skill["skill_id"] for skill in skills} >= {"python", "aws"}
    assert len(received) == 1
    assert received[0].startswith(b"\xff\xd8\xff")


def test_text_pdf_with_blank_page_does_not_require_vision(monkeypatch) -> None:
    def vision_unavailable():
        raise AssertionError("Vision should not be initialized for text or blank pages")

    monkeypatch.setattr(resume_parse, "_VISION_CLIENT", None)
    monkeypatch.setattr(resume_parse, "_vision_client", vision_unavailable)

    skills = resume_parse.parse_certificate(_pdf("text", "blank"))

    assert {skill["skill_id"] for skill in skills} >= {"python", "aws"}


def test_certificate_ocr_page_cap_rejects_before_calling_vision(monkeypatch) -> None:
    monkeypatch.setattr(resume_parse, "_document_ocr", lambda _image: pytest.fail("Vision called beyond page cap"))
    pdf_bytes = _pdf(*(["scan"] * (resume_parse.MAX_CERTIFICATE_OCR_PAGES + 1)))

    with pytest.raises(ValueError, match="Scanned certificates are limited"):
        resume_parse.parse_certificate(pdf_bytes)


def test_vision_errors_are_reported_for_image_uploads(monkeypatch) -> None:
    def fail_ocr(_image: bytes) -> str:
        raise RuntimeError("Google Cloud Vision could not process a certificate image.")

    monkeypatch.setattr(resume_parse, "_document_ocr", fail_ocr)
    with pytest.raises(RuntimeError, match="Google Cloud Vision"):
        resume_parse.parse_certificate(_image_bytes())


def test_document_ocr_uses_vision_client_and_surfaces_provider_errors(monkeypatch) -> None:
    try:
        from google.cloud import vision
    except ImportError:
        pytest.skip("Google Cloud Vision dependency is not installed")

    class FakeClient:
        def document_text_detection(self, *, image, image_context):
            assert image.content == b"mock-image"
            assert image_context is None
            return SimpleNamespace(
                error=SimpleNamespace(message="quota exceeded"),
                full_text_annotation=SimpleNamespace(text=""),
            )

    monkeypatch.setattr(resume_parse, "_VISION_CLIENT", FakeClient())
    with pytest.raises(RuntimeError, match="Google Cloud Vision"):
        resume_parse._document_ocr(b"mock-image")


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
