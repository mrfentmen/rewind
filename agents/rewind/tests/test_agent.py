"""Tests for Rewind: trace loading, replay, mutation, diffing, routing, federation.

    python3 agents/rewind/tests/test_agent.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

DIR = Path(__file__).resolve().parent
AGENT_DIR = Path(__file__).resolve().parents[1]
REPO = Path(__file__).resolve().parents[3]
for path in (str(REPO / "kit"), str(AGENT_DIR), str(DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)

import agent  # noqa: E402
import trace  # noqa: E402
import federation as fed  # noqa: E402

SAMPLE_TRACE = {
    "name": "run-1",
    "steps": [
        {"type": "tool_call", "tool": "read_file", "args": {"path": "foo.txt"},
         "response": "hello world", "timestamp": "2026-01-01T00:00:00Z"},
        {"type": "llm_call", "prompt": "summarize: hello world",
         "output": "It says hello.", "timestamp": "2026-01-01T00:00:01Z"},
        {"type": "decision", "decision": "write summary", "reason": "user asked",
         "timestamp": "2026-01-01T00:00:02Z"},
    ],
    "metadata": {"agent": "polymath", "model": "gpt-4"},
}

FAKE_SIBLING = r"""
import sys, json
def send(obj):
    sys.stdout.write(json.dumps(obj) + "\n"); sys.stdout.flush()
def recv():
    return json.loads(sys.stdin.readline())
req = recv()
send({"jsonrpc":"2.0","id":req["id"],"result":{"protocolVersion":1,"agentCapabilities":{},
      "authMethods":[],"agentInfo":{"name":"fake-sibling","title":"Fake","version":"1.0.0"}}})
while True:
    try:
        req = recv()
    except Exception:
        break
    method = req.get("method", "")
    if method == "session/new":
        send({"jsonrpc":"2.0","id":req["id"],"result":{"sessionId":"fake-session","modes":[]}})
    elif method == "session/prompt":
        text = "".join(b.get("text","") for b in req.get("params",{}).get("prompt",[]))
        send({"jsonrpc":"2.0","method":"session/update","params":{"update":{
            "sessionUpdate":"agent_message_chunk","content":{"type":"text",
            "text":"FAKE ANSWER for %r\n" % text}}}})
        send({"jsonrpc":"2.0","id":req["id"],"result":{"stopReason":"end-turn"}})
    elif method in ("session/cancel", "session/load"):
        continue
    else:
        send({"jsonrpc":"2.0","id":req.get("id"),"error":{"code":-32601,"message":"unhandled"}})
"""


class TraceTests(unittest.TestCase):
    def test_load_valid(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "t.json"
            p.write_text(json.dumps(SAMPLE_TRACE))
            t = trace.load_trace(str(p))
            self.assertEqual(t["name"], "run-1")
            self.assertEqual(len(t["steps"]), 3)

    def test_load_missing_file(self):
        with self.assertRaises(ValueError) as c:
            trace.load_trace("/no/such/file.json")
        self.assertIn("no such trace file", str(c.exception))

    def test_load_invalid_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "t.json"
            p.write_text("{not json")
            with self.assertRaises(ValueError) as c:
                trace.load_trace(str(p))
            self.assertIn("not JSON", str(c.exception))

    def test_load_missing_steps(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "t.json"
            p.write_text(json.dumps({"name": "x"}))
            with self.assertRaises(ValueError) as c:
                trace.load_trace(str(p))
            self.assertIn("'steps' list", str(c.exception))

    def test_load_unknown_type(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "t.json"
            bad = {"steps": [{"type": "bogus"}]}
            p.write_text(json.dumps(bad))
            with self.assertRaises(ValueError) as c:
                trace.load_trace(str(p))
            self.assertIn("unknown type", str(c.exception))

    def test_save_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = str(Path(tmp) / "out.json")
            trace.save_trace(p, SAMPLE_TRACE)
            loaded = trace.load_trace(p)
            self.assertEqual(loaded["name"], SAMPLE_TRACE["name"])

    def test_replay(self):
        replayed = trace.replay(SAMPLE_TRACE)
        self.assertEqual(len(replayed), 3)
        self.assertEqual(replayed[0]["type"], "tool_call")
        self.assertEqual(replayed[0]["tool"], "read_file")
        self.assertEqual(replayed[0]["response"], "hello world")
        self.assertEqual(replayed[1]["type"], "llm_call")
        self.assertEqual(replayed[2]["type"], "decision")

    def test_render_replay(self):
        replayed = trace.replay(SAMPLE_TRACE)
        text = trace.render_replay(replayed)
        self.assertIn("read_file", text)
        self.assertIn("hello world", text)

    def test_mutate_valid(self):
        mutated = trace.mutate(SAMPLE_TRACE, 0, "response", "goodbye world")
        self.assertEqual(mutated["steps"][0]["response"], "goodbye world")
        self.assertEqual(SAMPLE_TRACE["steps"][0]["response"], "hello world")  # original unchanged
        self.assertIn("mutation", mutated["metadata"])
        self.assertEqual(mutated["metadata"]["mutation"]["original"], "hello world")
        self.assertEqual(mutated["metadata"]["mutation"]["new"], "goodbye world")

    def test_mutate_out_of_range(self):
        with self.assertRaises(ValueError) as c:
            trace.mutate(SAMPLE_TRACE, 99, "response", "x")
        self.assertIn("out of range", str(c.exception))

    def test_mutate_unknown_field(self):
        with self.assertRaises(ValueError) as c:
            trace.mutate(SAMPLE_TRACE, 0, "nonexistent", "x")
        self.assertIn("no field", str(c.exception))

    def test_diff_no_divergence(self):
        r1 = trace.replay(SAMPLE_TRACE)
        r2 = trace.replay(SAMPLE_TRACE)
        deltas = trace.diff_runs(r1, r2)
        self.assertEqual(deltas, [])
        self.assertIn("no divergence", trace.render_diff(deltas))

    def test_diff_modified(self):
        mutated = trace.mutate(SAMPLE_TRACE, 0, "response", "goodbye world")
        r1 = trace.replay(SAMPLE_TRACE)
        r2 = trace.replay(mutated)
        deltas = trace.diff_runs(r1, r2)
        self.assertEqual(len(deltas), 1)
        self.assertEqual(deltas[0]["change"], "modified")
        self.assertIn("response", deltas[0]["fields"])
        text = trace.render_diff(deltas)
        self.assertIn("CHANGED", text)
        self.assertIn("hello world", text)
        self.assertIn("goodbye world", text)


class RouteTests(unittest.TestCase):
    def test_help_and_status(self):
        self.assertEqual(agent.route("help")[0], "help")
        self.assertEqual(agent.route("")[0], "help")
        self.assertEqual(agent.route("status")[0], "status")

    def test_replay_mutate_diff(self):
        self.assertEqual(agent.route("replay run.json"), ("replay", {"path": "run.json"}))
        self.assertEqual(agent.route("mutate run.json step 0 response goodbye"),
                         ("mutate", {"path": "run.json", "step": 0, "field": "response", "value": "goodbye"}))
        self.assertEqual(agent.route("diff a.json b.json"), ("diff", {"a": "a.json", "b": "b.json"}))

    def test_ask_sibling(self):
        self.assertEqual(agent.route("ask tides: what are the tides?"),
                         ("ask", {"agent": "tides", "question": "what are the tides?"}))
        self.assertEqual(agent.route("tell me a joke")[0], "help")


class ClientChildTests(unittest.TestCase):
    def _client(self, tmp):
        from acp_kit import AcpClient
        return AcpClient(command=[sys.executable, str(AGENT_DIR / "agent.py")],
                         permission="allow-once", fs_root=tmp, stderr=subprocess.DEVNULL)

    def _write_trace(self, tmp, data=None):
        p = Path(tmp) / "run.json"
        p.write_text(json.dumps(data or SAMPLE_TRACE))
        return str(p)

    def test_help(self):
        with tempfile.TemporaryDirectory() as tmp:
            c = self._client(tmp)
            with c:
                c.initialize()
                sid = c.new_session(cwd=tmp)
                turn = c.prompt("help", session_id=sid)
                self.assertEqual(turn["stopReason"], "end_turn")
                self.assertIn("Rewind", turn["text"])
                self.assertIn("replay", turn["text"])

    def test_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            tp = self._write_trace(tmp)
            c = self._client(tmp)
            with c:
                c.initialize()
                sid = c.new_session(cwd=tmp)
                turn = c.prompt(f"replay {tp}", session_id=sid)
                self.assertEqual(turn["stopReason"], "end_turn")
                self.assertIn("replayed: run-1", turn["text"])
                self.assertIn("read_file", turn["text"])
                self.assertIn("hello world", turn["text"])

    def test_mutate(self):
        with tempfile.TemporaryDirectory() as tmp:
            tp = self._write_trace(tmp)
            c = self._client(tmp)
            with c:
                c.initialize()
                sid = c.new_session(cwd=tmp)
                turn = c.prompt(f"mutate {tp} step 0 response goodbye world", session_id=sid)
                self.assertEqual(turn["stopReason"], "end_turn")
                self.assertIn("divergence:", turn["text"])
                self.assertIn("CHANGED", turn["text"])
                self.assertIn("hello world", turn["text"])
                self.assertIn("goodbye world", turn["text"])

    def test_mutate_invalid_step(self):
        with tempfile.TemporaryDirectory() as tmp:
            tp = self._write_trace(tmp)
            c = self._client(tmp)
            with c:
                c.initialize()
                sid = c.new_session(cwd=tmp)
                turn = c.prompt(f"mutate {tp} step 99 response x", session_id=sid)
                self.assertEqual(turn["stopReason"], "end_turn")
                self.assertIn("out of range", turn["text"])

    def test_diff(self):
        with tempfile.TemporaryDirectory() as tmp:
            tp1 = self._write_trace(tmp, SAMPLE_TRACE)
            mutated = trace.mutate(SAMPLE_TRACE, 1, "output", "Changed output.")
            tp2 = str(Path(tmp) / "run2.json")
            Path(tp2).write_text(json.dumps(mutated))
            c = self._client(tmp)
            with c:
                c.initialize()
                sid = c.new_session(cwd=tmp)
                turn = c.prompt(f"diff {tp1} {tp2}", session_id=sid)
                self.assertEqual(turn["stopReason"], "end_turn")
                self.assertIn("CHANGED", turn["text"])

    def test_status_after_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            tp = self._write_trace(tmp)
            c = self._client(tmp)
            with c:
                c.initialize()
                sid = c.new_session(cwd=tmp)
                c.prompt(f"replay {tp}", session_id=sid)
                turn = c.prompt("status", session_id=sid)
                self.assertIn("run-1", turn["text"])
                self.assertIn("3 steps", turn["text"])

    def test_corrupt_trace_is_honest(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "bad.json"
            p.write_text("{not json")
            c = self._client(tmp)
            with c:
                c.initialize()
                sid = c.new_session(cwd=tmp)
                turn = c.prompt(f"replay {p}", session_id=sid)
                self.assertIn("not JSON", turn["text"])

    def test_ask_sibling(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = None
            fake_path = Path(tmp) / "fake_sibling.py"
            fake_path.write_text(FAKE_SIBLING)
            old = os.environ.get("POLYMATH_FAKE_SIBLING")
            os.environ["POLYMATH_FAKE_SIBLING"] = str(fake_path)
            try:
                c = self._client(tmp)
                with c:
                    c.initialize()
                    sid = c.new_session(cwd=tmp)
                    turn = c.prompt("ask air: how is the air?", session_id=sid)
            finally:
                if old is None:
                    os.environ.pop("POLYMATH_FAKE_SIBLING", None)
                else:
                    os.environ["POLYMATH_FAKE_SIBLING"] = old
            self.assertEqual(turn["stopReason"], "end_turn")
            self.assertIn("FAKE ANSWER", turn["text"])


class FederationChildTests(unittest.TestCase):
    def _with_fake(self, tmp):
        old = os.environ.get("POLYMATH_FAKE_SIBLING")
        os.environ["POLYMATH_FAKE_SIBLING"] = str(Path(tmp) / "fake_sibling.py")
        (Path(tmp) / "fake_sibling.py").write_text(FAKE_SIBLING)
        return old

    def _without_fake(self, old):
        if old is None:
            os.environ.pop("POLYMATH_FAKE_SIBLING", None)
        else:
            os.environ["POLYMATH_FAKE_SIBLING"] = old

    def test_real_child_spawn_and_answer(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = self._with_fake(tmp)
            try:
                turn = fed.run_sibling("air", "Denver")
            finally:
                self._without_fake(old)
            self.assertTrue(turn.ok, turn.error)
            self.assertIn("FAKE ANSWER", turn.text)

    def test_unconfigured_home_is_honest(self):
        old = os.environ.pop("POLYMATH_FAKE_SIBLING", None)
        home = os.environ.pop("AWESOME_SERVERS_HOME", None)
        try:
            turn = fed.run_sibling("air", "Denver")
        finally:
            if old is not None:
                os.environ["POLYMATH_FAKE_SIBLING"] = old
            if home is not None:
                os.environ["AWESOME_SERVERS_HOME"] = home
        self.assertFalse(turn.ok)
        self.assertIn("AWESOME_SERVERS_HOME", turn.error)


if __name__ == "__main__":
    unittest.main()
