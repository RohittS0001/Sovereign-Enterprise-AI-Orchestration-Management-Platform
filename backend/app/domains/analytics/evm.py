"""Earned value management from finance connectors and Jira progress.

- BAC (budget at completion) and AC (actual cost) come from the project's budget lines
  (finance connectors, matched by the project's ``cost_centers`` links or its key/name).
- Percent complete = done story points / all story points in the project's issues.
- EV = BAC × percent complete; CPI = EV / AC; EAC = BAC / CPI; VAC = BAC − EAC.

There is no project-level schedule baseline (planned value) in Jira, so SPI is not
computed here; schedule confidence comes from the digital twin (``twin.py``).

Amounts in different currencies are never added: with more than one currency (and no
exchange rates configured) only the per-currency budget/actual totals are reported and
BAC/AC/EV/CPI/EAC stay empty, with a note saying why.
"""

from pydantic import BaseModel

from backend.app.domains.analytics.results import RiskFactor
from backend.app.domains.analytics.snapshot import ProjectSnapshot


class EarnedValue(BaseModel):
    sources: list[str]
    currency: str | None
    budget_at_completion: float | None
    actual_cost: float | None
    percent_complete: float | None
    earned_value: float | None
    cpi: float | None
    estimate_at_completion: float | None
    variance_at_completion: float | None
    note: str | None = None
    # Per currency: {"USD": {"budget": 1000.0, "actual": 400.0}}
    by_currency: dict[str, dict[str, float | None]] = {}


def earned_value(snapshot: ProjectSnapshot) -> EarnedValue | None:
    lines = snapshot.finance
    if not lines:
        return None
    by_currency: dict[str, dict[str, float | None]] = {}
    for line in lines:
        bucket = by_currency.setdefault(line.currency or "unknown", {"budget": None, "actual": 0.0})
        if line.budget is not None:
            bucket["budget"] = round((bucket["budget"] or 0) + line.budget, 2)
        bucket["actual"] = round((bucket["actual"] or 0) + (line.actual or 0), 2)
    currencies = set(by_currency)
    total = sum(i.points for i in snapshot.issues)
    done = sum(i.points for i in snapshot.issues if i.category == "done")
    pct = done / total if total else None
    if len(currencies) > 1:
        return EarnedValue(
            sources=sorted({line.source for line in lines}), currency=None,
            budget_at_completion=None, actual_cost=None,
            percent_complete=round(pct, 3) if pct is not None else None,
            earned_value=None, cpi=None, estimate_at_completion=None, variance_at_completion=None,
            note=f"Budget lines use different currencies ({', '.join(sorted(currencies))}); "
                 "earned value is not computed across currencies",
            by_currency=by_currency,
        )  # fmt: skip
    budgets = [line.budget for line in lines if line.budget is not None]
    bac = sum(budgets) if budgets else None
    ac = sum(line.actual or 0 for line in lines)
    ev = cpi = eac = vac = None
    note = None
    if bac is None:
        note = "No budget in the finance source; only actual cost is known"
    elif pct is None:
        note = "No story points in Jira; percent complete is unknown"
    else:
        ev = bac * pct
        if ac > 0:
            cpi = ev / ac
            eac = bac / cpi if cpi else None
            vac = bac - eac if eac is not None else None
    return EarnedValue(
        sources=sorted({line.source for line in lines}),
        currency=next(iter(currencies)) if currencies != {"unknown"} else None,
        budget_at_completion=round(bac, 2) if bac is not None else None,
        actual_cost=round(ac, 2),
        percent_complete=round(pct, 3) if pct is not None else None,
        earned_value=round(ev, 2) if ev is not None else None,
        cpi=round(cpi, 2) if cpi is not None else None,
        estimate_at_completion=round(eac, 2) if eac is not None else None,
        variance_at_completion=round(vac, 2) if vac is not None else None,
        note=note,
        by_currency=by_currency,
    )


def budget_factor(value: EarnedValue) -> RiskFactor:
    """Cost overrun risk: CPI 1.0 or better scores 0; CPI 0.75 or worse scores the full 10."""
    if value.cpi is None:
        return RiskFactor(name="Budget", score=0, max_score=10,
                          evidence=[value.note] if value.note else [])  # fmt: skip
    score = round(min(10.0, max(0.0, (1 - value.cpi) * 40)), 1)
    currency = f" {value.currency}" if value.currency else ""
    evidence = [
        f"CPI {value.cpi:.2f}: earned {value.earned_value:,.0f}{currency} for "
        f"{value.actual_cost:,.0f}{currency} spent ({value.percent_complete:.0%} complete)",
    ]
    if value.variance_at_completion is not None and value.variance_at_completion < 0:
        eac, bac = value.estimate_at_completion or 0, value.budget_at_completion or 0
        evidence.append(
            f"Forecast overrun {-value.variance_at_completion:,.0f}{currency} "
            f"(EAC {eac:,.0f} vs budget {bac:,.0f})"
        )
    return RiskFactor(
        name="Budget", score=score, max_score=10, evidence=evidence,
        recommendation="Review spend vs. progress with finance" if score else None,
    )  # fmt: skip
