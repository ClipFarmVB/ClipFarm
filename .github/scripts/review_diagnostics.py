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

    for node in walk(entries):
        if node.get("type") == "result":
            result = node

        name = node.get("name") or node.get("tool_name")
        if isinstance(name, str) and node.get("type") in {"tool_use", "server_tool_use"}:
            tool_calls[name] += 1

        blob = " ".join(
            str(node.get(k, "")) for k in ("permission_denial", "denial_reason", "subtype", "error")
        ).lower()
        looks_denied = (
            node.get("permission_denied") is True
            or "permission" in blob and "deni" in blob
            or node.get("subtype") == "permission_denial"
        )
        if looks_denied:
            tool = name if isinstance(name, str) else str(node.get("tool", "unknown"))
            denied_by_tool[tool] += 1
            # One short example per tool — enough to identify the pattern.
            if tool not in denial_examples:
                sample = node.get("input") or node.get("command") or node.get("message") or ""
                if sample:
                    denial_examples[tool] = redact(sample)

        if node.get("is_error") is True:
            msg = node.get("error") or node.get("message") or node.get("subtype") or "unspecified"
            error_texts[redact(msg, 160)] += 1

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
