"""The code-mode sandbox: a small, safe interpreter for a Python subset.

Model-written code is parsed with ``ast`` and *interpreted* node by node; it is never
compiled or exec'd. It can therefore only do what this module implements:

- no imports, classes, ``global``, ``with``, generators or ``async`` syntax;
- no attribute access except whitelisted methods of plain data types and the host
  namespaces it is handed (tools, ``json``, ``math``, ``statistics``, ``dates``);
- no names or attributes starting with ``__``; no ``eval``/``exec``/``open``/``getattr``;
- values are plain data (None, bool, int, float, str, list, tuple, dict, set) — tool
  results are JSON round-tripped before the code sees them.

Every evaluated node counts as a step; steps, wall time, tool calls, call depth, output
and collection/string/number sizes are capped. Limit violations cannot be caught by the
sandboxed code.
"""

import ast
import asyncio
import inspect
import json
import math
import statistics
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

# ------------------------------------------------------------------ limits & results


@dataclass(frozen=True)
class Limits:
    max_steps: int = 200_000
    timeout_seconds: float = 30.0
    max_tool_calls: int = 50
    max_output_chars: int = 20_000
    max_collection_size: int = 100_000
    max_string_length: int = 1_000_000
    max_call_depth: int = 40
    max_code_chars: int = 20_000
    max_int_bits: int = 100_000


class LimitExceeded(Exception):
    """A resource limit was hit. Not catchable by sandboxed code."""


class SandboxException(Exception):
    """An exception raised inside sandboxed code (catchable there by ``try``/``except``)."""

    def __init__(self, type_name: str, message: str = "") -> None:
        super().__init__(message)
        self.type_name = type_name
        self.message = message

    def __str__(self) -> str:
        return self.message

    def __repr__(self) -> str:
        return f"{self.type_name}({self.message!r})"


@dataclass
class RunResult:
    ok: bool
    result: Any = None
    stdout: str = ""
    error: str | None = None
    error_type: str | None = None
    steps: int = 0
    tool_calls: int = 0
    output_truncated: bool = False


# Exception classes sandboxed code may raise and catch, with their parent.
_EXCEPTIONS = {
    "Exception": None,
    "ArithmeticError": "Exception",
    "ZeroDivisionError": "ArithmeticError",
    "OverflowError": "ArithmeticError",
    "LookupError": "Exception",
    "KeyError": "LookupError",
    "IndexError": "LookupError",
    "ValueError": "Exception",
    "TypeError": "Exception",
    "NameError": "Exception",
    "AttributeError": "Exception",
    "RuntimeError": "Exception",
    "NotImplementedError": "RuntimeError",
    "AssertionError": "Exception",
    "ToolError": "Exception",  # a governed tool call failed or was denied
}


def _is_subclass(name: str, base: str) -> bool:
    current: str | None = name
    while current is not None:
        if current == base:
            return True
        current = _EXCEPTIONS.get(current)
    return False


# ------------------------------------------------------------------ sandbox values


@dataclass(frozen=True)
class ExceptionType:
    name: str

    def __repr__(self) -> str:
        return f"<class {self.name}>"


@dataclass
class Namespace:
    """A host object whose attributes the code may use, e.g. ``jira`` or ``math``."""

    name: str
    members: dict[str, Any]

    def __repr__(self) -> str:
        return f"<namespace {self.name}>"


@dataclass
class HostFunction:
    """A host callable (sync or async) exposed to sandboxed code."""

    name: str
    fn: Callable[..., Any]

    def __repr__(self) -> str:
        return f"<function {self.name}>"


@dataclass(frozen=True)
class PyType:
    """A whitelisted built-in type: callable as a constructor and usable in isinstance."""

    name: str
    type: type

    def __repr__(self) -> str:
        return f"<class {self.name}>"


@dataclass
class SandboxFunction:
    name: str
    args: ast.arguments
    body: list[ast.stmt] | ast.expr
    closure: "Scope"
    defaults: list[Any]
    kw_defaults: dict[str, Any]

    def __repr__(self) -> str:
        return f"<function {self.name}>"


class Scope:
    def __init__(self, variables: dict[str, Any], parent: "Scope | None" = None) -> None:
        self.vars = variables
        self.parent = parent

    def lookup(self, name: str) -> Any:
        scope: Scope | None = self
        while scope is not None:
            if name in scope.vars:
                return scope.vars[name]
            scope = scope.parent
        raise SandboxException("NameError", f"name {name!r} is not defined")


class _Return(Exception):  # noqa: N818 - control flow, not an error
    def __init__(self, value: Any) -> None:
        self.value = value


class _Break(Exception):  # noqa: N818
    pass


class _Continue(Exception):  # noqa: N818
    pass


_CONTROL = (_Return, _Break, _Continue, SandboxException, LimitExceeded)

# Methods of plain data types the code may call. Anything else is an AttributeError.
_METHODS: dict[type, frozenset[str]] = {
    str: frozenset(
        [
            "lower",
            "upper",
            "strip",
            "lstrip",
            "rstrip",
            "split",
            "rsplit",
            "join",
            "replace",
            "startswith",
            "endswith",
            "find",
            "rfind",
            "count",
            "title",
            "capitalize",
            "splitlines",
            "isdigit",
            "isalpha",
            "isalnum",
            "isspace",
            "zfill",
            "partition",
            "rpartition",
            "removeprefix",
            "removesuffix",
            "center",
            "ljust",
            "rjust",
            "casefold",
        ]
    ),
    list: frozenset(
        [
            "append",
            "extend",
            "insert",
            "pop",
            "remove",
            "index",
            "count",
            "sort",
            "reverse",
            "copy",
            "clear",
        ]
    ),
    dict: frozenset(
        ["get", "keys", "values", "items", "pop", "update", "setdefault", "copy", "clear"]
    ),
    set: frozenset(
        [
            "add",
            "discard",
            "remove",
            "union",
            "intersection",
            "difference",
            "symmetric_difference",
            "issubset",
            "issuperset",
            "copy",
            "pop",
            "clear",
        ]
    ),
    tuple: frozenset({"count", "index"}),
    float: frozenset({"is_integer"}),
}
_MUTATING = {"append", "extend", "insert", "update", "setdefault", "add"}
_GROWING_STR = {"replace", "join", "center", "ljust", "rjust", "zfill"}

_ALLOWED_NODES = (
    ast.Module, ast.Expr, ast.Assign, ast.AugAssign, ast.AnnAssign, ast.If, ast.For, ast.While,
    ast.Break, ast.Continue, ast.Pass, ast.FunctionDef, ast.Return, ast.Try, ast.Raise,
    ast.Assert, ast.Delete, ast.ExceptHandler, ast.arguments, ast.arg, ast.keyword,
    ast.Constant, ast.Name, ast.Load, ast.Store, ast.Del, ast.List, ast.Tuple, ast.Set,
    ast.Dict, ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp, ast.comprehension,
    ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.Compare, ast.IfExp, ast.Call, ast.Attribute,
    ast.Subscript, ast.Slice, ast.Starred, ast.JoinedStr, ast.FormattedValue, ast.Lambda,
    ast.Await, ast.operator, ast.unaryop, ast.boolop, ast.cmpop,
)  # fmt: skip


def validate(code: str, limits: Limits) -> ast.Module:
    """Parse and reject anything outside the supported subset (raises SandboxException)."""
    if len(code) > limits.max_code_chars:
        raise SandboxException("ValueError", f"code is longer than {limits.max_code_chars} chars")
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError as exc:
        raise SandboxException("SyntaxError", f"line {exc.lineno}: {exc.msg}") from None
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODES):
            raise SandboxException(
                "SyntaxError",
                f"line {getattr(node, 'lineno', '?')}: {type(node).__name__} is not supported "
                "in the sandbox (no imports, classes, with, global, yield or async def)",
            )
        name = getattr(node, "attr", None) if isinstance(node, ast.Attribute) else None
        if isinstance(node, ast.Name | ast.arg | ast.FunctionDef):
            name = getattr(node, "id", None) or getattr(node, "arg", None) or node.name
        if isinstance(name, str) and name.startswith("__"):
            raise SandboxException(
                "SyntaxError", f"names starting with '__' are not allowed: {name}"
            )
        if isinstance(node, ast.FunctionDef) and node.decorator_list:
            raise SandboxException("SyntaxError", "decorators are not supported")
    return tree


# ------------------------------------------------------------------ interpreter

_BINOPS: dict[type, Callable[[Any, Any], Any]] = {
    ast.Add: lambda a, b: a + b, ast.Sub: lambda a, b: a - b, ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b, ast.FloorDiv: lambda a, b: a // b, ast.Mod: lambda a, b: a % b,
    ast.Pow: lambda a, b: a**b, ast.BitAnd: lambda a, b: a & b, ast.BitOr: lambda a, b: a | b,
    ast.BitXor: lambda a, b: a ^ b, ast.LShift: lambda a, b: a << b,
    ast.RShift: lambda a, b: a >> b,
}  # fmt: skip
_CMPOPS: dict[type, Callable[[Any, Any], bool]] = {
    ast.Eq: lambda a, b: a == b, ast.NotEq: lambda a, b: a != b, ast.Lt: lambda a, b: a < b,
    ast.LtE: lambda a, b: a <= b, ast.Gt: lambda a, b: a > b, ast.GtE: lambda a, b: a >= b,
    ast.In: lambda a, b: a in b, ast.NotIn: lambda a, b: a not in b,
    ast.Is: lambda a, b: a is b, ast.IsNot: lambda a, b: a is not b,
}  # fmt: skip


class Interpreter:
    def __init__(self, host: dict[str, Any], limits: Limits) -> None:
        self.limits = limits
        self.steps = 0
        self.depth = 0
        self.output: list[str] = []
        self.output_chars = 0
        self.output_truncated = False
        self.deadline = time.monotonic() + limits.timeout_seconds
        self.globals = Scope({**self._builtins(), **host})
        self._handling: list[SandboxException] = []

    # ---------------------------------------------------------------- accounting

    def tick(self) -> None:
        self.steps += 1
        if self.steps > self.limits.max_steps:
            raise LimitExceeded(f"step limit of {self.limits.max_steps} exceeded")
        if self.steps % 500 == 0 and time.monotonic() > self.deadline:
            raise LimitExceeded(f"time limit of {self.limits.timeout_seconds:g}s exceeded")

    def check_size(self, value: Any) -> Any:
        if isinstance(value, str):
            if len(value) > self.limits.max_string_length:
                raise LimitExceeded(f"string longer than {self.limits.max_string_length} chars")
        elif isinstance(value, list | tuple | dict | set):
            if len(value) > self.limits.max_collection_size:
                raise LimitExceeded(
                    f"collection larger than {self.limits.max_collection_size} items"
                )
        elif (
            isinstance(value, int)
            and not isinstance(value, bool)
            and value.bit_length() > self.limits.max_int_bits
        ):
            raise LimitExceeded("integer too large")
        return value

    def write(self, text: str) -> None:
        room = self.limits.max_output_chars - self.output_chars
        if room <= 0:
            self.output_truncated = True
            return
        if len(text) > room:
            text, self.output_truncated = text[:room], True
        self.output.append(text)
        self.output_chars += len(text)

    # ---------------------------------------------------------------- statements

    async def run_module(self, tree: ast.Module) -> Any:
        last: Any = None
        for index, node in enumerate(tree.body):
            if index == len(tree.body) - 1 and isinstance(node, ast.Expr):
                # REPL-style: the final expression's value is the result.
                await self.exec_stmt(
                    ast.Assign([ast.Name("__last__", ast.Store())], node.value), self.globals
                )
                last = self.globals.vars.pop("__last__")
            else:
                await self.exec_stmt(node, self.globals)
        return self.globals.vars.get("result", last) if last is None else last

    async def exec_block(self, body: list[ast.stmt], scope: Scope) -> None:
        for node in body:
            await self.exec_stmt(node, scope)

    async def exec_stmt(self, node: ast.stmt, scope: Scope) -> None:
        self.tick()
        try:
            await getattr(self, f"_s_{type(node).__name__}")(node, scope)
        except _CONTROL:
            raise
        except RecursionError:
            raise LimitExceeded("code is nested too deeply") from None
        except (ArithmeticError, LookupError, TypeError, ValueError, AttributeError) as exc:
            raise SandboxException(type(exc).__name__, str(exc)) from None

    async def _s_Expr(self, node: ast.Expr, scope: Scope) -> None:
        await self.ev(node.value, scope)

    async def _s_Pass(self, node: ast.Pass, scope: Scope) -> None:
        return None

    async def _s_Break(self, node: ast.Break, scope: Scope) -> None:
        raise _Break

    async def _s_Continue(self, node: ast.Continue, scope: Scope) -> None:
        raise _Continue

    async def _s_Return(self, node: ast.Return, scope: Scope) -> None:
        raise _Return(await self.ev(node.value, scope) if node.value else None)

    async def _s_Assign(self, node: ast.Assign, scope: Scope) -> None:
        value = await self.ev(node.value, scope)
        for target in node.targets:
            await self.assign(target, value, scope)

    async def _s_AnnAssign(self, node: ast.AnnAssign, scope: Scope) -> None:
        if node.value is not None:
            await self.assign(node.target, await self.ev(node.value, scope), scope)

    async def _s_AugAssign(self, node: ast.AugAssign, scope: Scope) -> None:
        load = ast.Name(node.target.id, ast.Load()) if isinstance(node.target, ast.Name) else None
        current = await self.ev(
            load or ast.Subscript(node.target.value, node.target.slice, ast.Load()),
            scope,  # type: ignore[union-attr]
        )
        value = self.binop(node.op, current, await self.ev(node.value, scope))
        await self.assign(node.target, value, scope)

    async def _s_Delete(self, node: ast.Delete, scope: Scope) -> None:
        for target in node.targets:
            if isinstance(target, ast.Subscript):
                container = await self.ev(target.value, scope)
                if not isinstance(container, list | dict):
                    raise SandboxException("TypeError", "only list and dict items can be deleted")
                del container[await self.subscript_key(target.slice, scope)]
            elif isinstance(target, ast.Name):
                scope.vars.pop(target.id, None)

    async def _s_If(self, node: ast.If, scope: Scope) -> None:
        branch = node.body if await self.ev(node.test, scope) else node.orelse
        await self.exec_block(branch, scope)

    async def _s_While(self, node: ast.While, scope: Scope) -> None:
        while await self.ev(node.test, scope):
            self.tick()
            try:
                await self.exec_block(node.body, scope)
            except _Break:
                return
            except _Continue:
                continue
        await self.exec_block(node.orelse, scope)

    async def _s_For(self, node: ast.For, scope: Scope) -> None:
        for item in self.iterate(await self.ev(node.iter, scope)):
            self.tick()
            await self.assign(node.target, item, scope)
            try:
                await self.exec_block(node.body, scope)
            except _Break:
                return
            except _Continue:
                continue
        await self.exec_block(node.orelse, scope)

    async def _s_FunctionDef(self, node: ast.FunctionDef, scope: Scope) -> None:
        scope.vars[node.name] = await self.make_function(node.name, node.args, node.body, scope)

    async def _s_Try(self, node: ast.Try, scope: Scope) -> None:
        try:
            try:
                await self.exec_block(node.body, scope)
            except SandboxException as exc:
                for handler in node.handlers:
                    if await self.handler_matches(handler, exc, scope):
                        if handler.name:
                            scope.vars[handler.name] = exc
                        self._handling.append(exc)
                        try:
                            await self.exec_block(handler.body, scope)
                        finally:
                            self._handling.pop()
                        break
                else:
                    raise
            else:
                await self.exec_block(node.orelse, scope)
        finally:
            if node.finalbody:
                await self.exec_block(node.finalbody, scope)

    async def handler_matches(
        self, handler: ast.ExceptHandler, exc: SandboxException, scope: Scope
    ) -> bool:
        if handler.type is None:
            return True
        wanted = await self.ev(handler.type, scope)
        types = wanted if isinstance(wanted, tuple) else (wanted,)
        for item in types:
            if not isinstance(item, ExceptionType):
                raise SandboxException("TypeError", "except needs exception classes")
            if _is_subclass(exc.type_name, item.name):
                return True
        return False

    async def _s_Raise(self, node: ast.Raise, scope: Scope) -> None:
        if node.exc is None:
            if not self._handling:
                raise SandboxException("RuntimeError", "no active exception to re-raise")
            raise self._handling[-1]
        value = await self.ev(node.exc, scope)
        if isinstance(value, ExceptionType):
            value = SandboxException(value.name)
        if not isinstance(value, SandboxException):
            raise SandboxException("TypeError", "exceptions must be exception instances")
        raise value

    async def _s_Assert(self, node: ast.Assert, scope: Scope) -> None:
        if not await self.ev(node.test, scope):
            message = str(await self.ev(node.msg, scope)) if node.msg else ""
            raise SandboxException("AssertionError", message)

    async def assign(self, target: ast.expr, value: Any, scope: Scope) -> None:
        if isinstance(target, ast.Name):
            scope.vars[target.id] = value
        elif isinstance(target, ast.Tuple | ast.List):
            items = list(self.iterate(value))
            starred = [i for i, t in enumerate(target.elts) if isinstance(t, ast.Starred)]
            if starred:
                at = starred[0]
                after = len(target.elts) - at - 1
                if len(items) < len(target.elts) - 1:
                    raise SandboxException("ValueError", "not enough values to unpack")
                parts = [*items[:at], items[at : len(items) - after], *items[len(items) - after :]]
            else:
                if len(items) != len(target.elts):
                    raise SandboxException(
                        "ValueError",
                        f"expected {len(target.elts)} values to unpack, got {len(items)}",
                    )
                parts = items
            for sub, item in zip(target.elts, parts, strict=True):
                await self.assign(sub.value if isinstance(sub, ast.Starred) else sub, item, scope)
        elif isinstance(target, ast.Subscript):
            container = await self.ev(target.value, scope)
            if not isinstance(container, list | dict):
                raise SandboxException("TypeError", "only list and dict items can be assigned")
            container[await self.subscript_key(target.slice, scope)] = value
            self.check_size(container)
        else:
            raise SandboxException("SyntaxError", f"cannot assign to {type(target).__name__}")

    # ---------------------------------------------------------------- expressions

    async def ev(self, node: ast.expr, scope: Scope) -> Any:
        self.tick()
        return await getattr(self, f"_e_{type(node).__name__}")(node, scope)

    async def _e_Constant(self, node: ast.Constant, scope: Scope) -> Any:
        if not isinstance(node.value, str | int | float | bool | type(None)):
            raise SandboxException("TypeError", f"unsupported literal {type(node.value).__name__}")
        return node.value

    async def _e_Name(self, node: ast.Name, scope: Scope) -> Any:
        return scope.lookup(node.id)

    async def _e_Await(self, node: ast.Await, scope: Scope) -> Any:
        return await self.ev(node.value, scope)  # tool calls are already awaited

    async def _items(self, elts: list[ast.expr], scope: Scope) -> list[Any]:
        items: list[Any] = []
        for elt in elts:
            if isinstance(elt, ast.Starred):
                items.extend(self.iterate(await self.ev(elt.value, scope)))
            else:
                items.append(await self.ev(elt, scope))
            self.check_size(items)
        return items

    async def _e_List(self, node: ast.List, scope: Scope) -> list[Any]:
        return await self._items(node.elts, scope)

    async def _e_Tuple(self, node: ast.Tuple, scope: Scope) -> tuple[Any, ...]:
        return tuple(await self._items(node.elts, scope))

    async def _e_Set(self, node: ast.Set, scope: Scope) -> set[Any]:
        return set(await self._items(node.elts, scope))

    async def _e_Dict(self, node: ast.Dict, scope: Scope) -> dict[Any, Any]:
        result: dict[Any, Any] = {}
        for key, value in zip(node.keys, node.values, strict=True):
            if key is None:  # {**other}
                other = await self.ev(value, scope)
                if not isinstance(other, dict):
                    raise SandboxException("TypeError", "** needs a dict")
                result.update(other)
            else:
                result[await self.ev(key, scope)] = await self.ev(value, scope)
            self.check_size(result)
        return result

    async def _e_BinOp(self, node: ast.BinOp, scope: Scope) -> Any:
        return self.binop(
            node.op, await self.ev(node.left, scope), await self.ev(node.right, scope)
        )

    def binop(self, op: ast.operator, left: Any, right: Any) -> Any:
        if isinstance(op, ast.Mult):
            seq, count = (left, right) if isinstance(right, int) else (right, left)
            if isinstance(seq, str | list | tuple) and isinstance(count, int):
                limit = (
                    self.limits.max_string_length
                    if isinstance(seq, str)
                    else self.limits.max_collection_size
                )
                if len(seq) * max(count, 0) > limit:
                    raise LimitExceeded("repetition result too large")
        if (
            isinstance(op, ast.Pow)
            and isinstance(left, int)
            and isinstance(right, int)
            and right > 0
            and abs(left) > 1
            and abs(left).bit_length() * right > self.limits.max_int_bits
        ):
            raise LimitExceeded("integer too large")
        if (
            isinstance(op, ast.LShift)
            and isinstance(right, int)
            and right > self.limits.max_int_bits
        ):
            raise LimitExceeded("integer too large")
        operation = _BINOPS.get(type(op))
        if operation is None:
            raise SandboxException("SyntaxError", f"operator {type(op).__name__} is not supported")
        return self.check_size(operation(left, right))

    async def _e_UnaryOp(self, node: ast.UnaryOp, scope: Scope) -> Any:
        value = await self.ev(node.operand, scope)
        if isinstance(node.op, ast.Not):
            return not value
        if isinstance(node.op, ast.USub):
            return -value
        if isinstance(node.op, ast.UAdd):
            return +value
        return ~value

    async def _e_BoolOp(self, node: ast.BoolOp, scope: Scope) -> Any:
        value: Any = None
        for operand in node.values:
            value = await self.ev(operand, scope)
            if isinstance(node.op, ast.And) and not value:
                return value
            if isinstance(node.op, ast.Or) and value:
                return value
        return value

    async def _e_Compare(self, node: ast.Compare, scope: Scope) -> bool:
        left = await self.ev(node.left, scope)
        for op, comparator in zip(node.ops, node.comparators, strict=True):
            right = await self.ev(comparator, scope)
            if not _CMPOPS[type(op)](left, right):
                return False
            left = right
        return True

    async def _e_IfExp(self, node: ast.IfExp, scope: Scope) -> Any:
        return await self.ev(node.body if await self.ev(node.test, scope) else node.orelse, scope)

    async def _e_JoinedStr(self, node: ast.JoinedStr, scope: Scope) -> str:
        parts: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant):
                parts.append(str(value.value))
                continue
            assert isinstance(value, ast.FormattedValue)  # noqa: S101 - guaranteed by ast
            item = await self.ev(value.value, scope)
            if value.conversion == ord("r"):
                item = repr(item)
            elif value.conversion in (ord("s"), ord("a")):
                item = str(item)
            spec = await self.ev(value.format_spec, scope) if value.format_spec else ""
            # format() dispatches to the value's own __format__; plain data types only.
            if not isinstance(item, str | int | float | bool | type(None)):
                item = str(item)
            parts.append(format(item, spec))
        return self.check_size("".join(parts))

    async def _e_Attribute(self, node: ast.Attribute, scope: Scope) -> Any:
        obj = await self.ev(node.value, scope)
        name = node.attr
        if isinstance(obj, Namespace):
            if name not in obj.members:
                raise SandboxException("AttributeError", f"{obj.name} has no attribute {name!r}")
            return obj.members[name]
        if isinstance(obj, SandboxException):
            if name in ("message", "args"):
                return obj.message if name == "message" else (obj.message,)
            if name == "type":
                return obj.type_name
        allowed = _METHODS.get(type(obj), frozenset())
        if name not in allowed:
            raise SandboxException(
                "AttributeError", f"{type(obj).__name__!s} object has no usable attribute {name!r}"
            )
        return self.bound_method(obj, name)

    def bound_method(self, obj: Any, name: str) -> HostFunction:
        method = getattr(obj, name)

        async def call(*args: Any, **kwargs: Any) -> Any:
            if isinstance(obj, list) and name == "sort":
                return await self._sort_in_place(obj, **kwargs)
            if isinstance(obj, str) and name in _GROWING_STR:
                if name == "join":
                    args = (list(self.iterate(args[0])), *args[1:]) if args else args
                if name in {"center", "ljust", "rjust", "zfill"} and args:
                    self.check_size(" " * min(int(args[0]), self.limits.max_string_length + 1))
            result = method(*args, **kwargs)
            if name in _MUTATING:
                self.check_size(obj)
            if isinstance(result, type({}.keys()) | type({}.values()) | type({}.items())):
                result = list(result)
            return self.check_size(result)

        return HostFunction(f"{type(obj).__name__}.{name}", call)

    async def _sort_in_place(
        self, items: list[Any], key: Any = None, reverse: bool = False
    ) -> None:
        if key is None:
            items.sort(reverse=reverse)
            return
        keys = [await self.call(key, [item], {}) for item in items]
        order = sorted(range(len(items)), key=keys.__getitem__, reverse=reverse)
        items[:] = [items[i] for i in order]

    async def subscript_key(self, node: ast.expr, scope: Scope) -> Any:
        if isinstance(node, ast.Slice):
            return slice(
                await self.ev(node.lower, scope) if node.lower else None,
                await self.ev(node.upper, scope) if node.upper else None,
                await self.ev(node.step, scope) if node.step else None,
            )
        return await self.ev(node, scope)

    async def _e_Subscript(self, node: ast.Subscript, scope: Scope) -> Any:
        container = await self.ev(node.value, scope)
        if not isinstance(container, str | list | tuple | dict | range):
            raise SandboxException("TypeError", f"{type(container).__name__} is not subscriptable")
        return container[await self.subscript_key(node.slice, scope)]

    async def _e_Starred(self, node: ast.Starred, scope: Scope) -> Any:
        raise SandboxException("SyntaxError", "starred expression is not allowed here")

    async def _e_Lambda(self, node: ast.Lambda, scope: Scope) -> SandboxFunction:
        return await self.make_function("<lambda>", node.args, node.body, scope)

    async def _comprehension(
        self, generators: list[ast.comprehension], scope: Scope, emit: Callable[[Scope], Any]
    ) -> None:
        async def loop(index: int, inner: Scope) -> None:
            if index == len(generators):
                await emit(inner)
                return
            generator = generators[index]
            for item in self.iterate(await self.ev(generator.iter, inner)):
                self.tick()
                await self.assign(generator.target, item, inner)
                if all([await self.ev(cond, inner) for cond in generator.ifs]):
                    await loop(index + 1, inner)

        await loop(0, Scope({}, scope))

    async def _e_ListComp(self, node: ast.ListComp, scope: Scope) -> list[Any]:
        result: list[Any] = []

        async def emit(inner: Scope) -> None:
            result.append(await self.ev(node.elt, inner))
            self.check_size(result)

        await self._comprehension(node.generators, scope, emit)
        return result

    _e_GeneratorExp = _e_ListComp  # evaluated eagerly; fine for sum(), any(), ...

    async def _e_SetComp(self, node: ast.SetComp, scope: Scope) -> set[Any]:
        return set(await self._e_ListComp(node, scope))  # type: ignore[arg-type]

    async def _e_DictComp(self, node: ast.DictComp, scope: Scope) -> dict[Any, Any]:
        result: dict[Any, Any] = {}

        async def emit(inner: Scope) -> None:
            result[await self.ev(node.key, inner)] = await self.ev(node.value, inner)
            self.check_size(result)

        await self._comprehension(node.generators, scope, emit)
        return result

    async def _e_Call(self, node: ast.Call, scope: Scope) -> Any:
        func = await self.ev(node.func, scope)
        args = await self._items(node.args, scope)
        kwargs: dict[str, Any] = {}
        for keyword in node.keywords:
            value = await self.ev(keyword.value, scope)
            if keyword.arg is None:
                if not isinstance(value, dict):
                    raise SandboxException("TypeError", "** needs a dict")
                kwargs.update(value)
            else:
                kwargs[keyword.arg] = value
        return await self.call(func, args, kwargs)

    # ---------------------------------------------------------------- calling

    async def make_function(
        self, name: str, args: ast.arguments, body: list[ast.stmt] | ast.expr, scope: Scope
    ) -> SandboxFunction:
        defaults = [await self.ev(d, scope) for d in args.defaults]
        kw_defaults = {
            a.arg: await self.ev(d, scope)
            for a, d in zip(args.kwonlyargs, args.kw_defaults, strict=True)
            if d is not None
        }
        return SandboxFunction(name, args, body, scope, defaults, kw_defaults)

    async def call(self, func: Any, args: list[Any], kwargs: dict[str, Any]) -> Any:
        if isinstance(func, SandboxFunction):
            return await self.call_sandbox_function(func, args, kwargs)
        if isinstance(func, HostFunction):
            result = func.fn(*args, **kwargs)
            if inspect.isawaitable(result):
                result = await result
            return result
        if isinstance(func, PyType):
            sized = args and isinstance(args[0], range | str | list | tuple | dict | set)
            if sized and len(args[0]) > self.limits.max_collection_size and func.type is not str:
                raise LimitExceeded("collection too large")
            return self.check_size(func.type(*args, **kwargs))
        if isinstance(func, ExceptionType):
            return SandboxException(func.name, str(args[0]) if args else "")
        raise SandboxException("TypeError", f"{type(func).__name__} object is not callable")

    async def call_sandbox_function(
        self, func: SandboxFunction, args: list[Any], kwargs: dict[str, Any]
    ) -> Any:
        self.depth += 1
        if self.depth > self.limits.max_call_depth:
            raise LimitExceeded(
                f"call depth over {self.limits.max_call_depth} (runaway recursion?)"
            )
        try:
            scope = Scope(self.bind_arguments(func, args, kwargs), func.closure)
            if isinstance(func.body, ast.expr):
                return await self.ev(func.body, scope)
            try:
                await self.exec_block(func.body, scope)
            except _Return as returned:
                return returned.value
            return None
        finally:
            self.depth -= 1

    def bind_arguments(
        self, func: SandboxFunction, args: list[Any], kwargs: dict[str, Any]
    ) -> dict[str, Any]:
        spec = func.args
        positional = [a.arg for a in (*spec.posonlyargs, *spec.args)]
        bound: dict[str, Any] = {}
        for name, value in zip(positional, args, strict=False):
            bound[name] = value
        extra = args[len(positional) :]
        if extra and spec.vararg is None:
            raise SandboxException("TypeError", f"{func.name}() got too many positional arguments")
        if spec.vararg is not None:
            bound[spec.vararg.arg] = tuple(extra)
        keyword_names = set(positional) | {a.arg for a in spec.kwonlyargs}
        rest: dict[str, Any] = {}
        for name, value in kwargs.items():
            if name in keyword_names:
                if name in bound:
                    raise SandboxException(
                        "TypeError", f"{func.name}() got multiple values for {name!r}"
                    )
                bound[name] = value
            elif spec.kwarg is not None:
                rest[name] = value
            else:
                raise SandboxException(
                    "TypeError", f"{func.name}() got an unexpected keyword {name!r}"
                )
        if spec.kwarg is not None:
            bound[spec.kwarg.arg] = rest
        first_default = len(positional) - len(func.defaults)
        for index, name in enumerate(positional):
            if name not in bound:
                if index < first_default:
                    raise SandboxException("TypeError", f"{func.name}() missing argument {name!r}")
                bound[name] = func.defaults[index - first_default]
        for arg in spec.kwonlyargs:
            if arg.arg not in bound:
                if arg.arg not in func.kw_defaults:
                    raise SandboxException(
                        "TypeError", f"{func.name}() missing argument {arg.arg!r}"
                    )
                bound[arg.arg] = func.kw_defaults[arg.arg]
        return bound

    def iterate(self, value: Any) -> Iterable[Any]:
        if isinstance(value, dict):
            return list(value)
        if isinstance(value, str | list | tuple | set | range):
            return value
        raise SandboxException("TypeError", f"{type(value).__name__} object is not iterable")

    # ---------------------------------------------------------------- built-ins

    def _builtins(self) -> dict[str, Any]:
        def host(name: str) -> Callable[[Callable[..., Any]], HostFunction]:
            return lambda fn: HostFunction(name, fn)

        def collect(values: Iterable[Any]) -> list[Any]:
            if isinstance(values, list):
                return values  # already within limits
            items = self.iterate(values)
            # Check the size *before* materializing: list(range(10**9)) must never be built.
            if len(items) > self.limits.max_collection_size:  # type: ignore[arg-type]
                raise LimitExceeded(
                    f"collection larger than {self.limits.max_collection_size} items"
                )
            return list(items)

        async def keyed(values: Any, key: Any) -> list[tuple[Any, Any]]:
            return [(await self.call(key, [v], {}), v) for v in self.iterate(values)]

        def print_(*args: Any, sep: str = " ", end: str = "\n") -> None:
            self.write(sep.join(str(a) for a in args) + end)

        async def sorted_(values: Any, key: Any = None, reverse: bool = False) -> list[Any]:
            items = collect(values)
            await self._sort_in_place(items, key=key, reverse=reverse)
            return items

        async def extreme(
            pick: Callable[..., Any], *values: Any, key: Any = None, **kw: Any
        ) -> Any:
            items = collect(values[0]) if len(values) == 1 else list(values)
            if not items:
                if "default" in kw:
                    return kw["default"]
                raise SandboxException("ValueError", "empty sequence")
            if key is None:
                return pick(items)
            return pick(await keyed(items, key), key=lambda pair: pair[0])[1]

        async def map_(func: Any, *iterables: Any) -> list[Any]:
            rows = list(zip(*(collect(i) for i in iterables), strict=False))
            return self.check_size([await self.call(func, list(row), {}) for row in rows])

        async def filter_(func: Any, values: Any) -> list[Any]:
            items = collect(values)
            if func is None:
                return [v for v in items if v]
            return [v for v in items if await self.call(func, [v], {})]

        def range_(*args: int) -> range:
            if not all(isinstance(a, int) for a in args):
                raise SandboxException("TypeError", "range() needs integers")
            return range(*args)

        def isinstance_(value: Any, types: Any) -> bool:
            options = types if isinstance(types, tuple) else (types,)
            for option in options:
                if isinstance(option, PyType) and isinstance(value, option.type):
                    return True
                if (
                    isinstance(option, ExceptionType)
                    and isinstance(value, SandboxException)
                    and _is_subclass(value.type_name, option.name)
                ):
                    return True
            return False

        builtins: dict[str, Any] = {
            name: PyType(name, t)
            for name, t in {
                "int": int, "float": float, "str": str, "bool": bool, "list": list,
                "dict": dict, "set": set, "tuple": tuple,
            }.items()
        }  # fmt: skip
        builtins |= {name: ExceptionType(name) for name in _EXCEPTIONS}
        builtins |= {
            "print": host("print")(print_),
            "len": host("len")(len),
            "range": host("range")(range_),
            "abs": host("abs")(abs),
            "round": host("round")(round),
            "sum": host("sum")(
                lambda values, start=0: self.check_size(sum(collect(values), start))
            ),
            "min": host("min")(lambda *v, **k: extreme(min, *v, **k)),
            "max": host("max")(lambda *v, **k: extreme(max, *v, **k)),
            "sorted": host("sorted")(sorted_),
            "reversed": host("reversed")(lambda values: collect(values)[::-1]),
            "enumerate": host("enumerate")(
                lambda values, start=0: list(enumerate(collect(values), start))
            ),
            "zip": host("zip")(
                lambda *values: list(zip(*(collect(v) for v in values), strict=False))
            ),
            "any": host("any")(lambda values: any(collect(values))),
            "all": host("all")(lambda values: all(collect(values))),
            "map": host("map")(map_),
            "filter": host("filter")(filter_),
            "isinstance": host("isinstance")(isinstance_),
            "repr": host("repr")(repr),
            "divmod": host("divmod")(divmod),
            "chr": host("chr")(chr),
            "ord": host("ord")(ord),
            "json": Namespace(
                "json",
                {
                    "dumps": host("json.dumps")(
                        lambda value, indent=None: self.check_size(
                            json.dumps(value, indent=indent, default=str, ensure_ascii=False)
                        )
                    ),
                    "loads": host("json.loads")(
                        lambda text: json.loads(self.check_size(str(text)))
                    ),
                },
            ),
            "math": Namespace(
                "math",
                {
                    **{
                        n: host(f"math.{n}")(getattr(math, n))
                        for n in (
                            "floor",
                            "ceil",
                            "sqrt",
                            "log",
                            "log10",
                            "isclose",
                            "fabs",
                            "trunc",
                        )
                    },
                    "pi": math.pi,
                    "e": math.e,
                    "inf": math.inf,
                },
            ),  # fmt: skip
            "statistics": Namespace(
                "statistics",
                {
                    n: host(f"statistics.{n}")(
                        lambda values, _f=getattr(statistics, n): _f(collect(values))
                    )
                    for n in ("mean", "median", "pstdev", "stdev", "mode")
                },
            ),  # fmt: skip
            "dates": Namespace(
                "dates",
                {
                    "today": host("dates.today")(lambda: date.today().isoformat()),
                    "days_between": host("dates.days_between")(
                        lambda start, end: (_date(end) - _date(start)).days
                    ),
                    "add_days": host("dates.add_days")(
                        lambda day, days: (_date(day) + timedelta(days=int(days))).isoformat()
                    ),
                },
            ),
        }
        return builtins


def _date(value: str) -> date:
    return date.fromisoformat(str(value)[:10])


def to_plain(value: Any) -> Any:
    """Make host data safe for the sandbox (and its result safe for JSON): plain data only."""
    return json.loads(json.dumps(value, default=str))


async def run(code: str, host: dict[str, Any], limits: Limits | None = None) -> RunResult:
    """Run sandboxed code with the given host namespaces. Never raises."""
    limits = limits or Limits()
    interpreter = Interpreter(host, limits)
    try:
        tree = validate(code, limits)
        value = await asyncio.wait_for(interpreter.run_module(tree), timeout=limits.timeout_seconds)
        result = to_plain(value) if value is not None else None
        return RunResult(True, result, "".join(interpreter.output), steps=interpreter.steps,
                         output_truncated=interpreter.output_truncated)  # fmt: skip
    except (SandboxException, LimitExceeded, TimeoutError) as exc:
        if isinstance(exc, TimeoutError):
            error_type, message = (
                "LimitExceeded",
                f"time limit of {limits.timeout_seconds:g}s exceeded",
            )
        elif isinstance(exc, LimitExceeded):
            error_type, message = "LimitExceeded", str(exc)
        else:
            error_type, message = exc.type_name, exc.message
        return RunResult(
            False, None, "".join(interpreter.output), message, error_type,
            interpreter.steps, output_truncated=interpreter.output_truncated,
        )  # fmt: skip
    except (_Return, _Break, _Continue):
        return RunResult(False, None, "".join(interpreter.output),
                         "return/break/continue outside a function or loop", "SyntaxError",
                         interpreter.steps)  # fmt: skip


__all__ = [
    "HostFunction", "Limits", "LimitExceeded", "Namespace", "RunResult", "SandboxException",
    "run", "to_plain", "validate",
]  # fmt: skip
