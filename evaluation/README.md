# Evaluation

Measures whether the platform answers correctly, retrieves the right documents, computes the right
analytics and stays safe — on the built-in demo company, so results are reproducible and nothing
touches real systems.

```bash
python -m evaluation run                       # offline suites (also enforced by pytest/CI)
python -m evaluation run --suite retrieval     # one suite (repeatable)
python -m evaluation run --suite agent         # needs a model: LLM1_MODEL_NAME (e.g. qwen3:8b on Ollama)
python -m evaluation run --suite agent --judge --real-embeddings
python -m scripts.dev eval                     # same as the first line
```

Each run prints a Markdown summary, writes `reports/eval-<timestamp>.{json,md}`, and exits with
code 1 if any metric misses its threshold. Thresholds live next to the data in `datasets/*.yaml`.

## Suites

| Suite | Needs a model | What it checks | Metrics (threshold) |
|---|---|---|---|
| `retrieval` | no | Knowledge search over the demo documents, per role | hit@1 (≥0.7), recall@3 (≥0.85), MRR (≥0.75), nDCG@5, **leak rate (=0)** |
| `rag` | no | Retrieval-augmented answering through the real agent pipeline (extractive responder) and knowledge sync | context recall (≥0.85), context precision (≥0.6), citation validity (=1), faithfulness (≥0.9), **sync leak rate (=0)** |
| `guardrails` | no | Prompt-injection detector and redaction | injection recall (≥0.85) / precision (≥0.9), redaction recall (=1), redaction false positives (=0) |
| `analytics` | no | Forecast, workload, contradictions, risk on PHX at a fixed date | accuracy vs golden values (=1) |
| `planning` | no | Digital-twin calibration on synthetic projects; slip-model discrimination on held-out sprints | P80 coverage (0.65–0.95), Brier score (≤0.2), slip AUC (≥0.8) |
| `codemode` | no | MCP code mode: 20 sandbox escape attempts, governance of scripted tool calls, real tasks | escape block rate (=1), governance (=1), task success (=1) |
| `redteam` | no | A scripted *compromised* planner tries exfiltration, unauthorized writes, path/JQL injection, invented tools, restricted documents, secret leakage, allow-list bypass | containment rate (=1) |
| `agent` | **yes** | End-to-end questions answered by the configured model | completion (≥0.9), tool-selection recall (≥0.7), fact coverage (≥0.6), citation validity (≥0.9), approval compliance (=1), faithfulness via LLM-as-judge (≥0.7, `--judge`) |

The four offline suites run in CI through `tests/evaluation/test_quality_gates.py`. The same file
proves the gates have teeth: it disables approvals, breaks access control and switches off
redaction, and asserts the suites fail.

## Why native metrics instead of DeepEval / Ragas

Both default to hosted judge models and pull large dependency trees, which conflicts with a
sovereign, on-prem platform. The metrics here are small and transparent; the judge is your own
local model (LLM 2). The YAML datasets are plain enough to export to those tools later if wanted.

## Adding cases

- New document or query → `datasets/retrieval.yaml` (`relevant` for quality, `forbidden` for leaks).
- New attack phrasing or a false alarm seen in production → `datasets/guardrails.yaml`.
- New hostile behaviour → a scenario in `datasets/redteam.yaml` (`plan` = what the compromised
  model proposes, `expect` = how governance must respond).
- New question type → `datasets/agent.yaml`.

Caveat: the guardrail samples were written alongside the detectors, so their scores are
optimistic — grow that dataset with real traffic.
