"""Ground-truth evaluation of "Why is it late?" on synthetic projects with planted causes.

There is no public dataset of "why was this sprint late", so the suite *generates* one: each
seeded scenario is a small sprint with background noise (short review waits, a day of
rework…) plus one **planted dominant cause** of known size — a vendor block (directly or
through a dependency chain), an internal block, a late start, a review wait, rework, scope
added mid-sprint, or leave. The engine never sees the label.

- **top_cause_accuracy** — the top-ranked cause equals the planted one.
- **cause_macro_f1** — F1 per cause, averaged (no cause can hide behind a frequent one).
- **external_root_cause_recall** — vendor blocks found even two hops down a dependency
  chain or worded as "supplier", "third-party", "their side".
- **claim_verdict_accuracy** — status claims with a known truth ("no blockers" said on a day
  something was or was not blocked; "on track" when the projection is or is not late).
- **false_contradiction_rate** — true claims wrongly flagged as contradicted. This is the
  trust metric: accusing a manager of a false report must be rare.
"""

import random
from collections import Counter
from datetime import date, timedelta

from backend.app.domains.analytics.snapshot import (
    IssueFacts,
    LeaveFacts,
    Member,
    ProjectSnapshot,
    PullFacts,
    SprintFacts,
)
from backend.app.domains.delivery.delay import DelayFacts, Note, StatusChange, analyze_delay
from backend.app.domains.delivery.roadmap import build_roadmap
from evaluation import metrics as M
from evaluation.core import CaseResult, Metric, SuiteResult, Timer, load_dataset

START = date(2026, 11, 2)  # Monday
TODAY = date(2026, 11, 11)  # Wednesday of week 2: 7 working days elapsed
CAUSES = [
    "blocked_external",
    "blocked_internal",
    "started_late",
    "review_wait",
    "rework",
    "scope_added",
    "leave",
]
EXTERNAL_WORDING = [
    "Vendor confirmed a regression on their side.",
    "Waiting on the supplier to ship a patch.",
    "Third-party API is down; nothing we can do until they fix it.",
    "Partner team at the customer has not sent credentials yet.",
]
# Hard tier: realistic wording that defeats keyword rules (an LLM classifier should fix these).
HARD_EXTERNAL_WORDING = [
    "Waiting on Stripe to fix their webhook bug.",
    "AWS support ticket is still open, no response yet.",
    "Twilio rate-limits us until they raise the quota.",
]
HARD_INTERNAL_WORDING = [
    "Blocked until our team finishes the client SDK upgrade.",
    "Waiting for QA to test the partner dashboard feature.",
    "Need the customer-portal refactor from our own team first.",
]
INTERNAL_WORDING = [
    "Waiting for the database migration from our platform team.",
    "Blocked until the design review is done.",
    "Need the shared library release first.",
]


def day(n: int) -> date:
    return START + timedelta(days=n)


def _issue(key, **kw) -> IssueFacts:
    base = dict(
        summary=key,
        status="In Progress",
        category="in_progress",
        priority="Medium",
        assignee="A",
        assignee_id=None,
        due=None,
        points=3.0,
        issue_type="Story",
    )
    base.update(kw)
    return IssueFacts(key=key, **base)


def _ago(working: int) -> date:
    """The date `working` working days before TODAY (so [date, TODAY) holds that many)."""
    when = TODAY
    while working > 0:
        when -= timedelta(days=1)
        if when.weekday() < 5:
            working -= 1
    return when


def _after_start(working: int) -> date:
    """The date `working` working days after START."""
    when = START
    while working > 0:
        when += timedelta(days=1)
        if when.weekday() < 5:
            working -= 1
    return when


def plant(
    cause: str,
    size: int,
    base: int,
    owner: str,
    world: dict,
    rng: random.Random,
    chain: bool = False,
    hard: bool = False,
) -> None:
    """Add a loss of `cause` worth about `size` person-days, using keys T-<base>…"""
    k1, k2 = f"T-{base}", f"T-{base + 1}"
    issues, history, notes = world["issues"], world["history"], world["notes"]
    since = _ago(size)
    if cause in {"blocked_external", "blocked_internal"}:
        issues.append(_issue(k1, status="Blocked", assignee=owner))
        history[k1] = [
            StatusChange(day(0), owner, "To Do", "In Progress"),
            StatusChange(since, owner, "In Progress", "Blocked"),
        ]
        if hard:
            wording = (
                HARD_EXTERNAL_WORDING if cause == "blocked_external" else HARD_INTERNAL_WORDING
            )
        else:
            wording = EXTERNAL_WORDING if cause == "blocked_external" else INTERNAL_WORDING
        notes.append(Note(since, owner, rng.choice(wording), "jira comment", k1, [k1]))
        if chain:  # k2 waits on k1: its time must be charged to k1's root cause too
            issues.append(_issue(k2, status="In Review", assignee="A", blocked_by=[k1]))
            history[k2] = [
                StatusChange(day(0), "A", "To Do", "In Progress"),
                StatusChange(since, "A", "In Progress", "In Review"),
            ]
            notes.append(
                Note(
                    since,
                    "A",
                    f"Ready, but blocked until {k1} is resolved.",
                    "jira comment",
                    k2,
                    [k2, k1],
                )
            )
    elif cause == "started_late":
        # started `size + 1` working days in; the engine allows one day of grace
        issues.append(_issue(k1, priority="High", assignee=owner, created=day(0)))
        history[k1] = [StatusChange(_after_start(size + 1), owner, "To Do", "In Progress")]
    elif cause == "review_wait":
        issues.append(_issue(k1, status="In Review", assignee=owner))
        history[k1] = [StatusChange(day(0), owner, "To Do", "In Review")]
        world["pulls"].append(
            PullFacts(
                "acme/x",
                base,
                f"{k1} change",
                "open",
                False,
                None,
                _ago(size + 1),
                None,
                [],
                ["COMMENTED"],
                [k1],
            )
        )
    elif cause == "rework":
        issues.append(_issue(k1, assignee=owner))
        back = _ago(size)
        history[k1] = [
            StatusChange(day(0), owner, "To Do", "In Progress"),
            StatusChange(back - timedelta(days=1), owner, "In Progress", "In Review"),
            StatusChange(back, owner, "In Review", "In Progress"),
        ]
        if rng.random() < 0.5:  # sometimes it is back in review already
            history[k1].append(StatusChange(TODAY, owner, "In Progress", "In Review"))
    elif cause == "scope_added":
        # team rate is 45 points / 10 working days = 4.5 points per day
        issues.append(
            _issue(
                k1,
                created=day(3),
                points=size * 4.5,
                status="To Do",
                category="todo",
                assignee=owner,
            )
        )
    elif cause == "leave":
        # leave already taken inside the sprint: [size working days ago, yesterday]
        world["leave"].append(LeaveFacts(f"e-{owner}", _ago(size), TODAY - timedelta(days=1)))


def scenario(
    cause: str, rng: random.Random, index: int
) -> tuple[ProjectSnapshot, DelayFacts, dict]:
    """Background noise + a planted dominant cause + a smaller competing cause."""
    members = [Member(n, None, f"e-{n}", 15) for n in ("A", "B", "C")]
    world: dict = {"issues": [], "history": {}, "notes": [], "pulls": [], "leave": []}
    issues, history = world["issues"], world["history"]
    size = rng.randint(4, 6)  # person-days of the dominant loss
    secondary = rng.choice(
        [
            c
            for c in CAUSES
            if c != cause and not {c, cause} <= {"blocked_external", "blocked_internal"}
        ]
    )
    meta: dict = {"cause": cause, "secondary": secondary}

    # Background: three normal issues, a 2-day PR (1 day of review wait), maybe 1 day rework.
    for n, who in enumerate("ABC", start=1):
        key = f"T-{n}"
        issues.append(_issue(key, assignee=who))
        history[key] = [StatusChange(day(0), who, "To Do", "In Progress")]
    world["pulls"].append(
        PullFacts(
            "acme/x",
            1,
            "T-1 change",
            "merged",
            False,
            None,
            day(1),
            day(3),
            [],
            ["APPROVED"],
            ["T-1"],
        )
    )
    if rng.random() < 0.5:
        history["T-2"] += [
            StatusChange(day(2), "B", "In Progress", "In Review"),
            StatusChange(day(3), "B", "In Review", "In Progress"),
            StatusChange(day(4), "B", "In Progress", "In Review"),
        ]

    chain = cause == "blocked_external" and index % 3 == 0  # every third: via a dependency
    meta["chain"] = chain
    hard = index % 4 == 3  # every fourth scenario: misleading wording, a near-tie
    meta["hard"] = hard
    plant(cause, size, 9, "C", world, rng, chain=chain, hard=hard)
    gap = 1 if hard else rng.randint(2, 3)
    plant(secondary, max(1, size - gap), 20, "B", world, rng)
    notes, pulls, leave = world["notes"], world["pulls"], world["leave"]

    sprint = SprintFacts(1, "S1", "active", START, START + timedelta(days=11))
    snapshot = ProjectSnapshot(
        key="T",
        name="Synthetic",
        today=TODAY,
        issues=issues,
        sprint=sprint,
        sprint_issue_keys={i.key for i in issues},
        members=members,
        pulls=pulls,
        leave=leave,
    )
    return snapshot, DelayFacts(history=history, notes=notes), meta


def claim_cases(rng: random.Random, n: int) -> list[tuple[ProjectSnapshot, DelayFacts, str, str]]:
    """Claims with a known truth: (snapshot, facts, claim text, expected verdict)."""
    cases = []
    for i in range(n):
        blocked = rng.random() < 0.5
        issues = [_issue("T-1"), _issue("T-2", status="Blocked" if blocked else "In Progress")]
        history = {
            "T-1": [StatusChange(day(0), "A", "To Do", "In Progress")],
            "T-2": [StatusChange(day(0), "A", "To Do", "In Progress")],
        }
        said_on = day(rng.choice([7, 8]))
        if blocked:
            history["T-2"].append(StatusChange(day(4), "A", "In Progress", "Blocked"))
        late = rng.random() < 0.5
        points = 40.0 if late else 4.0
        issues.append(_issue("T-3", points=points, status="To Do", category="todo", assignee="B"))
        sprint = SprintFacts(1, "S1", "active", START, START + timedelta(days=11))
        snapshot = ProjectSnapshot(
            key="T",
            name="Synthetic",
            today=TODAY,
            issues=issues,
            sprint=sprint,
            sprint_issue_keys={x.key for x in issues},
            members=[Member("A", None, "e-A", 10), Member("B", None, "e-B", 10)],
        )
        if i % 2 == 0:
            text = rng.choice(
                ["No blockers this week.", "No major blockers.", "No real issues so far."]
            )
            expected = "contradicted" if blocked else "supported"
        else:
            text = rng.choice(
                ["We are on track.", "Sprint is on schedule.", "All green, as planned."]
            )
            expected = "contradicted" if build_roadmap(snapshot).late_vs_sprint else "supported"
        facts = DelayFacts(
            history=history, notes=[Note(said_on, "pm", text, "email", f"update {i}")]
        )
        cases.append((snapshot, facts, text, expected))
    return cases


async def run() -> SuiteResult:
    data = load_dataset("delivery")
    t = data["thresholds"]
    rng = random.Random(data["seed"])  # noqa: S311 - reproducible synthetic data
    result = SuiteResult("delivery")
    predicted, actual, hard_correct = [], [], []
    external_hits = external_total = 0
    external_all: list[float] = []
    verdict_ok, false_contradictions, true_claims = [], 0, 0

    with Timer() as timer:
        for index in range(data["scenarios"]):
            cause = CAUSES[index % len(CAUSES)]
            snapshot, facts, meta = scenario(cause, rng, index)
            report = analyze_delay(snapshot, facts, build_roadmap(snapshot))
            top = report.losses[0].cause if report.losses else "none"
            predicted.append(top)
            actual.append(cause)
            if meta["hard"]:
                hard_correct.append(float(top == cause))
            if cause == "blocked_external":
                external_all.append(float(top == "blocked_external"))
                if not meta["hard"]:
                    external_total += 1
                    external_hits += top == "blocked_external"
            result.cases.append(
                CaseResult(
                    f"scenario-{index:03d}-{cause}{'-chain' if meta.get('chain') else ''}"
                    f"{'-hard' if meta['hard'] else ''}",
                    top == cause,
                    {"predicted": top, "losses": {loss.cause: loss.days for loss in report.losses}},
                )
            )

        for i, (snapshot, facts, text, expected) in enumerate(claim_cases(rng, data["claims"])):
            report = analyze_delay(snapshot, facts, build_roadmap(snapshot))
            got = report.claims[0].verdict if report.claims else "unverifiable"
            verdict_ok.append(float(got == expected))
            if expected == "supported":
                true_claims += 1
                false_contradictions += got == "contradicted"
            result.cases.append(
                CaseResult(
                    f"claim-{i:03d}",
                    got == expected,
                    {"claim": text, "expected": expected, "got": got},
                )
            )

    f1s = []
    for cause in CAUSES:
        tp = sum(p == a == cause for p, a in zip(predicted, actual, strict=True))
        fp = sum(p == cause != a for p, a in zip(predicted, actual, strict=True))
        fn = sum(a == cause != p for p, a in zip(predicted, actual, strict=True))
        f1s.append(2 * tp / (2 * tp + fp + fn) if tp else 0.0)
    correct = [float(p == a) for p, a in zip(predicted, actual, strict=True)]
    confusion = Counter(f"{a}->{p}" for p, a in zip(predicted, actual, strict=True) if p != a)

    result.metrics = [
        Metric(
            "top_cause_accuracy",
            M.mean(correct),
            t["top_cause_accuracy"],
            ci=M.bootstrap_ci(correct),
        ),
        Metric(
            "hard_tier_accuracy",
            M.mean(hard_correct),
            t["hard_tier_accuracy"],
            ci=M.bootstrap_ci(hard_correct),
            description="Misleading wording and near-ties (keyword-rule limits show here)",
        ),
        Metric("cause_macro_f1", M.mean(f1s), t["cause_macro_f1"]),
        Metric(
            "external_root_cause_recall",
            round(external_hits / max(1, external_total), 4),
            t["external_root_cause_recall"],
        ),
        Metric(
            "external_recall_incl_hard",
            M.mean(external_all),
            None,
            description="Vendor root cause incl. unusual wording (informational)",
        ),
        Metric(
            "claim_verdict_accuracy",
            M.mean(verdict_ok),
            t["claim_verdict_accuracy"],
            ci=M.bootstrap_ci(verdict_ok),
        ),
        Metric(
            "false_contradiction_rate",
            round(false_contradictions / max(1, true_claims), 4),
            t["false_contradiction_rate"],
            higher_is_better=False,
        ),
    ]
    if confusion:
        result.cases.append(CaseResult("confusions", True, dict(confusion.most_common(5))))
    result.seconds = timer.seconds
    return result
