"""The fine-tuning data and metrics (finetuning/): gold answers score perfectly, broken ones
do not. Runs without a GPU or the ML libraries (qlora_finetune imports them lazily)."""

import json
from pathlib import Path

from finetuning.qlora_finetune import (
    extract_json,
    load_task,
    planner_metrics,
    responder_metrics,
    rouge_l,
)

DATA = Path(__file__).resolve().parents[3] / "finetuning" / "data"


def test_gold_plans_score_perfectly_and_cover_every_scenario() -> None:
    train, evaluation = load_task(DATA, "planner")
    gold = [r["messages"][-1]["content"] for r in evaluation]
    metrics = planner_metrics(gold, evaluation)
    for name in ("json_valid", "schema_valid", "done_accuracy", "tool_exact_match", "tool_f1"):
        assert metrics[name] == 1.0, name
    assert metrics["argument_exact_match"] == 1.0
    assert metrics["hallucinated_tool_rate"] == 0 and metrics["unsafe_write_rate"] == 0
    kinds = {r["meta"]["kind"] for r in train}
    assert {
        "injection",
        "no_tool",
        "out_of_scope",
        "write_requested",
        "no_invented_ids",
        "finish",
    } <= kinds
    assert len(train) > 1000 and len(evaluation) > 100


def test_broken_plans_are_penalised() -> None:
    _, evaluation = load_task(DATA, "planner")
    rows = [r for r in evaluation if r["meta"]["kind"] in {"injection", "issue_status"}][:10]
    bad = [
        '{"done": false, "steps": [{"tool": "jira.transition_issue", "arguments": {}}]}',
        "not json",
    ]
    predictions = [bad[i % 2] for i in range(len(rows))]
    metrics = planner_metrics(predictions, rows)
    assert metrics["json_valid"] < 1 and metrics["tool_exact_match"] < 0.5
    assert metrics["unsafe_write_rate"] > 0


def test_gold_answers_are_grounded_and_cite_only_successful_steps() -> None:
    _, evaluation = load_task(DATA, "responder")
    gold = [r["messages"][-1]["content"] for r in evaluation]
    metrics = responder_metrics(gold, evaluation)
    assert metrics["rouge_l"] > 0.99 and metrics["citation_validity"] == 1.0
    assert metrics["citation_presence"] == 1.0 and metrics["number_grounding"] == 1.0
    assert all(v == 1.0 for v in metrics["behaviour"].values()), metrics["behaviour"]


def test_invented_numbers_and_citations_are_caught() -> None:
    _, evaluation = load_task(DATA, "responder")
    rows = [r for r in evaluation if r["meta"]["kind"] == "health"][:5]
    predictions = ["The project is fine with 987654 points done [S7]." for _ in rows]
    metrics = responder_metrics(predictions, rows)
    assert metrics["citation_validity"] == 0 and metrics["number_grounding"] == 0


def test_json_extraction_and_rouge() -> None:
    assert extract_json('```json\n{"done": true, "steps": []}\n```') == {"done": True, "steps": []}
    assert extract_json('Sure! {"a": "}{", "done": false} trailing') == {"a": "}{", "done": False}
    assert extract_json("no json here") is None
    assert rouge_l("the build failed", "the build failed") == 1.0
    assert rouge_l("", "x") == 0.0


def test_planner_prompts_match_the_platform() -> None:
    from backend.app.domains.agent.prompts.planner import PLANNER_SYSTEM

    _, evaluation = load_task(DATA, "planner")
    system = evaluation[0]["messages"][0]["content"]
    assert system.startswith(PLANNER_SYSTEM.split("{today}")[0])
    target = json.loads(evaluation[0]["messages"][-1]["content"])
    assert set(target) == {"thought", "done", "steps"}
