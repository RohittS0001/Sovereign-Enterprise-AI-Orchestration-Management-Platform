"""Extract plain text (keeping headings as Markdown) from uploaded files."""

import io
import re
from html.parser import HTMLParser
from pathlib import PurePath

from backend.app.core.errors import AppException


class UnsupportedDocumentError(AppException):
    status_code = 415
    code = "unsupported_document"


SUPPORTED_EXTENSIONS = {".txt", ".md", ".markdown", ".pdf", ".docx", ".html", ".htm"}


class _HTMLText(HTMLParser):
    _BLOCK = {"p", "div", "li", "tr", "br", "section", "article", "table"}
    _SKIP = {"script", "style", "noscript", "head"}

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip_depth = 0
        self._heading: str | None = None

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in self._SKIP:
            self._skip_depth += 1
        elif re.fullmatch(r"h[1-6]", tag):
            self._heading = tag
            self.parts.append("\n\n" + "#" * int(tag[1]) + " ")
        elif tag in self._BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP and self._skip_depth:
            self._skip_depth -= 1
        elif tag == self._heading:
            self._heading = None
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self.parts.append(data)


def _normalize(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _pdf(data: bytes) -> str:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise UnsupportedDocumentError("Encrypted PDFs are not supported")
        return "\n\n".join(page.extract_text() or "" for page in reader.pages)
    except PdfReadError as exc:
        raise UnsupportedDocumentError("The PDF could not be read") from exc


def _docx(data: bytes) -> str:
    from docx import Document as DocxDocument

    try:
        document = DocxDocument(io.BytesIO(data))
    except Exception as exc:  # python-docx raises several unrelated types
        raise UnsupportedDocumentError("The DOCX file could not be read") from exc

    lines = []
    for paragraph in document.paragraphs:
        text = paragraph.text.strip()
        if not text:
            continue
        style = (paragraph.style.name or "").lower() if paragraph.style is not None else ""
        if style.startswith("heading"):
            level = int(style.split()[-1]) if style.split()[-1].isdigit() else 1
            lines.append(f"{'#' * min(level, 6)} {text}")
        else:
            lines.append(text)
    for table in document.tables:
        for row in table.rows:
            lines.append(" | ".join(cell.text.strip() for cell in row.cells))
    return "\n\n".join(lines)


def extract_text(filename: str, data: bytes) -> str:
    extension = PurePath(filename).suffix.lower()
    if extension not in SUPPORTED_EXTENSIONS:
        raise UnsupportedDocumentError(
            f"Unsupported file type {extension or '(none)'}; "
            f"supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
        )

    if extension == ".pdf":
        text = _pdf(data)
    elif extension == ".docx":
        text = _docx(data)
    else:
        try:
            raw = data.decode("utf-8")
        except UnicodeDecodeError:
            raw = data.decode("latin-1")
        if extension in {".html", ".htm"}:
            parser = _HTMLText()
            parser.feed(raw)
            text = "".join(parser.parts)
        else:
            text = raw

    text = _normalize(text)
    if not text:
        raise UnsupportedDocumentError("No text could be extracted (scanned PDFs need OCR)")
    return text
