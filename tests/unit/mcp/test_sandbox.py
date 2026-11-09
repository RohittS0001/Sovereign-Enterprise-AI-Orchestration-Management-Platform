"""The code-mode sandbox: supported Python subset, isolation and resource limits."""

import pytest

from backend.app.domains.mcp.sandbox import HostFunction, Limits, Namespace, run


async def ok(code: str, **host):
    result = await run(code, host)
    assert result.ok, f"{result.error_type}: {result.error}"
    return result


async def fails(code: str, error_type: str | None = None, limits: Limits | None = None, **host):
    result = await run(code, host, limits)
    assert not result.ok, f"expected failure, got {result.result!r}"
    if error_type:
        assert result.error_type == error_type, (result.error_type, result.error)
    return result


# ------------------------------------------------------------------ the supported subset


async def test_last_expression_is_the_result_and_print_is_captured() -> None:
    r = await ok("x = [1, 2, 3]\nprint('total', sum(x))\n{'n': len(x), 'max': max(x)}")
    assert r.result == {"n": 3, "max": 3}
    assert r.stdout == "total 6\n"


async def test_result_variable_is_used_when_last_statement_is_not_an_expression() -> None:
    assert (await ok("result = 41\nresult += 1")).result == 42


async def test_functions_defaults_kwargs_closures_and_recursion() -> None:
    code = """
def fib(n, memo={}):
    if n < 2:
        return n
    if n not in memo:
        memo[n] = fib(n - 1) + fib(n - 2)
    return memo[n]

def make_adder(k):
    return lambda x, *, scale=1: (x + k) * scale

add5 = make_adder(5)
def collect(*args, **kw):
    return [args, kw]
[fib(20), add5(1), add5(1, scale=2), collect(1, 2, a=3)]
"""
    assert (await ok(code)).result == [6765, 6, 12, [[1, 2], {"a": 3}]]


async def test_comprehensions_unpacking_slices_and_fstrings() -> None:
    code = """
rows = [{"key": "PHX-1", "points": 5, "status": "Done"},
        {"key": "PHX-2", "points": 3, "status": "In Progress"},
        {"key": "PHX-3", "points": 8, "status": "In Progress"}]
open_ = [r for r in rows if r["status"] != "Done"]
by_key = {r["key"]: r["points"] for r in rows}
first, *rest = sorted(open_, key=lambda r: -r["points"])
f"{first['key']} has {first['points']:>3} pts; rest={[r['key'] for r in rest]}; {by_key!r}"[:60]
"""
    assert (await ok(code)).result.startswith("PHX-3 has   8 pts; rest=['PHX-2']")


async def test_try_except_finally_and_raise() -> None:
    code = """
log = []
try:
    {}["missing"]
except LookupError as e:
    log.append(("lookup", e.type))
finally:
    log.append("finally")
try:
    raise ValueError("bad input")
except (TypeError, ValueError) as e:
    log.append(str(e))
try:
    1 / 0
except ArithmeticError:
    log.append("div")
log
"""
    assert (await ok(code)).result == [["lookup", "KeyError"], "finally", "bad input", "div"]


async def test_helpers_namespaces_and_methods() -> None:
    code = """
items = [5, 1, 4]
items.sort(reverse=True)
d = {"b": 2}
d.update(a=1)
[items, sorted(d.items()), statistics.median([3, 1, 2]), math.ceil(2.1),
 dates.days_between("2026-11-01", "2026-11-09"), json.loads(json.dumps({"x": [1]})),
 " a,b ".strip().split(","), list(enumerate("ab")), any(x > 4 for x in items)]
"""
    assert (await ok(code)).result == [
        [5, 4, 1], [["a", 1], ["b", 2]], 2, 3, 8, {"x": [1]}, ["a", "b"],
        [[0, "a"], [1, "b"]], True,
    ]  # fmt: skip


async def test_host_functions_and_namespaces_including_async() -> None:
    async def lookup(key):
        return {"key": key, "status": "Done"}

    tools = Namespace("jira", {"get_issue": HostFunction("jira.get_issue", lookup)})
    r = await ok("issue = await jira.get_issue('PHX-1')\nissue['status']", jira=tools)
    assert r.result == "Done"


async def test_output_is_capped() -> None:
    r = await run("for i in range(1000):\n    print('x' * 50)", {}, Limits(max_output_chars=100))
    assert r.ok and len(r.stdout) == 100 and r.output_truncated


# ------------------------------------------------------------------ isolation


@pytest.mark.parametrize(
    "code",
    [
        "import os",
        "from os import system",
        "__import__('os')",
        "().__class__.__bases__[0].__subclasses__()",
        "x = 1\nx.__class__",
        "class A: pass",
        "with open('f') as f: pass",
        "global x",
        "async def f(): pass",
        "def g():\n    yield 1",
        "@decorator\ndef f(): pass",
        "__builtins__",
    ],
)
async def test_unsupported_and_dunder_syntax_is_rejected_before_running(code) -> None:
    await fails(code, "SyntaxError")


@pytest.mark.parametrize(
    "code",
    ["open('secrets.txt')", "eval('1+1')", "exec('x=1')", "getattr(1, 'real')", "globals()",
     "vars()", "type(1)", "compile('1', 'f', 'eval')", "breakpoint()", "input()"],
)  # fmt: skip
async def test_dangerous_builtins_do_not_exist(code) -> None:
    await fails(code, "NameError")


@pytest.mark.parametrize(
    "code",
    [
        "'{0.__class__}'.format(1)",  # str.format attribute traversal
        "'abc'.encode()",
        "[].__len__",
        "(1).real",
        "len.__self__",
        "print.fn",
        "math.__dict__",
        "json.loads.fn",
        "f = lambda: 1\nf.closure",
        "try:\n    1/0\nexcept Exception as e:\n    e.__traceback__",
    ],
)
async def test_attribute_escapes_are_blocked(code) -> None:
    await fails(code)


async def test_errors_are_reported_with_type_and_message() -> None:
    r = await fails("x = {'a': 1}\nx['b']", "KeyError")
    assert "b" in r.error


# ------------------------------------------------------------------ resource limits


@pytest.mark.parametrize(
    "code",
    [
        "while True:\n    pass",
        "x = 0\nwhile True:\n    x += 1",
        "def f():\n    return f()\nf()",
        "'x' * (10 ** 9)",
        "[0] * (10 ** 8)",
        "10 ** (10 ** 8)",
        "1 << (10 ** 7)",
        "list(range(10 ** 9))",
        "x = []\nfor i in range(10 ** 9):\n    x.append(i)",
        "s = 'a'\nfor i in range(64):\n    s = s + s",
        "[[i, j] for i in range(10 ** 5) for j in range(10 ** 5)]",
        "sorted(range(10 ** 9))",
        "'-'.join(['ab'] * 1000) * 1000",
    ],
)
async def test_resource_exhaustion_is_stopped(code) -> None:
    await fails(code, "LimitExceeded", Limits(max_steps=50_000, timeout_seconds=5))


async def test_limits_cannot_be_caught_by_sandboxed_code() -> None:
    code = "try:\n    while True:\n        pass\nexcept Exception:\n    result = 'escaped'"
    await fails(code, "LimitExceeded", Limits(max_steps=5_000))


async def test_code_length_is_limited() -> None:
    await fails("x = 1\n" * 10, "ValueError", Limits(max_code_chars=20))
