import itertools
import json
import subprocess
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import trace_check


def parse(lines):
    return trace_check.load_trace("\n".join(json.dumps(l) for l in lines))


def codes(findings):
    return [f["code"] for f in findings]


class StructuralTests(unittest.TestCase):
    def test_clean_trace_no_findings(self):
        recs = parse([
            {"type": "call", "id": "a", "tool": "t", "ts": 1},
            {"type": "result", "id": "a", "ts": 2},
        ])
        self.assertEqual(trace_check.check_trace(recs), [])

    def test_orphan_result(self):
        recs = parse([{"type": "result", "id": "a"}])
        self.assertIn("orphan-result", codes(trace_check.check_trace(recs)))

    def test_pending_call_is_warning(self):
        recs = parse([{"type": "call", "id": "a", "tool": "t"}])
        f = trace_check.check_trace(recs)
        self.assertEqual(codes(f), ["pending-call"])
        self.assertEqual(f[0]["severity"], trace_check.WARNING)
        self.assertFalse(trace_check.has_errors(f))

    def test_duplicate_call_and_result(self):
        recs = parse([
            {"type": "call", "id": "a", "tool": "t"},
            {"type": "call", "id": "a", "tool": "t"},
            {"type": "result", "id": "a"},
            {"type": "result", "id": "a"},
        ])
        c = codes(trace_check.check_trace(recs))
        self.assertIn("duplicate-call", c)
        self.assertIn("duplicate-result", c)

    def test_result_ts_before_call_ts(self):
        recs = parse([
            {"type": "call", "id": "a", "tool": "t", "ts": 5},
            {"type": "result", "id": "a", "ts": 4},
        ])
        self.assertIn("result-before-call", codes(trace_check.check_trace(recs)))

    def test_bad_type_and_missing_id(self):
        recs = parse([{"type": "note", "id": "a"}, {"type": "call"}])
        c = codes(trace_check.check_trace(recs))
        self.assertIn("bad-type", c)
        self.assertIn("missing-id", c)

    def test_nonfinite_ts(self):
        recs = parse([{"type": "call", "id": "a", "tool": "t", "ts": float("nan")}])
        self.assertIn("bad-ts", codes(trace_check.check_trace(recs)))

    def test_ts_regression_warning(self):
        recs = parse([
            {"type": "call", "id": "a", "tool": "t", "ts": 10},
            {"type": "call", "id": "b", "tool": "t", "ts": 9},
            {"type": "result", "id": "a", "ts": 11},
            {"type": "result", "id": "b", "ts": 12},
        ])
        f = trace_check.check_trace(recs)
        self.assertIn("ts-regression", codes(f))
        self.assertFalse(trace_check.has_errors(f))

    def test_findings_deterministic(self):
        lines = [
            {"type": "result", "id": "z"},
            {"type": "call", "id": "a", "tool": "t"},
            {"type": "call", "id": "a", "tool": "t"},
            {"type": "result", "id": "y"},
        ]
        f1 = codes(trace_check.check_trace(parse(lines)))
        f2 = codes(trace_check.check_trace(parse(lines)))
        self.assertEqual(f1, f2)


class InputCapTests(unittest.TestCase):
    def test_oversize_rejected(self):
        big = "\n".join(json.dumps({"type": "call", "id": str(i), "tool": "t"}) for i in range(200_000))
        with self.assertRaises(trace_check.InvalidInput):
            trace_check.load_trace(big)

    def test_too_many_records_rejected(self):
        text = "\n".join(json.dumps({"type": "call", "id": str(i), "tool": "t"})
                         for i in range(trace_check.MAX_RECORDS + 1))
        with self.assertRaises(trace_check.InvalidInput):
            trace_check.load_trace(text)

    def test_bad_json_rejected(self):
        with self.assertRaises(trace_check.InvalidInput):
            trace_check.load_trace("{not json")

    def test_non_object_record_rejected(self):
        with self.assertRaises(trace_check.InvalidInput):
            trace_check.load_trace("[1,2,3]")


class OracleTests(unittest.TestCase):
    """Exhaustive oracle: every trace over {call A, result A, call B, result B}
    of length 0..6 (5,461 traces) checked against an independent counter-based
    oracle."""

    EVENTS = (("call", "a"), ("result", "a"), ("call", "b"), ("result", "b"))

    @staticmethod
    def oracle_findings(seq):
        # Independent recount: for each event, derive its expected code purely
        # from the counts of earlier events with the same id.
        expected = []
        call_count = Counter()
        result_count = Counter()
        paired = Counter()
        for pos, (typ, rid) in enumerate(seq, 1):
            if typ == "call":
                if call_count[rid] > 0:
                    expected.append("duplicate-call")
                call_count[rid] += 1
            else:
                if call_count[rid] == 0:
                    expected.append("orphan-result")
                elif paired[rid] > 0 or result_count[rid] > 0:
                    expected.append("duplicate-result")
                else:
                    result_count[rid] += 1
                    paired[rid] += 1
        for rid in ("a", "b"):
            if call_count[rid] >= 1 and paired[rid] == 0:
                expected.append("pending-call")  # one finding per unmatched id
        return expected

    def test_all_short_traces(self):
        checked = 0
        for length in range(7):
            for seq in itertools.product(self.EVENTS, repeat=length):
                recs = parse([{"type": t, "id": i, "tool": "x"} for t, i in seq])
                got = codes(trace_check.check_trace(recs))
                want = self.oracle_findings(seq)
                self.assertEqual(Counter(got), Counter(want),
                                 f"mismatch on {seq}: got {got}, want {want}")
                checked += 1
        self.assertEqual(checked, sum(4 ** k for k in range(7)))


class CliTests(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, str(Path(__file__).parent / "trace_check.py"), *args],
            capture_output=True, text=True)

    def test_cli_clean_exit_0(self):
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as fh:
            fh.write(json.dumps({"type": "call", "id": "a", "tool": "t"}) + "\n")
            fh.write(json.dumps({"type": "result", "id": "a"}) + "\n")
            path = fh.name
        r = self.run_cli(path)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_cli_errors_exit_1(self):
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as fh:
            fh.write(json.dumps({"type": "result", "id": "a"}) + "\n")
            path = fh.name
        r = self.run_cli(path)
        self.assertEqual(r.returncode, 1)
        self.assertIn("orphan-result", r.stdout)

    def test_cli_invalid_exit_2(self):
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as fh:
            fh.write("{not json")
            path = fh.name
        r = self.run_cli(path)
        self.assertEqual(r.returncode, 2)
        self.assertIn("invalid input", r.stderr)

    def test_cli_exact_1mib_boundary(self):
        rec = {"type": "call", "id": "a", "tool": "t", "pad": ""}
        line = json.dumps(rec)
        pad = trace_check.MAX_BYTES - len(line) - 1  # one trailing newline
        rec["pad"] = "x" * pad
        text = json.dumps(rec) + "\n"
        self.assertEqual(len(text.encode()), trace_check.MAX_BYTES)
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as fh:
            fh.write(text)
            path = fh.name
        r = self.run_cli(path)
        self.assertEqual(r.returncode, 0, r.stderr)  # pending-call is a warning
        rec["pad"] += "x"
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as fh:
            fh.write(json.dumps(rec) + "\n")
            path = fh.name
        r = self.run_cli(path)
        self.assertEqual(r.returncode, 2)


if __name__ == "__main__":
    unittest.main()
