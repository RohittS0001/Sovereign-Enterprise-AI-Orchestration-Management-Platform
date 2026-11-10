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
python -m evaluation export --format ragas     # samples for Ragas / DeepEval
```

Each run prints a Markdown summary, writes `reports/eval-<timestamp>.{json,md}`, and exits with
code 1 if any metric misses its threshold. Thresholds live next to the data in `datasets/*.yaml`.

## Suites

The full method, metric definitions, current results, findings and limitations are in
[docs/evaluation.md](../docs/evaluation.md). Formulas live in `metrics.py`, LLM-judge versions
in `judges.py`, Ragas/DeepEval export in `frameworks.py`.

| Suite | Needs a model | What it checks | Gated metrics (threshold) |
|---|---|---|---|
| `retrieval` | no | Knowledge search per role; keyword/semantic/hybrid ablation | hit@1 (≥0.7), hit@3 (≥0.85), recall@3 (≥0.8), MRR (≥0.75), graded nDCG@3 (≥0.7), context precision@3 (≥0.7), p95 latency (≤500 ms), **leak rate (=0)** |
| `rag` | no (`--judge` adds LLM metrics) | Answers through the real agent pipeline (extractive responder) and knowledge sync | context recall (≥0.85), context precision@4 (≥0.7), faithfulness (≥0.9), answer relevance, semantic similarity (≥0.5), factual-correctness F1 (≥0.6), citation validity (=1), **sync leak rate (=0)** |
| `guardrails` | no | Prompt-injection detector and redaction | injection recall (≥0.85) / precision (≥0.9) / F1 / false alarms, redaction recall (=1), false positives (=0) |
| `analytics` | no | Forecast, workload, contradictions, risk, critical path, DORA, delay attribution on PHX at a fixed date | accuracy vs golden values (=1) |
| `delivery` | no | "Why is it late?" on 140 synthetic sprints with planted causes (35 hard) + 60 claims of known truth | top-cause accuracy (≥0.85), hard tier (≥0.5), macro-F1 (≥0.85), vendor recall (≥0.95), claim accuracy (≥0.95), **false contradictions (≤0.02)** |
| `planning` | no | Digital-twin calibration; slip-model discrimination | P80 coverage (0.65–0.95), Brier (≤0.2), slip AUC (≥0.8) |
| `codemode` | no | MCP code mode: sandbox escapes, governance, real tasks | escape block (=1), governance (=1), task success (=1) |
| `redteam` | no | A scripted *compromised* planner tries exfiltration, unauthorized writes, injection, bypasses | containment rate (=1) |
| `agent` | **yes** | End-to-end questions incl. writes and unanswerable ones | completion, tool recall/precision/F1, trajectory in order, argument accuracy, fact coverage, citations, approval compliance (=1), negative rejection, faithfulness (`--judge`), latency, tokens |

Headline metrics carry bootstrap 95% confidence intervals. `tests/evaluation/test_quality_gates.py`
runs the offline suites in CI and proves the gates have teeth: it disables approvals, breaks access
control, switches off redaction, publishes restricted synced pages, removes vendor detection and
plugs in a responder that echoes the question, and asserts the suites fail.

## Frameworks (Ragas, DeepEval, Phoenix, LangSmith)

The metrics are implemented natively so CI is free and deterministic; the same samples export to
the frameworks' own schemas (`python -m evaluation export --format ragas|deepeval`) and can be
graded there (`python -m evaluation frameworks --ragas|--deepeval`, optional installs). Traces carry
OpenInference attributes, so a local Phoenix (`OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:6006`)
shows agent runs, tool calls and LLM calls.

## Adding cases

- New document or query → `datasets/retrieval.yaml` (`relevant` for quality, `forbidden` for leaks).
- New attack phrasing or a false alarm seen in production → `datasets/guardrails.yaml`.
- New hostile behaviour → a scenario in `datasets/redteam.yaml` (`plan` = what the compromised
  model proposes, `expect` = how governance must respond).
- New question type → `datasets/agent.yaml`.

Caveat: the guardrail samples were written alongside the detectors, so their scores are
optimistic — grow that dataset with real traffic.
