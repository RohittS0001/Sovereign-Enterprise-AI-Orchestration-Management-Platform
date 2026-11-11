"""Why is the project late? Days lost per cause, and what was reported vs what happened.

Evidence, not opinion: every number traces to a Jira status change, a comment, a pull
request, a build, a leave record, an incident or an e-mail.

**Time lost** is counted in *person working days* inside the sprint (start → today):

- Blocked — days an issue sat in a blocked status. Classified as *external* when the
  issue's comments or e-mails mention a vendor/partner/customer, else *internal*.
- Started late — high-priority work first moved to "in progress" days after the sprint
  began (or not started at all), minus a one-day grace.
- Waiting for code review — pull request age beyond a one-day review agreement.
- Rework — time after an issue was sent backwards (review/done → in progress).
- Scope added mid-sprint — issues created after the sprint started, in points and days.
- Leave — approved leave of people holding open work.
- Production incidents — days incidents stayed open inside the window.

Causes overlap (a blocked issue can also have a late PR), so the shares rank causes; they
are not an exact decomposition of the slip. **Reported vs actual** extracts claims ("on
track", "no blockers", "done by <date>") from comments and e-mails with plain rules and
checks each one against the data *as it was on the day the claim was made*.
"""

import asyncio
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Literal

from pydantic import BaseModel, Field

from backend.app.domains.analytics.forecast import working_days
from backend.app.domains.analytics.snapshot import (
    ISSUE_KEY,
    IssueFacts,
    ProjectSnapshot,
    parse_date,
)
from backend.app.domains.delivery.roadmap import Roadmap
from backend.app.domains.governance.guardrails.redaction import redact_text

BLOCKED = {"blocked", "on hold", "impeded", "waiting"}
IN_PROGRESS = {"in progress", "in development", "doing", "started"}
LATER_STAGES = {"in review", "review", "code review", "qa", "testing", "done", "closed", "resolved"}
EXTERNAL = re.compile(
    r"\b(vendor|supplier|third[- ]party|partner|customer|client|external|upstream|their side)\b",
    re.I,
)
REVIEW_GRACE_DAYS = 1
WAITING = re.compile(r"(blocked|waiting|until|depends on|dependency)", re.I)
START_GRACE_DAYS = 1
MAX_ISSUES = 50

Cause = Literal[
    "blocked_external",
    "blocked_internal",
    "started_late",
    "review_wait",
    "rework",
    "scope_added",
    "leave",
    "incidents",
]
CAUSE_LABELS: dict[str, str] = {
    "blocked_external": "Waiting on an external party",
    "blocked_internal": "Blocked inside the team",
    "started_late": "High-priority work started late",
    "review_wait": "Waiting for code review",
    "rework": "Rework (work sent back)",
    "scope_added": "Scope added mid-sprint",
    "leave": "Leave and absence",
    "incidents": "Production incidents",
}


# ------------------------------------------------------------------ evidence collected


@dataclass
class StatusChange:
    at: date
    author: str | None
    from_status: str
    to_status: str


@dataclass
class Note:
    """A comment or an e-mail: where people *say* what is happening."""

    at: date | None
    author: str | None
    text: str
    source: str  # "jira comment" | "email"
    ref: str  # issue key or e-mail subject
    issue_keys: list[str] = field(default_factory=list)


@dataclass
class DelayFacts:
    history: dict[str, list[StatusChange]] = field(default_factory=dict)
    notes: list[Note] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ------------------------------------------------------------------ results


class Segment(BaseModel):
    status: str
    start: date
    end: date
    days: int


class Loss(BaseModel):
    cause: str
    label: str
    days: float = Field(description="Person working days lost")
    share: float = Field(description="Share of all days lost (causes can overlap)")
    issues: list[str]
    evidence: list[str]


class IssueStory(BaseModel):
    key: str
    summary: str
    assignee: str | None
    status: str
    days_lost: float
    segments: list[Segment]
    events: list[str]


class ClaimCheck(BaseModel):
    claim: str
    source: str
    said_by: str | None
    said_on: date | None
    verdict: Literal["contradicted", "supported", "unverifiable"]
    reality: list[str]


class DelayReport(BaseModel):
    project: str
    as_of: date
    sprint: str | None
    sprint_start: date | None
    target: date | None
    projected_finish: date | None
    slip_days: int = Field(description="Working days the projected finish is past the target")
    headline: str
    total_days_lost: float
    losses: list[Loss]
    stories: list[IssueStory]
    claims: list[ClaimCheck]
    warnings: list[str]


# ------------------------------------------------------------------ collection


def parse_history(raw: dict[str, Any]) -> list[StatusChange]:
    """Jira changelog (`values` or `histories`) → status changes, oldest first."""
    changes = []
    for entry in raw.get("values") or raw.get("histories") or []:
        at = parse_date(entry.get("created"))
        author = entry.get("author")
        name = author.get("displayName") if isinstance(author, dict) else author
        for item in entry.get("items") or []:
            if (item.get("field") or "").lower() == "status" and at:
                changes.append(
                    StatusChange(at, name, item.get("fromString") or "", item.get("toString") or "")
                )
    return sorted(changes, key=lambda c: c.at)


def _comment_text(body: Any) -> str:
    if isinstance(body, str):
        return body
    from backend.app.domains.agent.observations import adf_to_text

    return adf_to_text(body)


async def collect_delay_facts(
    hub: Any, snapshot: ProjectSnapshot, email_days: int = 45, include_email: bool = True
) -> DelayFacts:
    """Status history and comments for in-scope issues, plus e-mails naming the project."""
    facts = DelayFacts()
    scope = (snapshot.sprint_issues or snapshot.issues)[:MAX_ISSUES]
    jira = getattr(hub, "jira", None)
    if jira is not None and scope:
        limit = asyncio.Semaphore(5)

        async def one(issue: IssueFacts) -> None:
            async with limit:
                try:
                    history, comments = await asyncio.gather(
                        jira.get_issue_history(issue.key), jira.get_issue_comments(issue.key)
                    )
                except Exception as exc:  # noqa: BLE001 - one issue failing is a warning
                    facts.warnings.append(f"Jira history for {issue.key} unavailable: {exc}")
                    return
            facts.history[issue.key] = parse_history(history)
            for c in comments.get("comments") or []:
                author = c.get("author")
                text = redact_text(_comment_text(c.get("body"))).text[:1000]
                facts.notes.append(
                    Note(
                        at=parse_date(c.get("created")),
                        author=author.get("displayName") if isinstance(author, dict) else author,
                        text=text,
                        source="jira comment",
                        ref=issue.key,
                        issue_keys=sorted({issue.key, *ISSUE_KEY.findall(text.upper())}),
                    )
                )

        await asyncio.gather(*(one(i) for i in scope))
    elif jira is None:
        facts.warnings.append("Jira is not connected: no status history.")

    email = getattr(hub, "email", None) if include_email else None
    if email is not None:
        try:
            facts.notes += await asyncio.to_thread(_emails, email, snapshot.key, email_days)
        except Exception as exc:  # noqa: BLE001 - e-mail is optional evidence
            facts.warnings.append(f"E-mail unavailable: {exc}")

    # Project chat channels: where people say how things really are.
    since = snapshot.today - timedelta(days=email_days)
    for source, service in (hub.by_category("chat") if hasattr(hub, "by_category") else {}).items():
        link = "slack_channels" if source == "slack" else "teams_channels"
        if not callable(getattr(service, "list_messages", None)):
            continue
        for channel in snapshot.links.get(link, []):
            try:
                messages = await service.list_messages(channel, since)
            except Exception as exc:  # noqa: BLE001 - chat is optional evidence
                facts.warnings.append(f"{source} channel {channel} unavailable: {exc}")
                continue
            for message in messages:
                text = redact_text(message.text).text[:1000]
                facts.notes.append(
                    Note(
                        at=message.sent_at.date(),
                        author=message.author,
                        text=text,
                        source=f"{source} message",
                        ref=f"#{channel}",
                        issue_keys=sorted(set(ISSUE_KEY.findall(text.upper()))),
                    )
                )
    return facts


def _emails(service: Any, project_key: str, days: int) -> list[Note]:
    from backend.app.tools.gmail.tools import EmailTools

    found = service.search_messages(f"{project_key} newer_than:{days}d", max_results=15)
    notes = []
    for ref in found.get("messages") or []:
        message = service.get_message(ref["id"])
        headers = {
            h["name"].lower(): h["value"] for h in message.get("payload", {}).get("headers", [])
        }
        body = EmailTools._extract_body(message.get("payload", {})) or message.get("snippet", "")
        text = redact_text(body).text[:2000]
        notes.append(
            Note(
                at=parse_date(headers.get("date")),
                author=headers.get("from"),
                text=text,
                source="email",
                ref=headers.get("subject") or ref["id"],
                issue_keys=sorted(set(ISSUE_KEY.findall(text.upper()))),
            )
        )
    return notes


# ------------------------------------------------------------------ analysis helpers


def _days(start: date, end: date) -> int:
    """Working days in [start, end)."""
    return working_days(start, end - timedelta(days=1)) if end > start else 0


def segments(
    issue: IssueFacts, changes: list[StatusChange], window_start: date, today: date
) -> list[Segment]:
    """Status timeline clipped to [window_start, today]."""
    result: list[Segment] = []
    first_status = changes[0].from_status if changes else issue.status
    current, since = first_status, (issue.created or window_start)
    for change in changes:
        if change.at > since:
            result.append(Segment(status=current, start=since, end=change.at, days=0))
        current, since = change.to_status, change.at
    result.append(Segment(status=current, start=since, end=today, days=0))
    clipped = []
    for s in result:
        start, end = max(s.start, window_start), min(s.end, today)
        if end > start or (s is result[-1] and start <= end):
            clipped.append(Segment(status=s.status, start=start, end=end, days=_days(start, end)))
    return clipped


def _status_on(changes: list[StatusChange], issue: IssueFacts, day: date) -> str:
    status = changes[0].from_status if changes else issue.status
    for change in changes:
        if change.at <= day:
            status = change.to_status
    return status


def _add(
    losses: dict[str, dict], cause: str, days: float, issue: str | None, evidence: str
) -> None:
    if days <= 0:
        return
    bucket = losses.setdefault(cause, {"days": 0.0, "issues": set(), "evidence": []})
    bucket["days"] += days
    if issue:
        bucket["issues"].add(issue)
    bucket["evidence"].append(evidence)


# ------------------------------------------------------------------ claims

_ON_TRACK = re.compile(r"\b(on track|on schedule|as planned|all green|going well)\b", re.I)
_NO_BLOCKERS = re.compile(r"\bno (major |real )?(blockers?|issues|risks?)\b", re.I)
_DONE_BY = re.compile(
    r"\b(?:done|ready|live|shipped|delivered|complete[d]?|finished|release[d]?)\s+"
    r"(?:by|on|before)\s+([^.;,\n]{3,30})",
    re.I,
)
_RELEASE_ON = re.compile(
    r"\b(?:release|go-live|launch|ship)\w*\s+(?:on|for|by)\s+([^.;,\n]{3,30})", re.I
)
_MONTHS = {
    m: i
    for i, m in enumerate(
        ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"],
        start=1,
    )
}
_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def parse_when(text: str, said_on: date) -> date | None:
    """ISO dates, "14 Nov", "Nov 14", "Friday", "tomorrow" (relative to when it was said)."""
    text = text.strip().lower()
    if m := re.search(r"(\d{4})-(\d{2})-(\d{2})", text):
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    m = re.search(r"(\d{1,2})(?:st|nd|rd|th)?\s+([a-z]{3})", text) or re.search(
        r"([a-z]{3})[a-z]*\s+(\d{1,2})", text
    )
    if m:
        day, month = (m.group(1), m.group(2)) if m.group(1).isdigit() else (m.group(2), m.group(1))
        if month[:3] in _MONTHS:
            try:
                guess = date(said_on.year, _MONTHS[month[:3]], int(day))
            except ValueError:
                return None
            return (
                guess
                if guess >= said_on - timedelta(days=60)
                else guess.replace(year=guess.year + 1)
            )
    if "tomorrow" in text:
        return said_on + timedelta(days=1)
    for index, name in enumerate(_WEEKDAYS):
        if name in text:
            return said_on + timedelta(days=(index - said_on.weekday()) % 7 or 7)
    if "end of week" in text or "eow" in text:
        return said_on + timedelta(days=(4 - said_on.weekday()) % 7)
    return None


def check_claims(
    notes: list[Note],
    snapshot: ProjectSnapshot,
    facts: DelayFacts,
    roadmap: Roadmap | None,
    target: date | None,
) -> list[ClaimCheck]:
    finish_by_key = {i.key: i.finish for i in roadmap.items} if roadmap else {}
    issues = {i.key: i for i in snapshot.issues}
    checks: list[ClaimCheck] = []
    for note in notes:
        said_on = note.at or snapshot.today
        sentences = re.split(r"(?<=[.!?])\s+|\n", note.text)
        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue
            reality: list[str] = []
            verdict = "unverifiable"
            if _NO_BLOCKERS.search(sentence):
                blocked = [
                    k
                    for k, i in issues.items()
                    if i.key in facts.history
                    and _status_on(facts.history[k], i, said_on).lower() in BLOCKED
                ]
                if blocked:
                    verdict = "contradicted"
                    reality.append(
                        f"On {said_on}, {', '.join(blocked)} had been blocked since "
                        + ", ".join(str(_blocked_since(facts.history[k], said_on)) for k in blocked)
                    )
                else:
                    verdict = "supported"
                    reality.append(f"No issue was in a blocked status on {said_on}.")
            if _ON_TRACK.search(sentence) and roadmap is not None:
                if roadmap.late_vs_sprint:
                    verdict = "contradicted"
                    reality.append(
                        f"Projected finish {roadmap.finish} is after the target {target}."
                    )
                elif verdict != "contradicted":
                    verdict = "supported"
                    reality.append(f"Projected finish {roadmap.finish} is on or before {target}.")
            for match in [*_DONE_BY.finditer(sentence), *_RELEASE_ON.finditer(sentence)]:
                promised = parse_when(match.group(1), said_on)
                if promised is None:
                    continue
                keys = ISSUE_KEY.findall(sentence.upper())
                key = next((k for k in keys if k in finish_by_key or k in issues), None)
                projected = finish_by_key.get(key) if key else (roadmap.finish if roadmap else None)
                if key and key in issues and not issues[key].is_open:
                    verdict = "supported" if verdict != "contradicted" else verdict
                    reality.append(f"{key} is already done.")
                elif projected and projected > promised:
                    verdict = "contradicted"
                    what = key or "the sprint"
                    reality.append(
                        f"Data projects {what} to finish {projected}, "
                        f"{_days(promised, projected)} working day(s) after {promised}."
                    )
                elif projected:
                    verdict = "supported" if verdict != "contradicted" else verdict
                    reality.append(f"Data projects {key or 'the sprint'} to finish {projected}.")
            reality = list(dict.fromkeys(reality))
            if reality:
                checks.append(
                    ClaimCheck(
                        claim=sentence[:300],
                        source=f"{note.source}: {note.ref}",
                        said_by=note.author,
                        said_on=note.at,
                        verdict=verdict,
                        reality=reality,
                    )
                )
    order = {"contradicted": 0, "unverifiable": 1, "supported": 2}
    return sorted(checks, key=lambda c: (order[c.verdict], c.said_on or date.min), reverse=False)


def _blocked_since(changes: list[StatusChange], day: date) -> date:
    since = day
    for change in changes:
        if change.at <= day and change.to_status.lower() in BLOCKED:
            since = change.at
    return since


# ------------------------------------------------------------------ main analysis


def analyze_delay(
    snapshot: ProjectSnapshot, facts: DelayFacts, roadmap: Roadmap | None = None
) -> DelayReport:
    today = snapshot.today
    sprint = snapshot.sprint
    start = sprint.start if sprint and sprint.start else today - timedelta(days=14)
    target = sprint.end if sprint else None
    scope = snapshot.sprint_issues or snapshot.issues
    losses: dict[str, dict] = {}
    per_issue: dict[str, float] = defaultdict(float)
    events: dict[str, list[str]] = defaultdict(list)
    elapsed = max(1, _days(start, today))

    notes_by_issue: dict[str, list[Note]] = defaultdict(list)
    for note in facts.notes:
        for key in note.issue_keys:
            notes_by_issue[key].append(note)
        if note.source == "jira comment":
            events[note.ref].append(
                f"{note.at}: {note.author or 'someone'} commented — {note.text[:160]}"
            )
    for note in facts.notes:
        if note.source == "email":
            for key in note.issue_keys:
                events[key].append(f"{note.at}: e-mail from {note.author}: {note.ref}")

    stories_segments: dict[str, list[Segment]] = {}
    rates = _daily_rate(snapshot)
    by_key = {i.key: i for i in snapshot.issues}
    # Open issues waiting on other open issues: their lost time belongs to the blocker's cause.
    waiting_on = {
        i.key: [b for b in i.blocked_by if b in by_key and by_key[b].is_open]
        for i in scope
        if i.is_open
    }
    waiting_on = {k: v for k, v in waiting_on.items() if v}

    def blocked_cause(key: str, seen: frozenset[str] = frozenset()) -> tuple[str, str | None]:
        """(cause, quote) for an issue, following "blocked by" links to the root."""
        for note in notes_by_issue.get(key, []):
            if EXTERNAL.search(note.text):
                return "blocked_external", note.text[:140]
        for blocker in waiting_on.get(key, []):
            if blocker not in seen:
                cause, quote = blocked_cause(blocker, seen | {key})
                if cause == "blocked_external":
                    return cause, quote
        return "blocked_internal", None

    for issue in scope:
        changes = facts.history.get(issue.key, [])
        segs = segments(issue, changes, start, today)
        stories_segments[issue.key] = segs
        for change in changes:
            events[issue.key].append(
                f"{change.at}: {change.author or 'someone'} moved it "
                f"{change.from_status} → {change.to_status}"
            )

        # Blocked time
        blocked_days = sum(s.days for s in segs if s.status.lower() in BLOCKED)
        if blocked_days:
            cause, why = blocked_cause(issue.key)
            _add(
                losses,
                cause,
                blocked_days,
                issue.key,
                f"{issue.key} blocked {blocked_days} working day(s)"
                + (f": “{why}”" if why else ""),
            )
            per_issue[issue.key] += blocked_days

        # Late start of high-priority work
        if issue.priority.lower() in {"highest", "high", "critical", "blocker"}:
            started = next((c.at for c in changes if c.to_status.lower() in IN_PROGRESS), None)
            begin = max(start, issue.created or start)
            has_code = any(issue.key in p.issue_keys for p in snapshot.pulls)
            if started is None and issue.category == "todo" and not has_code:
                late = _days(begin, today) - START_GRACE_DAYS
                if late > 0 and elapsed >= 3:
                    _add(
                        losses,
                        "started_late",
                        late,
                        issue.key,
                        f"{issue.key} ({issue.priority}) not started after "
                        f"{_days(begin, today)} working day(s)",
                    )
                    per_issue[issue.key] += late
            elif started and started > begin:
                late = _days(begin, started) - START_GRACE_DAYS
                if late > 0:
                    _add(
                        losses,
                        "started_late",
                        late,
                        issue.key,
                        f"{issue.key} ({issue.priority}) started {started}, "
                        f"{_days(begin, started)} working day(s) into the sprint",
                    )
                    per_issue[issue.key] += late

        # Rework: moved backwards from review/done
        for i, change in enumerate(changes):
            if (
                change.from_status.lower() in LATER_STAGES
                and change.to_status.lower() not in LATER_STAGES
            ):
                back_until = next(
                    (c.at for c in changes[i + 1 :] if c.to_status.lower() in LATER_STAGES), today
                )
                days = _days(max(change.at, start), back_until)
                if issue.key in waiting_on and back_until == today:
                    continue  # counted below as time waiting on the blocking issue
                _add(
                    losses,
                    "rework",
                    days,
                    issue.key,
                    f"{issue.key} sent back {change.from_status} → {change.to_status} "
                    f"on {change.at}",
                )
                per_issue[issue.key] += days

        # Scope added after the sprint started
        if sprint and issue.created and issue.created > start:
            points = issue.points or 0
            days = round(points / rates, 1) if points else 0.0
            _add(
                losses,
                "scope_added",
                days,
                issue.key,
                f"{issue.key} ({issue.issue_type}, {points:g} pts) added on {issue.created}",
            )
            events[issue.key].append(f"{issue.created}: created after the sprint started")

    # Waiting on another open issue (charged to the blocker's root cause)
    for issue in scope:
        if issue.key not in waiting_on:
            continue
        if any(s.status.lower() in BLOCKED for s in stories_segments.get(issue.key, [])):
            continue  # already counted as blocked time
        changes = facts.history.get(issue.key, [])
        said = [n.at for n in notes_by_issue.get(issue.key, []) if n.at and WAITING.search(n.text)]
        since = min(said) if said else (changes[-1].at if changes else start)
        days = _days(max(since, start), today)
        cause, why = blocked_cause(issue.key)
        blockers = ", ".join(waiting_on[issue.key])
        _add(
            losses,
            cause,
            days,
            issue.key,
            f"{issue.key} waiting on {blockers} since {since}"
            + (f" (root cause: “{why}”)" if why else ""),
        )
        per_issue[issue.key] += days

    # Code review wait
    scope_keys = {i.key for i in scope}
    for pull in snapshot.pulls:
        if not (set(pull.issue_keys) & scope_keys) or not pull.created:
            continue
        end = pull.merged or (today if pull.state == "open" else None)
        if end is None:
            continue
        approved = any(s.upper() == "APPROVED" for s in pull.review_states)
        wait = _days(max(pull.created, start), end) - REVIEW_GRACE_DAYS
        key = next(iter(sorted(set(pull.issue_keys) & scope_keys)))
        events[key].append(
            f"{pull.created}: PR {pull.repository}#{pull.number} opened"
            + (f", merged {pull.merged}" if pull.merged else ", still open")
        )
        if key in waiting_on:
            events[key].append(
                f"PR {pull.repository}#{pull.number} waits on {', '.join(waiting_on[key])}"
            )
            continue
        if pull.state == "open" and key in by_key and not by_key[key].is_open:
            continue  # issue already done: a status mismatch (see contradictions), not lost time
        if wait > 0 and (pull.state == "open" and not approved or pull.merged):
            status = (
                f"merged after {_days(pull.created, end)} day(s)"
                if pull.merged
                else (f"open {_days(pull.created, end)} day(s), no approval")
            )
            _add(losses, "review_wait", wait, key, f"PR {pull.repository}#{pull.number} {status}")
            per_issue[key] += wait

    # Leave of people holding open work
    open_owners = {i.assignee for i in scope if i.is_open and i.assignee}
    by_id = {mid.lower(): m for m in snapshot.members for mid in m.ids}
    for leave in snapshot.leave:
        member = by_id.get(leave.employee_id.lower())
        if not member or member.name not in open_owners:
            continue
        window_end = target or today
        lo, hi = max(leave.start, start), min(leave.end, window_end)
        days = working_days(lo, hi) if hi >= lo else 0
        when = "upcoming" if leave.start > today else "taken"
        _add(
            losses,
            "leave",
            days,
            None,
            f"{member.name}: {days} day(s) of leave {leave.start}–{leave.end} ({when})",
        )

    # Production incidents
    for incident in snapshot.ops.incidents if snapshot.ops else []:
        if incident.created and incident.created >= start:
            days = max(1, _days(incident.created, today))
            _add(
                losses,
                "incidents",
                days,
                None,
                f"{incident.source} incident {incident.id} open since {incident.created}: "
                f"{incident.title}",
            )

    total = round(sum(b["days"] for b in losses.values()), 1)
    ranked = sorted(losses.items(), key=lambda kv: -kv[1]["days"])
    loss_models = [
        Loss(
            cause=cause,
            label=CAUSE_LABELS[cause],
            days=round(b["days"], 1),
            share=round(b["days"] / total, 2) if total else 0.0,
            issues=sorted(b["issues"]),
            evidence=b["evidence"][:8],
        )
        for cause, b in ranked
    ]

    projected = roadmap.finish if roadmap else None
    slip = _days(target, projected) if target and projected and projected > target else 0
    if loss_models:
        top = loss_models[0]
        where = f" ({', '.join(top.issues)})" if top.issues else ""
        status = f"{slip} working day(s) late" if slip else "on time so far"
        headline = (
            f"Projected {status}. Main reason: {top.label.lower()}{where}, "
            f"{top.days:g} of {total:g} person-days lost."
        )
    else:
        headline = (
            "No time lost found in the data."
            if not slip
            else (
                f"Projected {slip} working day(s) late, but no single cause stands out in the data."
            )
        )

    stories = [
        IssueStory(
            key=i.key,
            summary=i.summary,
            assignee=i.assignee,
            status=i.status,
            days_lost=round(per_issue.get(i.key, 0.0), 1),
            segments=stories_segments.get(i.key, []),
            events=sorted(set(events.get(i.key, [])))[:12],
        )
        for i in sorted(scope, key=lambda i: (-per_issue.get(i.key, 0.0), i.key))
    ]
    claims = check_claims(facts.notes, snapshot, facts, roadmap, target)
    return DelayReport(
        project=snapshot.key,
        as_of=today,
        sprint=sprint.name if sprint else None,
        sprint_start=start,
        target=target,
        projected_finish=projected,
        slip_days=slip,
        headline=headline,
        total_days_lost=total,
        losses=loss_models,
        stories=stories,
        claims=claims,
        warnings=[*snapshot.warnings, *facts.warnings],
    )


def _daily_rate(snapshot: ProjectSnapshot) -> float:
    """Team points per working day (sprint capacity / sprint working days), at least 1."""
    sprint = snapshot.sprint
    days = working_days(sprint.start, sprint.end) if sprint and sprint.start and sprint.end else 10
    capacity = sum(m.capacity_points for m in snapshot.members)
    return max(1.0, capacity / max(1, days))


def check_statement(
    text: str, snapshot: ProjectSnapshot, facts: DelayFacts, roadmap: Roadmap | None
) -> list[ClaimCheck]:
    """Check something you were told ("we're on track, refunds ship Friday") against the data."""
    note = Note(
        at=snapshot.today,
        author="you were told",
        text=redact_text(text).text[:2000],
        source="statement",
        ref="your input",
    )
    target = snapshot.sprint.end if snapshot.sprint else None
    checks = check_claims([note], snapshot, facts, roadmap, target)
    return checks or [
        ClaimCheck(
            claim=text[:300],
            source="statement: your input",
            said_by=None,
            said_on=snapshot.today,
            verdict="unverifiable",
            reality=[
                "No checkable claim found. Try phrases like “on track”, “no blockers” or "
                "“PHX-2 done by Friday”."
            ],
        )
    ]
