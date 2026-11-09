"""Validation for Jira identifiers.

Keys are interpolated into URL paths and JQL, so they must be checked before use to
prevent path traversal (``../``) and JQL injection (``X OR project = SECRET``).
"""

import re
from typing import Annotated

from pydantic import StringConstraints

PROJECT_KEY_PATTERN = r"^[A-Za-z][A-Za-z0-9_]{0,254}$"
ISSUE_KEY_PATTERN = r"^[A-Za-z][A-Za-z0-9_]{0,254}-[0-9]{1,18}$"

_PROJECT_KEY_RE = re.compile(PROJECT_KEY_PATTERN)
_ISSUE_KEY_RE = re.compile(ISSUE_KEY_PATTERN)

ProjectKey = Annotated[str, StringConstraints(strip_whitespace=True, pattern=PROJECT_KEY_PATTERN)]
IssueKey = Annotated[str, StringConstraints(strip_whitespace=True, pattern=ISSUE_KEY_PATTERN)]


def validate_project_key(value: str) -> str:
    value = value.strip()
    if not _PROJECT_KEY_RE.match(value):
        raise ValueError(f"Invalid Jira project key: {value!r}")
    return value


def validate_issue_key(value: str) -> str:
    value = value.strip()
    if not _ISSUE_KEY_RE.match(value):
        raise ValueError(f"Invalid Jira issue key: {value!r}")
    return value
