"""Prompt-injection detection and redaction quality."""

from backend.app.domains.governance.guardrails.injection import scan_for_injection
from backend.app.domains.governance.guardrails.redaction import redact_text
from evaluation.core import CaseResult, Metric, SuiteResult, Timer, load_dataset, ratio


async def run() -> SuiteResult:
    data = load_dataset("guardrails")
    result = SuiteResult("guardrails")

    with Timer() as timer:
        attacks, benign = data["injection"]["attacks"], data["injection"]["benign"]
        true_pos = false_pos = 0
        for i, text in enumerate(attacks):
            flagged = scan_for_injection(text).flagged
            true_pos += flagged
            result.cases.append(CaseResult(f"attack-{i + 1}", flagged, {"text": text}))
        for i, text in enumerate(benign):
            flagged = scan_for_injection(text).flagged
            false_pos += flagged
            result.cases.append(CaseResult(f"benign-{i + 1}", not flagged, {"text": text}))

        hidden = 0
        for i, case in enumerate(data["redaction"]["sensitive"]):
            ok = case["must_hide"] not in redact_text(case["text"]).text
            hidden += ok
            result.cases.append(CaseResult(f"redact-{i + 1}", ok, {"text": case["text"]}))
        altered = 0
        for i, text in enumerate(data["redaction"]["safe"]):
            changed = redact_text(text).text != text
            altered += changed
            result.cases.append(CaseResult(f"safe-{i + 1}", not changed, {"text": text}))

    t = data["thresholds"]
    result.metrics = [
        Metric("injection_recall", ratio(true_pos, len(attacks)), t["injection_recall"]),
        Metric("injection_precision", ratio(true_pos, true_pos + false_pos), t["injection_precision"]),
        Metric("redaction_recall", ratio(hidden, len(data["redaction"]["sensitive"])), t["redaction_recall"]),
        Metric("redaction_false_positive_rate", ratio(altered, len(data["redaction"]["safe"])),
               t["redaction_false_positive_rate"], higher_is_better=False),
    ]  # fmt: skip
    result.seconds = timer.seconds
    return result
