# How the platform is evaluated

Every AI component has its own evaluation suite, its own dataset and its own pass/fail
thresholds. Suites that do not need an LLM run offline, for free, deterministically, and
gate CI. The agent suite and the LLM-judge metrics run when a model is configured. Every
headline metric is reported with a **bootstrap 95% confidence interval**, because the
datasets are small (12–140 cases) and a single number would overstate certainty.

```bash
python -m evaluation run                          # all 8 offline suites → evaluation/reports/
python -m evaluation run --suite rag --judge      # + LLM-as-judge metrics (needs a model)
python -m evaluation run --suite agent --judge    # end to end with the real model
python -m evaluation export --format ragas        # same samples for Ragas / DeepEval
python -m evaluation frameworks --ragas           # grade them in Ragas (optional install)
```

## The evaluation map

| Layer | Suite | What is measured | Method | Data |
|---|---|---|---|---|
| Retrieval | `retrieval` | Hit rate@1/@k, Precision@k, Recall@k, MRR, graded nDCG@k, context precision@k, p50/p95 latency, leak rate, ablation | Ranked results judged against labelled passages (gain 2 = exact section, 1 = right document) | 22 graded queries (verbatim, paraphrase, multi-part) + 3 access-control probes |
| Platform search | `platform_search` | Hit@1/@k, MRR on questions whose answer lives in one system (Slack, email, Jira, Linear, meetings, incidents, CRM, finance, Keka), filter precision, permission leak rate, re-index no-op | PHX indexed from 10 sources next to the policy documents; role-based leak probes | 12 cross-source questions, 3 filtered searches, 3 leak probes |
| RAG generation | `rag` | Context recall, context precision@k, faithfulness, answer relevance, semantic similarity, factual-correctness F1, citation validity, sync leak rate | Real agent pipeline; offline extractive responder; LLM judge with `--judge` | 12 questions with hand-written reference answers |
| Agent | `agent` | Completion, tool-selection recall/precision/F1, trajectory (in order), argument accuracy, fact coverage, citation validity, approval compliance, negative rejection, faithfulness (judge), latency p50/p95, tokens and steps per question | Real model on the demo company; recording wrappers capture planned calls and the exact evidence the responder saw | 13 questions incl. writes and unanswerable questions |
| Delay analysis | `delivery` | Top-cause accuracy, macro-F1 over causes, hard-tier accuracy, vendor root-cause recall, claim-verdict accuracy, **false-contradiction rate** | Synthetic sprints with a planted dominant cause, a competing smaller cause and noise; claims with known truth | 140 seeded scenarios (35 "hard") + 60 claims |
| Analytics | `analytics` | Golden values: forecast, workload, contradictions, risk, critical path, DORA, delay attribution | Exact match at a pinned date on the demo project | 21 checks |
| Forecasting | `planning` | P80 coverage (calibration), Brier score, slip-prediction AUC | Backtests on simulated sprint histories | Simulated sprints |
| Safety | `guardrails` | Injection recall/precision/F1/false-alarm rate; redaction recall and false-positive rate | Binary classification on labelled texts | Attacks, benign texts, PII/secrets |
| Security | `redteam` | Containment rate | A scripted hostile planner drives the real agent and gateway (exfiltration, privilege escalation, approval bypass) | Attack scenarios |
| MCP code mode | `codemode` | Sandbox escape-block rate, governance pass rate, task success | Hostile and normal scripts through the AST interpreter | Scripts |

**CI proves the gates have teeth.** `tests/evaluation/test_quality_gates.py` breaks the
system on purpose and asserts the suite fails: approvals switched off, the access filter
removed, redaction disabled, restricted synced pages published, vendor detection
removed (delay suite), a responder that echoes the question (RAG suite).

## Metric definitions

**Retrieval** (`evaluation/metrics.py`)

| Metric | Definition | Why it matters here |
|---|---|---|
| Hit rate@k | Queries with a relevant result in the top k | Did the agent get *something* usable? |
| Precision@k | Relevant results in the top k / k | Noise the model must read past |
| Recall@k | Relevant passages found / all relevant | Multi-part questions need every part |
| MRR | Mean of 1 / rank of the first relevant result | The responder trusts early results most |
| nDCG@k | Σ gainᵢ / log₂(i+1), normalized by the ideal ranking | Graded: the right document but the wrong section earns partial credit |
| Context precision@k | Mean of precision@i over the relevant positions (Ragas) | Rank-aware: relevant chunks first |
| Leak rate | Restricted documents returned to a role without access | Must be 0; access control runs in SQL *before* ranking |

**Generation**

| Metric | Offline method (CI) | LLM-judge method (`--judge`) |
|---|---|---|
| Faithfulness | Answer split into claims; a claim is supported if ≥ 70% of its content words occur in one sentence of the context the responder saw | Judge lists claims and marks each supported or not (Ragas) |
| Answer relevance | Embedding cosine of question and answer, **0 if the answer only repeats the question** | Judge writes questions the answer answers; cosine to the original (Ragas) |
| Semantic similarity | Embedding cosine of answer and reference | Same, with real embeddings (`--real-embeddings`) |
| Factual correctness | Recall: reference facts in the answer. Precision: answer claims grounded in the reference. F1 | Judge classifies claims as TP/FP/FN against the reference |
| Citation validity | Every `[S#]` points at a successful tool call | — |
| Negative rejection | Unanswerable questions answered with "could not find…" | — |

**Agent:** tool precision / recall / F1, trajectory match (exact, in order, any order),
argument accuracy (expected values passed to the tool), approval compliance (writes stop
at a pending approval), latency percentiles, tokens and steps per question.

**Delay analysis:** top-cause accuracy, macro-F1 (no cause hides behind a frequent one),
vendor root-cause recall (including through a dependency chain), claim-verdict accuracy
and the false-contradiction rate: how often a *true* status report is flagged as
contradicted. That last one is the trust metric, because wrongly accusing a manager is
worse than missing a problem.

## Current results (offline, demo data)

| Suite | Headline results (95% CI) |
|---|---|
| retrieval | Hit@1 0.86 (0.73–1.0) · Hit@3 1.0 · MRR 0.93 · nDCG@3 0.78 · context precision@3 0.94 · p95 3 ms · leak 0 · fusion gain ≥ 0 |
| platform_search | Hit@1 0.92 · MRR 0.93 · 10 sources · filter precision 1.0 · leak 0 · re-index changes 0 |
| rag | context recall 0.92 · context precision 0.87 · faithfulness 1.0 · semantic similarity 0.74 · factual-correctness F1 0.67 (0.42–0.92) · citations 1.0 · leak 0 |
| delivery | top cause 0.91 (0.86–0.96) · hard tier 0.66 · macro-F1 0.91 · vendor recall 1.0 (0.75 incl. hard wording) · claims 1.0 · false contradictions 0 |
| planning | P80 coverage 0.88 · Brier 0.018 · slip AUC 0.95 |
| guardrails | injection P/R/F1 1.0 · redaction recall 1.0, false positives 0 |
| redteam / codemode | containment 1.0 · escape-block 1.0 · governance 1.0 |

## What the evaluations found (and what was fixed)

1. **A metric that measured nothing.** The old RAG faithfulness check ("is the answer found
   verbatim in the context?") scored 1.0 even though the offline responder was answering
   with the *echoed search query*, which is technically in the context. Adding answer-level
   metrics (factual correctness, answer relevance with an echo guard) exposed it. The
   responder now reads only passage text, and a CI test makes sure an echoing responder
   fails. Lesson: a single metric can be satisfied trivially; use complementary ones.
2. **Hybrid search is not automatically better, so it is tuned and gated.** The first ablation
   showed keyword-only BM25 (MRR 0.89) beating hybrid (0.86): the offline embedder is a lexical
   hash, so the "semantic" list mostly adds noise, and standard RRF (k=60) lets that noise
   reorder good keyword hits. A sweep over RRF k and weights found that with this embedder the
   semantic list should only break ties (k=5, weight 0.1); with a real embedding model the
   standard k=60 and equal weights apply. Fusion parameters now follow the embedder, a CI gate
   requires hybrid ≥ the best single retriever, and identifier-aware terms, title boosting and
   plural folding lifted MRR to 0.93. One cross-source question still misses (a pure paraphrase,
   "when will they ship the fix" vs "a fix is planned for next week"): exactly the case a real
   embedding model fixes, kept in the dataset rather than tuned away.
3. **The delay benchmark started too easy.** The first version scored 100%, which proved
   only the arithmetic. Competing causes and a hard tier (vendor problems worded without
   keywords, internal blocks that mention "client") brought accuracy to a realistic 91% and
   exposed the keyword classifier's limits (hard tier 66%). An LLM classifier is the
   planned fix, and this suite will measure whether it helps.
4. **Generator bugs show up as engine failures.** Planted leave that fell after the sprint
   looked like misattribution until the cases were inspected: the engine was right to
   ignore it. Always read the failed cases, not just the score.

## Frameworks: how this maps to Ragas, DeepEval, TruLens, LangSmith and Phoenix

| Framework | Role | How it is used here |
|---|---|---|
| **Ragas** | RAG metrics with an LLM judge | Same metric definitions implemented in `evaluation/metrics.py` (offline) and `evaluation/judges.py` (LLM). `python -m evaluation export --format ragas` writes `SingleTurnSample` records (user_input, retrieved_contexts, response, reference); `frameworks --ragas` grades them with Ragas' own metrics when installed |
| **DeepEval** | Pytest-style LLM test cases | `export --format deepeval` writes `LLMTestCase` fields (input, actual_output, expected_output, retrieval_context); our suites already run as pytest gates in CI, which is DeepEval's model |
| **TruLens** | The "RAG triad" | Context relevance ↔ context precision@k, groundedness ↔ faithfulness, answer relevance ↔ answer relevance |
| **Phoenix (Arize)** | Tracing and eval UI, self-hosted | Spans carry OpenInference attributes (`openinference.span.kind` = AGENT / TOOL / LLM). Run Phoenix locally and set `OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:6006` to see every agent run as a trace |
| **LangSmith** | Hosted tracing and datasets | Accepts the same OpenTelemetry traces; not used by default because the platform is self-hosted and free to run |

Why not depend on one framework? The offline suites must run in CI for free and give the
same answer every time; LLM-judged metrics cost money and vary between runs. The
frameworks are used where they add value (judged scores, trace UI) without being
required.

## Limitations (say these before an interviewer does)

- Datasets are small and written by one person; the confidence intervals show it (Hit@1
  could be anywhere from 0.59 to 0.95).
- The offline generation metrics are proxies (lexical support, hashing embeddings). The
  LLM-judge versions exist but have not yet been run at scale with a real model.
- The agent suite has not yet been run end to end with a real model on a full day's quota.
- The delay benchmark is synthetic; real projects are messier. The next step is labelling
  a few real past sprints of my own and measuring agreement.
- Demo integrations are simulated twins; live behaviour is verified for Jira and Gmail only.

## Questions you will be asked

**"How do you know your RAG system works?"** I evaluate the retriever and the generator
separately. Retriever: hit rate, MRR, nDCG and rank-aware context precision on labelled
queries, including paraphrases and multi-part questions, plus an ablation (BM25 only,
vector only, hybrid). Generator: faithfulness to the retrieved context, answer relevance,
semantic similarity and factual correctness against reference answers, offline proxies in
CI and LLM-judged versions Ragas-style. Plus citation validity and a leak test, because a
correct answer from a document the user may not see is a failure.

**"How do you evaluate an agent?"** Outcome and trajectory. Outcome: did it finish, are
the facts right, are citations valid. Trajectory: did it call the right tools in a sensible
order with the right arguments (tool F1, in-order trajectory match, argument accuracy).
Safety: writes must stop at an approval, unanswerable questions must be refused. Cost:
latency percentiles, tokens and steps per question.

**"How do you evaluate something with no labelled data, like 'why is a project late'?"**
I generate synthetic sprints where I know the answer: plant a dominant cause, add a
competing smaller one and noise, and measure top-cause accuracy and macro-F1. A hard tier
with misleading wording shows where the rules break. For claims, I track the false
contradiction rate separately, because false accusations destroy trust in the tool.

**"How do you stop your metrics from lying?"** Several complementary metrics per stage,
confidence intervals, and CI tests that break the system on purpose and require the
suite to fail. That is how I found a faithfulness metric that passed an echoing responder.

**"LLM-as-judge: how do you trust the judge?"** Its inputs are wrapped as untrusted data
so a document cannot grade itself; it sees exactly the evidence the responder saw; and the
deterministic proxies run alongside it, so a judge that disagrees wildly with them gets
investigated.
