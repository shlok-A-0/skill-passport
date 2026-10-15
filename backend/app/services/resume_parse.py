import io
import re
from typing import Any

import pdfplumber
from pdfminer.pdfparser import PDFSyntaxError

from app.services.framework import get_skills


def redact_personal_data(text: str) -> str:
    redacted = text
    patterns = [
        (r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", "[REDACTED EMAIL]"),
        (r"(?<!\w)(?:\+?\d[\d\s().-]{7,}\d)(?!\w)", "[REDACTED PHONE]"),
        (r"\b(?:\d{1,5}\s+[\w.'#-]+\s+(?:street|st|road|rd|avenue|ave|lane|ln|drive|dr|boulevard|blvd)\b[^\n]*)", "[REDACTED ADDRESS]"),
        (r"(?im)^\s*(?:name|gender|sex|age|date of birth|dob|address|phone|mobile|email)\s*[:\-].*$", "[REDACTED PERSONAL DETAIL]"),
        (r"(?im)^\s*.*\b(?:university|college|institute|school of technology)\b.*$", "[REDACTED EDUCATION INSTITUTION]"),
        (r"(?im)^\s*(?:linkedin|portfolio|website|github)\s*[:\-]\s*\S+.*$", "[REDACTED PERSONAL LINK]"),
    ]
    for pattern, replacement in patterns:
        redacted = re.sub(pattern, replacement, redacted, flags=re.IGNORECASE)

    lines = redacted.splitlines()
    section_headings = {
        "summary", "profile", "objective", "education", "skills",
        "experience", "projects", "contact", "certifications",
    }
    name_pattern = re.compile(
        r"^[A-ZÀ-ÖØ-Þ][\wÀ-ÖØ-öø-ÿ.'-]*(?:\s+[A-ZÀ-ÖØ-Þ][\wÀ-ÖØ-öø-ÿ.'-]*){1,3}$"
    )
    for index, line in enumerate(lines[:8]):
        candidate = line.strip()
        if (
            candidate.lower() not in section_headings
            and name_pattern.fullmatch(candidate)
            and not any(char in candidate for char in "@:/")
        ):
            lines[index] = "[REDACTED NAME]"
            break
    return "\n".join(lines)


def extract_resume_text(pdf_bytes: bytes) -> str:
    if not pdf_bytes:
        raise ValueError("Resume PDF is empty.")
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            text = "\n".join(page.extract_text(layout=True) or "" for page in pdf.pages)
    except (pdfplumber.pdf.PdfminerException, PDFSyntaxError, OSError, ValueError) as exc:
        raise ValueError(f"Could not parse resume PDF: {exc}") from exc
    if not text.strip():
        raise ValueError("No readable text found in the resume PDF.")
    return text


# Skill names that are also everyday English words ("ready to go", "react calmly",
# "express ideas"). They only count when written in their capitalised tech form.
COMMON_WORDS = {"go", "react", "express", "torch", "shell"}


def _term_in_line(term: str, line: str) -> bool:
    pattern = rf"(?<!\w){re.escape(term)}(?!\w)"
    if term.lower() not in COMMON_WORDS:
        return bool(re.search(pattern, line, flags=re.IGNORECASE))
    pattern = rf"(?<!\w){re.escape(term.capitalize())}(?!\w)"
    for match in re.finditer(pattern, line):  # case-sensitive: "Go", "React", "Express"
        before = line[:match.start()].rstrip()
        after = line[match.end():].lstrip()
        sentence_start = not before or before.endswith((".", "!", "?"))
        listed = not after or after[0] in ",/|;)" or before.endswith((",", ":", "/", "|", "(", "•", "-"))
        if not sentence_start or listed:
            return True
    return False


def match_resume_skills(text: str) -> list[dict[str, str]]:
    skills = get_skills()
    matches: list[dict[str, str]] = []
    for skill in skills:
        terms = [skill["name"], *skill.get("aliases", [])]
        if skill["id"] == "pytorch":
            terms = [term for term in terms if term.lower() != "torch"]
        found_line = None
        for line in text.splitlines():
            for term in terms:
                if _term_in_line(term, line):
                    found_line = line.strip()
                    break
            if found_line:
                break
        if found_line:
            matches.append({"skill_id": skill["id"], "mention": found_line})
    return matches


def parse_resume(pdf_bytes: bytes) -> dict[str, Any]:
    text = extract_resume_text(pdf_bytes)
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        page_count = len(pdf.pages)
    return {
        "skills": match_resume_skills(text),
        "redacted_text": redact_personal_data(text),
        "page_count": page_count,
    }


def parse_certificate(pdf_bytes: bytes) -> list[dict[str, str]]:
    return match_resume_skills(extract_resume_text(pdf_bytes))
