"""One entry point for everyday development tasks (works the same on Windows, macOS, Linux).

python -m scripts.dev check            # everything CI runs: lint, types, all tests, build
python -m scripts.dev test [pytest args]
python -m scripts.dev lint             # ruff + formatting check + frontend type check
python -m scripts.dev fmt              # auto-fix lint and format
python -m scripts.dev web-test         # frontend tests
python -m scripts.dev eval [args]      # evaluation suites (see evaluation/README.md)
python -m scripts.dev migrate          # alembic upgrade head
python -m scripts.dev demo             # local demo API (see scripts/run_demo.py)
"""

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable


def run(*command: str) -> None:
    print(f"\n$ {' '.join(command)}", flush=True)
    executable = shutil.which(command[0]) or command[0]
    result = subprocess.run([executable, *command[1:]], cwd=ROOT)
    if result.returncode:
        sys.exit(result.returncode)


def npm(*args: str) -> None:
    run("npm", "--prefix", "frontend", *args)


def lint() -> None:
    run(PY, "-m", "ruff", "check", ".")
    run(PY, "-m", "ruff", "format", "--check", ".")
    npm("run", "typecheck")


COMMANDS = {
    "test": lambda args: run(PY, "-m", "pytest", "-q", *args),
    "lint": lambda args: lint(),
    "fmt": lambda args: (
        run(PY, "-m", "ruff", "check", "--fix", "."),
        run(PY, "-m", "ruff", "format", "."),
    ),
    "web-test": lambda args: npm("test"),
    "eval": lambda args: run(PY, "-m", "evaluation", "run", *args),
    "migrate": lambda args: run(PY, "-m", "alembic", "upgrade", "head"),
    "demo": lambda args: run(PY, "-m", "scripts.run_demo"),
    "check": lambda args: (lint(), run(PY, "-m", "pytest", "-q"), npm("test"), npm("run", "build")),
}


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(__doc__)
        sys.exit(2)
    COMMANDS[sys.argv[1]](sys.argv[2:])


if __name__ == "__main__":
    main()
