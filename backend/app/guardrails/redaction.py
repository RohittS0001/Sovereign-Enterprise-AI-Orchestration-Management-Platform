"""Mask secrets and sensitive identifiers in text and structured data.

Applied to everything that reaches the LLM and to audit-log details. Secrets,
payment card numbers and government IDs are always masked; email addresses and phone
numbers only when ``include_contact_info`` is set (the platform's models run locally,
and project data often legitimately needs names and addresses).
"""

import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

_SENSITIVE_KEYS = re.compile(
    r"(pass(word)?|secret|token|api[_-]?key|authorization|credential|private[_-]?key)",
    re.IGNORECASE,
)


def _luhn_ok(digits: str) -> bool:
    total, parity = 0, len(digits) % 2
    for index, char in enumerate(digits):
        value = int(char)
        if index % 2 == parity:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


def _card(match: re.Match[str]) -> str | None:
    digits = re.sub(r"\D", "", match.group(0))
    return "[REDACTED:CARD]" if 13 <= len(digits) <= 19 and _luhn_ok(digits) else None


@dataclass(frozen=True)
class _Rule:
    label: str
    pattern: re.Pattern[str]
    # Optional validator returning the replacement, or None to keep the match.
    replace: Callable[[re.Match[str]], str | None] | None = None
    contact: bool = False


_RULES: tuple[_Rule, ...] = (
    _Rule(
        "PRIVATE_KEY",
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
    ),
    _Rule("JWT", re.compile(r"\beyJ[\w-]{10,}\.[\w-]{10,}\.[\w-]{10,}")),
    _Rule("BEARER_TOKEN", re.compile(r"(?i)\bbearer\s+[\w.~+/-]{20,}=*")),
    _Rule("AWS_KEY", re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b")),
    _Rule("GITHUB_TOKEN", re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{36,}|github_pat_\w{22,})\b")),
    _Rule("ATLASSIAN_TOKEN", re.compile(r"\bATATT3[\w=-]{20,}")),
    _Rule(
        "SECRET",
        re.compile(
            r"(?i)\b(api[_-]?key|secret|password|passwd|token)\b(\s*[:=]\s*)[\"']?([^\s\"',;]{6,})"
        ),
        replace=lambda m: f"{m.group(1)}{m.group(2)}[REDACTED:SECRET]",
    ),
    _Rule("CARD", re.compile(r"\b(?:\d[ -]?){12,18}\d\b"), replace=_card),
    _Rule("US_SSN", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    # Not part of a longer digit run (e.g. a 16-digit number that failed the card check).
    _Rule(
        "AADHAAR",
        re.compile(r"(?<!\d)(?<!\d[ -])[2-9]\d{3}[ -]?\d{4}[ -]?\d{4}(?![ -]?\d)"),
    ),
    _Rule("PAN", re.compile(r"\b[A-Z]{3}[ABCFGHLJPT][A-Z]\d{4}[A-Z]\b")),
    _Rule("IBAN", re.compile(r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{4}){3,7}(?:[ ]?[A-Z0-9]{1,3})?\b")),
    _Rule("EMAIL", re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b"), contact=True),
    _Rule("PHONE", re.compile(r"(?<![\w-])\+?\d[\d ().-]{8,}\d\b"), contact=True),
)


@dataclass
class RedactionResult:
    text: str
    counts: Counter[str] = field(default_factory=Counter)

    @property
    def redacted(self) -> bool:
        return bool(self.counts)


def redact_text(text: str, *, include_contact_info: bool = False) -> RedactionResult:
    counts: Counter[str] = Counter()

    for rule in _RULES:
        if rule.contact and not include_contact_info:
            continue

        def substitute(match: re.Match[str], rule: _Rule = rule) -> str:
            replacement = rule.replace(match) if rule.replace else f"[REDACTED:{rule.label}]"
            if replacement is None:
                return match.group(0)
            counts[rule.label] += 1
            return replacement

        text = rule.pattern.sub(substitute, text)

    return RedactionResult(text, counts)


def redact_data(value: Any, *, include_contact_info: bool = False) -> Any:
    """Recursively redact strings in dicts/lists; mask values under sensitive keys."""
    if isinstance(value, str):
        return redact_text(value, include_contact_info=include_contact_info).text
    if isinstance(value, dict):
        return {
            key: (
                "[REDACTED:SECRET]"
                if isinstance(key, str) and _SENSITIVE_KEYS.search(key) and value[key]
                else redact_data(value[key], include_contact_info=include_contact_info)
            )
            for key in value
        }
    if isinstance(value, list | tuple):
        return [redact_data(item, include_contact_info=include_contact_info) for item in value]
    return value
