# agent-trace-check

Offline validator for agent tool-call traces.

When an agent run goes wrong, the first question is whether the trace itself is intact: did every tool call get exactly one result, in the right order, with sane timestamps? This tool answers that and nothing else. It reads a JSONL trace, checks call/result pairing and timing, and reports findings. It never executes anything, never calls a model, and never touches the network.

## Trace format

One JSON object per line:

```jsonl
{"type": "call",   "id": "s1", "tool": "web.search", "ts": 1.0}
{"type": "result", "id": "s1", "ok": true,           "ts": 1.4}
```

- `type` - required, `"call"` or `"result"`.
- `id` - required, non-empty string pairing a result to its call.
- `ts` - optional, must be a finite number when present.
- Any other fields (`tool`, `ok`, `args`, ...) are carried by your format and ignored here.

## What it checks

| Code | Severity | Meaning |
| --- | --- | --- |
| `bad-type` | error | record type is not `call` or `result` |
| `missing-id` | error | record has no non-empty string id |
| `bad-ts` | error | ts is not a finite number |
| `duplicate-call` | error | two calls share an id |
| `orphan-result` | error | result with no preceding call of that id |
| `duplicate-result` | error | second result for an already-paired call |
| `result-before-call` | error | result's ts precedes its call's ts |
| `pending-call` | warning | call with no result in this trace (normal for in-flight traces) |
| `ts-regression` | warning | timestamps not monotonic across the trace |

Findings are sorted deterministically (severity, code, line, message), so the same trace always produces the same report.

## Use

```sh
python3 trace_check.py trace.jsonl      # validate
python3 demo.py                         # synthetic demo
python3 -m unittest test_trace_check    # 18 tests
```

Exit codes: `0` clean (warnings may still print), `1` errors found, `2` invalid input.

## Safety caps

The CLI refuses traces over 1 MiB, records nested deeper than 64 levels, and traces with more than 10,000 records - all as exit 2, never a crash.

## Tests

18 tests, including an exhaustive independent oracle: every trace over a two-call alphabet up to length 6 (5,461 traces) is checked against a counter-based oracle written separately from the validator's single-pass implementation, plus the exact 1 MiB accept/reject boundary.

## Limits

- **Pairing only.** A perfectly paired trace can still record wrong tool calls, wrong arguments, or wrong results. This tool says nothing about correctness of what the agent did.
- **One result per call.** Streaming or multi-part results are flagged as duplicates; if your framework emits those, this tool does not fit that shape.
- **Pending calls are ambiguous by nature.** A call without a result at trace end may be in flight, dropped, or crashed - the warning cannot tell you which.
- **Timestamps are trusted.** The validator cannot detect a wrong clock, only inconsistent ones.
- **Trusted-input API.** The size/depth/count caps are enforced by the CLI and by `load_trace`; calling `check_trace` directly with hand-built records bypasses the input caps.

## License

MIT - see [LICENSE](LICENSE).
