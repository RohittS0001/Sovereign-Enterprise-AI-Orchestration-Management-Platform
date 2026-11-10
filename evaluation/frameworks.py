"""Interop with evaluation frameworks: Ragas, DeepEval (datasets) and Phoenix/LangSmith (traces).

The suites compute the metrics themselves (offline, free, deterministic), but the same RAG
samples can be exported in each framework's own schema and graded there, e.g. to compare our
proxies with their LLM-judged versions:

    python -m evaluation export --format ragas    > evaluation/reports/ragas.jsonl
    python -m evaluation export --format deepeval > evaluation/reports/deepeval.json
    python -m evaluation frameworks --ragas       # needs `pip install ragas` + a judge model

Ragas ``SingleTurnSample``: user_input, retrieved_contexts, response, reference.
DeepEval ``LLMTestCase``: input, actual_output, expected_output, retrieval_context.

Tracing (Phoenix, LangSmith, Jaeger): the platform emits OpenTelemetry spans with
OpenInference attributes (``openinference.span.kind`` = AGENT / TOOL / LLM, model names), so
pointing ``OTEL_EXPORTER_OTLP_ENDPOINT`` at a local Phoenix (``http://localhost:6006``) shows
every agent run as a trace: planner LLM calls, tool calls through the gateway, responder.
Neither framework is a dependency: the platform stays self-hosted and free to run.
"""

import json
from typing import Any


async def rag_samples() -> list[dict[str, Any]]:
    """Question, retrieved contexts, answer and reference for every RAG dataset question."""
    from evaluation.suites import rag

    result = await rag.run()
    return result.artifacts.get("samples", [])


def to_ragas(samples: list[dict[str, Any]]) -> str:
    return "\n".join(
        json.dumps(
            {
                "user_input": s["question"],
                "retrieved_contexts": s["contexts"],
                "response": s["answer"],
                "reference": s["reference"],
            }
        )
        for s in samples
    )


def to_deepeval(samples: list[dict[str, Any]]) -> str:
    return json.dumps(
        [
            {
                "input": s["question"],
                "actual_output": s["answer"],
                "expected_output": s["reference"],
                "retrieval_context": s["contexts"],
            }
            for s in samples
        ],
        indent=2,
    )


async def run_ragas() -> str:
    """Grade the samples with Ragas (optional dependency; needs a judge LLM configured)."""
    try:
        from ragas import EvaluationDataset, evaluate  # type: ignore[import-not-found]
        from ragas.metrics import (  # type: ignore[import-not-found]
            FactualCorrectness,
            Faithfulness,
            LLMContextPrecisionWithReference,
            LLMContextRecall,
            ResponseRelevancy,
        )
    except ImportError:
        return (
            "Ragas is not installed. `pip install ragas`, configure its judge model (e.g. "
            "OPENAI_API_KEY or a LangChain wrapper around your local model), then rerun."
        )
    samples = await rag_samples()
    dataset = EvaluationDataset.from_list(
        [json.loads(line) for line in to_ragas(samples).splitlines()]
    )
    metrics = [
        LLMContextPrecisionWithReference(),
        LLMContextRecall(),
        Faithfulness(),
        ResponseRelevancy(),
        FactualCorrectness(),
    ]
    return str(evaluate(dataset=dataset, metrics=metrics))


async def run_deepeval() -> str:
    """Grade the samples with DeepEval (optional dependency; needs a judge LLM configured)."""
    try:
        from deepeval import evaluate  # type: ignore[import-not-found]
        from deepeval.metrics import (  # type: ignore[import-not-found]
            AnswerRelevancyMetric,
            ContextualPrecisionMetric,
            ContextualRecallMetric,
            FaithfulnessMetric,
        )
        from deepeval.test_case import LLMTestCase  # type: ignore[import-not-found]
    except ImportError:
        return (
            "DeepEval is not installed. `pip install deepeval`, configure its judge model, rerun."
        )
    cases = [LLMTestCase(**case) for case in json.loads(to_deepeval(await rag_samples()))]
    metrics = [
        ContextualPrecisionMetric(),
        ContextualRecallMetric(),
        FaithfulnessMetric(),
        AnswerRelevancyMetric(),
    ]
    return str(evaluate(test_cases=cases, metrics=metrics))
