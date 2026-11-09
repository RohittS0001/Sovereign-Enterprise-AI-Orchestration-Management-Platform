"""Split documents into overlapping, heading-aware chunks.

Markdown-style headings start new sections; each chunk records the heading path it
belongs to (used in citations). Long sections are split on paragraph, then sentence,
then word boundaries, with a character overlap between neighbouring chunks.
"""

import re
from dataclasses import dataclass

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True)
class Chunk:
    ordinal: int
    text: str
    heading: str | None
    char_start: int


def _sections(text: str) -> list[tuple[str | None, str, int]]:
    """Return ``(heading_path, body, start_offset)`` for each heading section."""
    sections: list[tuple[str | None, str, int]] = []
    path: list[tuple[int, str]] = []
    buffer: list[str] = []
    start = offset = 0

    def flush() -> None:
        body = "\n".join(buffer).strip()
        if body:
            sections.append((" > ".join(h for _, h in path) or None, body, start))

    for line in text.split("\n"):
        match = _HEADING.match(line)
        if match:
            flush()
            buffer = []
            start = offset
            level = len(match.group(1))
            path = [(lvl, h) for lvl, h in path if lvl < level] + [(level, match.group(2))]
        else:
            buffer.append(line)
        offset += len(line) + 1
    flush()
    return sections


def _split(text: str, size: int) -> list[str]:
    """Greedy packing of paragraphs → sentences → words into pieces ≤ size."""
    if len(text) <= size:
        return [text]
    for separator, units in (
        ("\n\n", text.split("\n\n")),
        (" ", _SENTENCE.split(text)),
        (" ", text.split(" ")),
    ):
        if len(units) == 1:
            continue
        pieces: list[str] = []
        current = ""
        for unit in units:
            unit = unit.strip()
            if not unit:
                continue
            candidate = f"{current}{separator}{unit}" if current else unit
            if len(candidate) <= size:
                current = candidate
                continue
            if current:
                pieces.append(current)
            if len(unit) > size:
                pieces.extend(_split(unit, size))
                current = ""
            else:
                current = unit
        if current:
            pieces.append(current)
        if all(len(p) <= size for p in pieces):
            return pieces
    # A single unbreakable token longer than size.
    return [text[i : i + size] for i in range(0, len(text), size)]


def chunk_text(text: str, *, size: int = 1200, overlap: int = 150) -> list[Chunk]:
    if overlap >= size:
        raise ValueError("overlap must be smaller than chunk size")

    chunks: list[Chunk] = []
    for heading, body, start in _sections(text):
        previous_tail = ""
        for piece in _split(body, size - overlap if overlap else size):
            content = f"{previous_tail} {piece}".strip() if previous_tail else piece
            chunks.append(Chunk(len(chunks), content, heading, start))
            previous_tail = piece[-overlap:] if overlap else ""
            if previous_tail and " " in previous_tail:
                previous_tail = previous_tail.split(" ", 1)[1]  # start on a word boundary
    return chunks
