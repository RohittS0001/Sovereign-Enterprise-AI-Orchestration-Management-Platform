"""Prompt-injection detection and redaction quality, as binary classifiers.

Injection: recall (attacks caught), precision, F1 and the false-alarm rate on benign text
(over-blocking also hurts users). Redaction: recall of sensitive values (cards, Aadhaar,
PAN, SSN, IBAN, secrets) and the false-positive rate on safe text.
"""

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
    recall = ratio(true_pos, len(attacks))
    precision = ratio(true_pos, true_pos + false_pos)
    f1 = round(2 * precision * recall / (precision + recall), 4) if precision + recall else 0.0
    false_alarm = ratio(false_pos, len(benign))
    result.metrics = [
        Metric("injection_recall", recall, t["injection_recall"]),
        Metric("injection_precision", precision, t["injection_precision"]),
        Metric("injection_f1", f1, t.get("injection_f1")),
        Metric("injection_false_alarm_rate", false_alarm, t.get("injection_false_alarm_rate"),
               higher_is_better=False, description="Benign texts flagged as attacks"),
        Metric("redaction_recall", ratio(hidden, len(data["redaction"]["sensitive"])), t["redaction_recall"]),
        Metric("redaction_false_positive_rate", ratio(altered, len(data["redaction"]["safe"])),
               t["redaction_false_positive_rate"], higher_is_better=False),
    ]  # fmt: skip
    result.seconds = timer.seconds
    return result
