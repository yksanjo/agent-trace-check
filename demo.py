"""Synthetic demo: validate one clean trace and one broken trace.

All traces here are made up. No model calls, no network, nothing executes.
"""
import trace_check

clean = [
    {"type": "call", "id": "s1", "tool": "web.search", "ts": 1.0},
    {"type": "result", "id": "s1", "ok": True, "ts": 1.4},
    {"type": "call", "id": "f1", "tool": "web.fetch", "ts": 1.5},
    {"type": "result", "id": "f1", "ok": True, "ts": 2.1},
]

broken = [
    {"type": "call", "id": "s1", "tool": "web.search", "ts": 1.0},
    {"type": "result", "id": "ghost"},                      # orphan
    {"type": "call", "id": "s1", "tool": "web.search", "ts": 1.1},  # duplicate
    {"type": "result", "id": "s1", "ts": 0.9},              # ts before call
    {"type": "call", "id": "f1", "tool": "web.fetch", "ts": 1.2},   # pending
    {"type": "call", "id": "n1", "tool": "note"},           # pending too
]

for label, lines in [("clean", clean), ("broken", broken)]:
    recs = trace_check.load_trace("\n".join(__import__("json").dumps(l) for l in lines))
    findings = trace_check.check_trace(recs)
    print(f"--- {label} trace ({len(lines)} records) ---")
    if not findings:
        print("no findings")
    for f in findings:
        where = f" [line {f['line']}]" if f["line"] else ""
        print(f"{f['severity']}: {f['code']}{where}: {f['message']}")
    print()
