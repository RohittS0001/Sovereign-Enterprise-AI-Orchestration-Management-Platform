"""Find places where Jira says one thing and GitHub another."""

from backend.app.domains.analytics.results import Contradiction
from backend.app.domains.analytics.snapshot import ProjectSnapshot, PullFacts


def find_contradictions(snapshot: ProjectSnapshot) -> list[Contradiction]:
    if not snapshot.pulls:
        return []

    pulls_by_issue: dict[str, list[PullFacts]] = {}
    for pull in snapshot.pulls:
        for key in pull.issue_keys:
            pulls_by_issue.setdefault(key, []).append(pull)

    def ref(pull: PullFacts) -> str:
        return pull.url or f"{pull.repository}#{pull.number}"

    found: list[Contradiction] = []
    for issue in snapshot.issues:
        pulls = pulls_by_issue.get(issue.key, [])
        open_pulls = [p for p in pulls if p.state == "open"]
        merged = [p for p in pulls if p.state == "merged"]

        if issue.category == "done" and open_pulls and not merged:
            found.append(
                Contradiction(
                    kind="done_but_pr_open",
                    issue=issue.key,
                    detail=f"{issue.key} is {issue.status} in Jira but PR "
                    + ", ".join(f"#{p.number}" for p in open_pulls)
                    + " is still open",
                    evidence=[ref(p) for p in open_pulls],
                )
            )
        elif issue.status.lower() in {"in review", "code review", "review"} and not pulls:
            found.append(
                Contradiction(
                    kind="review_without_pr",
                    issue=issue.key,
                    detail=f"{issue.key} is in review but no pull request references it",
                    evidence=[],
                )
            )
        elif merged and issue.category != "done" and not open_pulls:
            found.append(
                Contradiction(
                    kind="merged_but_not_done",
                    issue=issue.key,
                    detail=f"PR #{merged[0].number} for {issue.key} was merged but the issue "
                    f"is still "
                    f"{issue.status}",
                    evidence=[ref(p) for p in merged],
                )
            )
    return found
