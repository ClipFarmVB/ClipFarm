"""Print a redacted diagnostic summary of a Claude review run.

This repo is public, so Actions logs are world-readable. `show_full_output: true`
publishes everything Claude prints — including its reasoning over the diff — which
is why it is not set. But a review that runs cleanly and posts nothing is then
undebuggable.

This prints only *structural* facts: how many turns, which tools were denied and
how often, and what error types occurred. It never prints assistant text, tool
inputs, tool results, or file contents. Any token-shaped string that does slip
into a field we print is redacted as a backstop.

Usage: python review_diagnostics.py <claude-execution-output.json>
Exits 0 always — diagnostics must never fail the job.
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter

# Backstop only: nothing we deliberately print should contain a credential, but
# redact anything token-shaped in case a payload field carries one.
_SECRET_RE = re.compile(
    r"(gh[pousr]_[A-Za-z0-9]{16,}"
    r"|sk-ant-[A-Za-z0-9_\-]{16,}"
    r"|ey[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{10,}"
    r"|[A-Za-z0-9_\-]{32,})"
)

# Fields safe to echo: short, structural, no user content.
_SAFE_RESULT_KEYS = (
    "subtype",
    "is_error",
    "num_turns",
    "duration_ms",
    "total_cost_usd",
    "permission_denials_count",
)


def redact(value: object, limit: int = 120) -> str:
    text = str(value)
    text = _SECRET_RE.sub("<redacted>", text)
    text = " ".join(text.split())
    return text[:limit] + ("..." if len(text) > limit else "")


def load(path: str) -> list[dict]:
    """The action's log has been seen both as one JSON value and as JSONL."""
    raw = open(path, encoding="utf-8", errors="replace").read()
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, list) else [parsed]
    except json.JSONDecodeError:
        pass
    out: list[dict] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


def walk(node: object):
    """Yield every dict in the structure, at any depth."""
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from walk(v)


_DENIAL_RE = re.compile(
    r"permission|denied|not allowed|requested permissions|granted", re.IGNORECASE
)


def text_of(value: object) -> str:
    """Flatten a `content` field to plain text.

    The SDK writes it as a bare string, a block dict, or a list of blocks
    depending on the tool, so all three shapes have to work.
    """
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return str(value.get("text") or value.get("content") or "")
    if isinstance(value, list):
        return " ".join(text_of(v) for v in value)
    return ""


def first_line(raw: str, limit: int = 200) -> str:
    """First line only, capped and redacted.

    Errors are short and diagnostic; a tool_result body can be arbitrarily
    large and may echo file contents, so never take more than the first line.
    """
    stripped = raw.strip()
    if not stripped:
        return "unspecified"
    return redact(stripped.splitlines()[0], limit)


def main() -> int:
    if len(sys.argv) < 2:
        print("review-diagnostics: no log path given")
        return 0
    path = sys.argv[1]
    try:
        entries = load(path)
    except OSError as exc:
        print(f"review-diagnostics: could not read log ({exc.__class__.__name__})")
        return 0

    if not entries:
        print("review-diagnostics: log was empty or unparseable")
        return 0

    denied_by_tool: Counter[str] = Counter()
    denial_examples: dict[str, str] = {}
    error_texts: Counter[str] = Counter()
    tool_calls: Counter[str] = Counter()
    result: dict = {}

    # Pass 1: the result block, and a tool_use_id -> tool name map. Errors arrive
    # on tool_result blocks, which name only the id — without this map every
    # denial is attributed to "unknown".
    tool_names: dict[str, str] = {}
    for node in walk(entries):
        if node.get("type") == "result":
            result = node
        if node.get("type") in {"tool_use", "server_tool_use"}:
            name = node.get("name") or node.get("tool_name")
            if isinstance(name, str):
                tool_calls[name] += 1
                tid = node.get("id")
                if isinstance(tid, str):
                    tool_names[tid] = name

    # Pass 2: walk top-level entries, because the error text often sits in a
    # `tool_use_result` field on the *envelope* rather than inside the
    # tool_result block itself. Missing that is why everything previously came
    # back as "unspecified".
    for entry in entries:
        sibling = entry.get("tool_use_result") if isinstance(entry, dict) else None
        for node in walk(entry):
            if node.get("type") != "tool_result" or node.get("is_error") is not True:
                continue
            tool = tool_names.get(str(node.get("tool_use_id")), "unknown")
            raw = text_of(node.get("content")) or text_of(sibling)
            msg = first_line(raw)
            if _DENIAL_RE.search(raw):
                denied_by_tool[tool] += 1
                denial_examples.setdefault(tool, msg)
            else:
                error_texts[f"{tool}: {msg}"] += 1

    # ASCII only: this has to survive a cp1252 console as well as a UTF-8 runner.
    print("--- review diagnostics (redacted) ---")
    if result:
        summary = {k: result.get(k) for k in _SAFE_RESULT_KEYS if k in result}
        print(f"result: {summary}")

    reported = result.get("permission_denials_count")
    if reported:
        print(f"permission denials reported by SDK: {reported}")

    if denied_by_tool:
        print("denials attributed by tool:")
        for tool, count in denied_by_tool.most_common():
            example = denial_examples.get(tool)
            suffix = f"  e.g. {example}" if example else ""
            print(f"  {tool}: {count}{suffix}")
    elif reported:
        print(
            "denials attributed by tool: none matched - the log shape differs from\n"
            "  what this parser expects. Fix the matcher rather than assuming zero."
        )

    if tool_calls:
        top = ", ".join(f"{n} x{c}" for n, c in tool_calls.most_common(8))
        print(f"tool calls attempted: {top}")

    if error_texts:
        print("errors:")
        for msg, count in error_texts.most_common(8):
            print(f"  (x{count}) {msg}")

    print("--- end diagnostics ---")
    return 0


if __name__ == "__main__":
    sys.exit(main())
