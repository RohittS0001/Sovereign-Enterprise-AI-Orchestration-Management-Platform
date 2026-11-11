"""Run evaluations.

    python -m evaluation run                     # all offline suites (CI gate)
    python -m evaluation run --suite retrieval   # one suite
    python -m evaluation run --suite agent       # needs LLM1_MODEL_NAME (e.g. Ollama)
    python -m evaluation run --suite agent --judge --real-embeddings
    python -m evaluation export --format ragas     # RAG samples for Ragas / DeepEval
    python -m evaluation frameworks --ragas        # grade with Ragas (optional install)

Writes JSON + Markdown reports to evaluation/reports/ and exits 1 if any threshold fails.
"""

import argparse
import asyncio
import sys
from pathlib import Path

from evaluation.core import SuiteResult, to_markdown, write_reports

OFFLINE = [
    "retrieval",
    "rag",
    "platform_search",
    "guardrails",
    "analytics",
    "delivery",
    "planning",
    "redteam",
    "codemode",
]
ALL = [*OFFLINE, "agent"]


async def run_suites(
    names: list[str], *, judge: bool = False, real_embeddings: bool = False
) -> list[SuiteResult]:
    from evaluation.suites import (
        agent,
        analytics,
        codemode,
        delivery,
        guardrails,
        planning,
        platform_search,
        rag,
        redteam,
        retrieval,
    )

    runners = {
        "retrieval": lambda: retrieval.run(real_embeddings=real_embeddings),
        "guardrails": guardrails.run,
        "analytics": analytics.run,
        "redteam": redteam.run,
        "codemode": codemode.run,
        "rag": lambda: rag.run(judge=judge),
        "delivery": delivery.run,
        "platform_search": platform_search.run,
        "planning": planning.run,
        "agent": lambda: agent.run(judge=judge, real_embeddings=real_embeddings),
    }
    return [await runners[name]() for name in names]


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m evaluation")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument(
        "--suite", action="append", choices=ALL, help="repeatable; default: offline suites"
    )
    run.add_argument(
        "--judge", action="store_true", help="agent suite: grade faithfulness with LLM 2"
    )
    run.add_argument(
        "--real-embeddings", action="store_true", help="use EMBEDDING_MODEL_NAME instead of hashing"
    )
    run.add_argument("--out", default=str(Path(__file__).parent / "reports"))
    export = sub.add_parser("export", help="RAG samples in a framework's dataset format")
    export.add_argument("--format", choices=["ragas", "deepeval"], required=True)
    frameworks = sub.add_parser("frameworks", help="grade with Ragas/DeepEval if installed")
    frameworks.add_argument("--ragas", action="store_true")
    frameworks.add_argument("--deepeval", action="store_true")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):  # Windows consoles default to a legacy code page
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    if args.command == "export":
        from evaluation import frameworks as fw

        samples = asyncio.run(fw.rag_samples())
        print(fw.to_ragas(samples) if args.format == "ragas" else fw.to_deepeval(samples))
        return
    if args.command == "frameworks":
        from evaluation import frameworks as fw

        if args.ragas:
            print(asyncio.run(fw.run_ragas()))
        if args.deepeval:
            print(asyncio.run(fw.run_deepeval()))
        return

    results = asyncio.run(
        run_suites(args.suite or OFFLINE, judge=args.judge, real_embeddings=args.real_embeddings)
    )
    print(to_markdown(results))
    json_path, md_path = write_reports(results, Path(args.out))
    print(f"Reports: {json_path}\n         {md_path}")
    sys.exit(0 if all(r.passed for r in results) else 1)


if __name__ == "__main__":
    main()
