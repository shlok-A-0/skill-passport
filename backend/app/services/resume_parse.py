import io
import math
import re
from typing import Any

import pdfplumber
import fitz
from pdfminer.pdfparser import PDFSyntaxError

from app.services.framework import get_skills
from app.services.identity import resume_github_logins

MAX_PDF_PAGES = 50
MAX_CERTIFICATE_OCR_PAGES = 10
MAX_CERTIFICATE_OCR_PIXELS = 16_000_000
MAX_CERTIFICATE_OCR_SIDE = 8192
MAX_CERTIFICATE_BLANK_CHECK_PIXELS = 262_144
MAX_CERTIFICATE_BLANK_CHECK_SIDE = 512


class PdfPageLimitError(Exception):
    pass


def redact_personal_data(text: str, detect_name: bool = True) -> str:
    """Remove personal details (I11). `detect_name` guesses the name from the first lines of a
    whole resume; it is off for single mention lines, where "Python Django" would look like a name.
    Job-title headers are excluded from the name heuristic to avoid redacting role descriptions."""
    redacted = re.sub(
        r"(?im)^([ \t]*(?:(?:full|candidate)[ \t]+)?name[ \t]*[:\-][ \t]*)([^|•·—–;/\n]+)",
        lambda match: f"{match.group(1)}[REDACTED NAME]",
        text,
    )
    patterns = [
        (r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", "[REDACTED EMAIL]"),
        (r"(?<!\w)(?:\+?\d[\d\s().-]{7,}\d)(?!\w)", "[REDACTED PHONE]"),
        (r"\b(?:\d{1,5}\s+[\w.'#-]+\s+(?:street|st|road|rd|avenue|ave|lane|ln|drive|dr|boulevard|blvd)\b[^\n]*)", "[REDACTED ADDRESS]"),
        (r"(?im)^\s*(?:gender|sex|age|date of birth|dob|address|phone|mobile|email)\s*[:\-].*$", "[REDACTED PERSONAL DETAIL]"),
        (r"(?im)^\s*.*\b(?:university|college|institute|school of technology)\b.*$", "[REDACTED EDUCATION INSTITUTION]"),
        (r"(?im)^\s*(?:linkedin|portfolio|website|github)\s*[:\-]\s*\S+.*$", "[REDACTED PERSONAL LINK]"),
    ]
    for pattern, replacement in patterns:
        redacted = re.sub(pattern, replacement, redacted, flags=re.IGNORECASE)
    if not detect_name:
        return redacted

    lines = redacted.splitlines()
    section_headings = {
        "summary", "profile", "objective", "education", "skills",
        "experience", "projects", "contact", "certifications",
    }
    name_pattern = re.compile(
        r"^[A-ZÀ-ÖØ-Þ][\wÀ-ÖØ-öø-ÿ.'-]*(?:\s+[A-ZÀ-ÖØ-Þ][\wÀ-ÖØ-öø-ÿ.'-]*){1,3}$"
    )
    job_title_terms = {
        "analyst", "architect", "consultant", "designer", "developer", "engineer",
        "intern", "manager", "researcher", "scientist", "specialist", "student",
    }
    for index, line in enumerate(lines[:12]):
        candidate = line.strip()
        header = re.sub(r"(?i)^name\s*[:\-]\s*", "", candidate)
        header_name = re.split(
            r"\s*(?:\||•|·|—|–|(?<=\s)-(?=\s)|,|/|;|:|\s+\()\s*",
            header,
            maxsplit=1,
        )[0].strip()
        if (
            candidate.lower() not in section_headings
            and header_name.lower() != "demo data"
            and (
                name_pattern.fullmatch(candidate)
                or (header_name != candidate and name_pattern.fullmatch(header_name))
            )
            and not (set(header_name.lower().split()) & job_title_terms)
            and not any(char in header_name for char in "@:/")
        ):
            return re.sub(
                rf"(?<!\w){re.escape(header_name)}(?!\w)",
                "[REDACTED NAME]",
                "\n".join(lines),
                flags=re.IGNORECASE,
            )
    return "\n".join(lines)


def extract_resume_text(pdf_bytes: bytes) -> str:
    if not pdf_bytes:
        raise ValueError("Resume PDF is empty.")
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            if len(pdf.pages) > MAX_PDF_PAGES:
                raise PdfPageLimitError(f"PDF must have {MAX_PDF_PAGES} pages or fewer.")
            text = "\n".join(page.extract_text(layout=True) or "" for page in pdf.pages)
    except PdfPageLimitError as exc:
        raise ValueError(str(exc)) from exc
    except (pdfplumber.pdf.PdfminerException, PDFSyntaxError, OSError, ValueError) as exc:
        raise ValueError(f"Could not parse resume PDF: {exc}") from exc
    if not text.strip():
        raise ValueError("No readable text found in the resume PDF.")
    return text


# Skill names that are also everyday English words ("ready to go", "react calmly",
# "express ideas"). They only count when written in their capitalised tech form.
COMMON_WORDS = {"go", "react", "express", "torch", "shell"}


def term_span(term: str, line: str) -> tuple[int, int] | None:
    """Where `term` occurs in `line` as a skill mention, or None. Also used by jd_parse."""
    pattern = rf"(?<!\w){re.escape(term)}(?!\w)"
    if term.lower() not in COMMON_WORDS:
        match = re.search(pattern, line, flags=re.IGNORECASE)
        return match.span() if match else None
    pattern = rf"(?<!\w){re.escape(term.capitalize())}(?!\w)"
    for match in re.finditer(pattern, line):  # case-sensitive: "Go", "React", "Express"
        before = line[:match.start()].rstrip()
        after = line[match.end():].lstrip()
        sentence_start = not before or before.endswith((".", "!", "?"))
        listed = not after or after[0] in ",/|;)" or before.endswith((",", ":", "/", "|", "(", "•", "-"))
        if not sentence_start or listed:
            return match.span()
    return None


def _term_in_line(term: str, line: str) -> bool:
    return term_span(term, line) is not None


def skill_terms(skill: dict[str, Any]) -> list[str]:
    """A skill's name and aliases as matched in text ("torch" alone is not PyTorch)."""
    terms = [skill["name"], *skill.get("aliases", [])]
    if skill["id"] == "pytorch":
        terms = [term for term in terms if term.lower() != "torch"]
    return terms


def find_skill_mentions(text: str) -> list[dict[str, Any]]:
    """The first line mentioning each framework skill, with the matched span in that line."""
    found: list[dict[str, Any]] = []
    for skill in get_skills():
        terms = skill_terms(skill)
        hit = None
        for line in text.splitlines():
            for term in terms:
                span = term_span(term, line)
                if span is not None:
                    hit = {"skill_id": skill["id"], "name": skill["name"], "line": line, "span": span}
                    break
            if hit:
                break
        if hit:
            found.append(hit)
    return found


def match_resume_skills(text: str) -> list[dict[str, str]]:
    return [{"skill_id": item["skill_id"], "mention": item["name"]} for item in find_skill_mentions(text)]


def parse_resume(pdf_bytes: bytes) -> dict[str, Any]:
    text = extract_resume_text(pdf_bytes)
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        page_count = len(pdf.pages)
    return {
        "skills": match_resume_skills(text),
        "redacted_text": redact_personal_data(text),
        "page_count": page_count,
        # From the raw text: redaction removes "GitHub: ..." lines. Only usernames are kept.
        "github_logins": resume_github_logins(text),
    }


def extract_certificate_text(pdf_bytes: bytes) -> str:
    """Text of a PDF (hosted OCR for scanned pages) or a PNG/JPEG image (hosted OCR).

    Also used for project reports, which accept the same file types.
    """
    if not pdf_bytes:
        raise ValueError("Certificate file is empty.")
    if pdf_bytes.startswith(b"%PDF-"):
        return _extract_certificate_pdf_text(pdf_bytes)
    if not (pdf_bytes.startswith(b"\x89PNG\r\n\x1a\n") or pdf_bytes.startswith(b"\xff\xd8\xff")):
        raise ValueError("Certificate must be a PDF, PNG, or JPEG image.")
    text = _document_ocr(pdf_bytes)
    if not text.strip():
        raise ValueError("No readable text found in the certificate image.")
    return text


def parse_certificate(pdf_bytes: bytes) -> list[dict[str, str]]:
    return match_resume_skills(extract_certificate_text(pdf_bytes))


def _vision_client():
    """Create the hosted OCR client lazily so text PDFs don't need cloud credentials."""
    try:
        from google.cloud import vision

        return vision.ImageAnnotatorClient()
    except Exception as exc:
        raise RuntimeError(
            "Hosted certificate OCR is not configured. Set up Google Cloud Vision credentials."
        ) from exc


_VISION_CLIENT = None


def _document_ocr(image_bytes: bytes) -> str:
    global _VISION_CLIENT
    if _VISION_CLIENT is None:
        _VISION_CLIENT = _vision_client()
    from google.cloud import vision

    try:
        response = _VISION_CLIENT.document_text_detection(
            image=vision.Image(content=image_bytes), image_context=None
        )
    except Exception as exc:
        raise RuntimeError("Google Cloud Vision could not process a certificate image.") from exc
    if response.error.message:
        raise RuntimeError("Google Cloud Vision could not process a certificate image.")
    return response.full_text_annotation.text or ""


def _extract_certificate_pdf_text(pdf_bytes: bytes) -> str:
    """Extract embedded text locally, using hosted OCR only for image-only PDF pages."""
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            if len(pdf.pages) > MAX_PDF_PAGES:
                raise ValueError(f"PDF must have {MAX_PDF_PAGES} pages or fewer.")
            pages = [page.extract_text(layout=True) or "" for page in pdf.pages]
    except ValueError:
        raise
    except (pdfplumber.pdf.PdfminerException, PDFSyntaxError, OSError) as exc:
        raise ValueError(f"Could not parse certificate PDF: {exc}") from exc

    try:
        with fitz.open(stream=pdf_bytes, filetype="pdf") as document:
            # Check rendered content: inline images and vectorized text may have no image XObjects.
            ocr_indexes = [
                index for index, text in enumerate(pages)
                if not text.strip() and _certificate_page_has_visible_content(document[index])
            ]
            if len(ocr_indexes) > MAX_CERTIFICATE_OCR_PAGES:
                raise ValueError(
                    f"Scanned certificates are limited to {MAX_CERTIFICATE_OCR_PAGES} pages per document."
                )
            for index in ocr_indexes:
                pages[index] = _document_ocr(_render_certificate_page(document[index]))
    except (RuntimeError, ValueError):
        raise
    except Exception as exc:
        raise ValueError("Could not render a scanned certificate PDF for OCR.") from exc
    text = "\n".join(pages)
    if not text.strip():
        raise ValueError("No readable text found in the certificate PDF.")
    return text


def _render_certificate_page(page) -> bytes:
    """Render a PDF page within a bounded pixel budget before allocating its pixmap."""
    scale = _bounded_certificate_render_scale(
        page,
        target_scale=1.5,
        max_pixels=MAX_CERTIFICATE_OCR_PIXELS,
        max_side=MAX_CERTIFICATE_OCR_SIDE,
    )
    pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
    return pixmap.tobytes("jpeg", jpg_quality=85)


def _certificate_page_has_visible_content(page) -> bool:
    """Detect nonblank pages from a bounded thumbnail, including vector-only and inline-image art."""
    scale = _bounded_certificate_render_scale(
        page,
        target_scale=1.0,
        max_pixels=MAX_CERTIFICATE_BLANK_CHECK_PIXELS,
        max_side=MAX_CERTIFICATE_BLANK_CHECK_SIDE,
    )
    pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
    samples = pixmap.samples
    channels = pixmap.n
    if len(samples) <= channels:
        return False
    background = samples[:channels]
    return samples != background * (len(samples) // channels)


def _bounded_certificate_render_scale(
    page,
    *,
    target_scale: float,
    max_pixels: int,
    max_side: int,
) -> float:
    width, height = float(page.rect.width), float(page.rect.height)
    if not math.isfinite(width) or not math.isfinite(height) or width <= 0 or height <= 0:
        raise ValueError("Certificate PDF contains a page with invalid dimensions.")

    scale = min(
        target_scale,
        math.sqrt(max_pixels) / math.sqrt(width) / math.sqrt(height),
        max_side / width,
        max_side / height,
    )
    if not math.isfinite(scale) or scale <= 0:
        raise ValueError("Certificate PDF page is too large to render safely.")
    pixel_width, pixel_height = math.ceil(width * scale), math.ceil(height * scale)
    while (
        pixel_width * pixel_height > max_pixels
        or max(pixel_width, pixel_height) > max_side
    ):
        scale *= 0.99
        pixel_width, pixel_height = math.ceil(width * scale), math.ceil(height * scale)
    return scale
