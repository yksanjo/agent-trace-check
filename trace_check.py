"""agent-trace-check: offline validator for agent tool-call traces.

A trace is JSONL, one record per line:
  {"type": "call",   "id": "c1", "tool": "web.search", "ts": 1.0, ...}
  {"type": "result", "id": "c1", "ok": true,           "ts": 1.2, ...}

Every record needs "type" ("call" or "result") and a non-empty string "id"
pairing a result to its call. "ts" is optional but must be a finite number
when present.

The validator is purely structural: it never executes anything, never calls
a model, and says nothing about whether the calls themselves were right.
"""

import json
import math
import sys

MAX_BYTES = 1 << 20          # 1 MiB trace cap
MAX_RECORDS = 10_000         # record count cap
MAX_DEPTH = 64               # JSON nesting cap per record

ERROR = "error"
WARNING = "warning"


class InvalidInput(Exception):
    """Raised when the trace cannot be parsed within the safety caps."""


def _check_depth(node, depth=0):
    if depth > MAX_DEPTH:
        raise InvalidInput(f"JSON nesting deeper than {MAX_DEPTH}")
    if isinstance(node, dict):
        for v in node.values():
            _check_depth(v, depth + 1)
    elif isinstance(node, list):
        for v in node:
            _check_depth(v, depth + 1)


def load_trace(text):
    """Parse JSONL trace text, enforcing size, count and depth caps."""
    if len(text.encode("utf-8", "replace")) > MAX_BYTES:
        raise InvalidInput(f"trace larger than {MAX_BYTES} bytes")
    records = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        if len(records) >= MAX_RECORDS:
            raise InvalidInput(f"more than {MAX_RECORDS} records")
        try:
            rec = json.loads(line)
        except (json.JSONDecodeError, RecursionError) as e:
            raise InvalidInput(f"line {lineno}: invalid JSON: {e}")
        _check_depth(rec)
        if not isinstance(rec, dict):
            raise InvalidInput(f"line {lineno}: record is not an object")
        records.append((lineno, rec))
    return records


def check_trace(records):
    """Return deterministically ordered findings for parsed records.

    `records` is a list of (lineno, dict) from load_trace. Each finding is
    {severity, code, line, message}.
    """
    findings = []

    def add(sev, code, line, msg):
        findings.append({"severity": sev, "code": code, "line": line,
                         "message": msg})

    calls = {}          # id -> (lineno, ts or None)
    results = {}        # id -> (lineno, ts or None)
    last_ts = None
    monotonic = True

    for lineno, rec in records:
        rtype = rec.get("type")
        rid = rec.get("id")
        if rtype not in ("call", "result"):
            add(ERROR, "bad-type", lineno,
                f"line {lineno}: type must be 'call' or 'result'")
            continue
        if not isinstance(rid, str) or not rid:
            add(ERROR, "missing-id", lineno,
                f"line {lineno}: missing non-empty string id")
            continue
        ts = rec.get("ts")
        if ts is not None:
            if not isinstance(ts, (int, float)) or isinstance(ts, bool) or not math.isfinite(ts):
                add(ERROR, "bad-ts", lineno,
                    f"line {lineno}: ts must be a finite number")
                ts = None
            else:
                if last_ts is not None and ts < last_ts:
                    monotonic = False
                last_ts = ts

        if rtype == "call":
            if rid in calls:
                add(ERROR, "duplicate-call", lineno,
                    f"line {lineno}: duplicate call id {rid!r} (first at line {calls[rid][0]})")
            else:
                calls[rid] = (lineno, ts)
        else:
            if rid not in calls:
                add(ERROR, "orphan-result", lineno,
                    f"line {lineno}: result {rid!r} has no call")
            elif rid in results:
                add(ERROR, "duplicate-result", lineno,
                    f"line {lineno}: second result for {rid!r} (first at line {results[rid][0]})")
            else:
                call_line, call_ts = calls[rid]
                if call_line > lineno:
                    # Unreachable with a single pass, kept for direct-API use
                    # with reordered record lists.
                    add(ERROR, "result-before-call", lineno,
                        f"line {lineno}: result {rid!r} precedes its call (line {call_line})")
                results[rid] = (lineno, ts)
                if call_ts is not None and ts is not None and ts < call_ts:
                    add(ERROR, "result-before-call", lineno,
                        f"line {lineno}: result {rid!r} ts {ts} is before its call ts {call_ts}")

    for rid, (lineno, _ts) in calls.items():
        if rid not in results:
            add(WARNING, "pending-call", lineno,
                f"line {lineno}: call {rid!r} has no result in this trace")

    if not monotonic:
        add(WARNING, "ts-regression", None,
            "timestamps are not monotonic across the trace")

    order = {ERROR: 0, WARNING: 1}
    findings.sort(key=lambda f: (order[f["severity"]], f["code"],
                                 f["line"] or 0, f["message"]))
    return findings


def has_errors(findings):
    return any(f["severity"] == ERROR for f in findings)


def main(argv):
    if len(argv) != 2:
        print("usage: trace_check.py TRACE.jsonl", file=sys.stderr)
        return 2
    try:
        with open(argv[1], "rb") as fh:
            raw = fh.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise InvalidInput(f"trace larger than {MAX_BYTES} bytes")
        records = load_trace(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, InvalidInput) as e:
        print(f"invalid input: {e}", file=sys.stderr)
        return 2
    findings = check_trace(records)
    for f in findings:
        where = f" [line {f['line']}]" if f["line"] else ""
        print(f"{f['severity']}: {f['code']}{where}: {f['message']}")
    if has_errors(findings):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
