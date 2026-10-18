"""Rewind: replay agent runs, mutate one variable, see what would have happened.

The problem: your agent failed and you don't know why. Rewind takes a recorded agent
run (a trace of tool calls, LLM calls, and decisions) and replays it deterministically
(mocked, no real side effects). Then it mutates one variable - a tool response, a
prompt, a decision - and replays again. The diff shows exactly where behavior diverged.

    replay <trace.json>                      replay the run, report every step
    mutate <trace.json> step N field value    replay with one field changed at step N
    diff <a.json> <b.json>                   diff two runs
    status                                   show the loaded trace

The trace file is the whole contract: plain JSON, one list of steps, any tool can read
or write it. Nothing here reads a model's mind; a mutation that cannot be applied
honestly is refused.

The vendored lockwork (kit, federation) carries its credit lines.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for path in (str(REPO / "kit"), str(Path(__file__).resolve().parent)):
    if path not in sys.path:
        sys.path.insert(0, path)

from acp_kit import STOP_END_TURN, STOP_REFUSAL, AcpAgent, SessionContext, prompt_text  # noqa: E402

import trace  # noqa: E402
import federation as fed  # noqa: E402

WELCOME = (
    "I am Rewind. I replay agent runs and mutate one variable to see what would have "
    "happened:\n"
    "  - replay <trace.json>                replay the run, report every step\n"
    "  - mutate <trace.json> step N <field> <value>\n"
    "                                       replay with one field changed at step N\n"
    "  - diff <a.json> <b.json>             diff two runs\n"
    "  - status                             show the loaded trace\n"
    "A mutation that cannot be applied honestly is refused, not faked."
)

_REPLAY = re.compile(r"^\s*replay\s+(.+?)\s*$", re.I)
_MUTATE = re.compile(r"^\s*mutate\s+(\S+)\s+step\s+(\d+)\s+(\S+)\s+(.+?)\s*$", re.I | re.S)
_DIFF = re.compile(r"^\s*diff\s+(\S+)\s+(\S+)\s*$", re.I)
_STATUS = re.compile(r"^\s*(status|show|what)\b", re.I)
_HELP = re.compile(r"^\s*(help|what can you do)\b", re.I)
_ASK = re.compile(r"^\s*ask\s+(.+)$", re.I | re.S)


def route(text: str) -> tuple[str, dict]:
    stripped = str(text or "").strip()
    if not stripped or _HELP.search(stripped):
        return "help", {}
    m = _REPLAY.search(stripped)
    if m:
        return "replay", {"path": m.group(1).strip()}
    m = _MUTATE.search(stripped)
    if m:
        return "mutate", {"path": m.group(1).strip(), "step": int(m.group(2)),
                          "field": m.group(3).strip(), "value": m.group(4).strip()}
    m = _DIFF.search(stripped)
    if m:
        return "diff", {"a": m.group(1).strip(), "b": m.group(2).strip()}
    if _STATUS.search(stripped):
        return "status", {}
    m = _ASK.search(stripped)
    if m:
        pair = fed.ask_pair(stripped)
        if pair:
            return "ask", {"agent": pair[0], "question": pair[1]}
        return "ask", {"question": m.group(1).strip()}
    pair = fed.ask_pair(stripped)
    if pair:
        return "ask", {"agent": pair[0], "question": pair[1]}
    return "help", {}


class RewindAgent(AcpAgent):
    name = "rewind"
    title = "Rewind - replay agent runs, mutate one variable, see what would have happened"
    version = "1.0.0"

    def new_session(self, session) -> dict:
        session.remember("agent", "Rewind session opened.")
        self._trace = None
        self._replayed = None
        return {}

    def prompt(self, ctx: SessionContext, prompt: list[dict]) -> str:
        text = prompt_text(prompt)
        skill, params = route(text)
        cwd = Path(ctx.session.cwd or ".")
        ctx.plan([
            (f"Take the prompt as Rewind ({skill})", "high"),
            ("Read or replay the trace - no inventing", "high"),
            ("Report the diff honestly", "medium"),
        ])

        if skill == "help":
            ctx.stream_text(WELCOME + "\n")
            return STOP_END_TURN

        if skill == "status":
            if not getattr(self, "_trace", None):
                ctx.stream_text("no trace loaded. try: replay <trace.json>\n")
                return STOP_END_TURN
            t = self._trace
            ctx.stream_text(f"loaded trace: {t.get('name')} ({len(t.get('steps', []))} steps)\n"
                            f"metadata: {json.dumps(t.get('metadata', {}))}\n")
            return STOP_END_TURN

        if skill == "replay":
            path = params["path"]
            if not Path(path).is_absolute():
                path = str(cwd / path)
            try:
                t = trace.load_trace(path)
            except ValueError as exc:
                ctx.message(f"could not load the trace: {exc}")
                return STOP_END_TURN
            self._trace = t
            self._replayed = trace.replay(t)
            ctx.stream_text(f"replayed: {t.get('name')} ({len(t.get('steps', []))} steps)\n"
                            + trace.render_replay(self._replayed) + "\n")
            return STOP_END_TURN

        if skill == "mutate":
            path = params["path"]
            if not Path(path).is_absolute():
                path = str(cwd / path)
            try:
                t = trace.load_trace(path)
            except ValueError as exc:
                ctx.message(f"could not load the trace: {exc}")
                return STOP_END_TURN
            try:
                mutated = trace.mutate(t, params["step"], params["field"], params["value"])
            except ValueError as exc:
                ctx.message(f"could not apply the mutation: {exc}")
                return STOP_END_TURN
            original_replayed = trace.replay(t)
            mutated_replayed = trace.replay(mutated)
            deltas = trace.diff_runs(original_replayed, mutated_replayed)
            ctx.stream_text(f"original: {t.get('name')}\n" + trace.render_replay(original_replayed) + "\n\n"
                            f"mutated (step {params['step']}.{params['field']} = {params['value']}):\n"
                            + trace.render_replay(mutated_replayed) + "\n\n"
                            f"divergence:\n" + trace.render_diff(deltas) + "\n")
            return STOP_END_TURN

        if skill == "diff":
            a_path = params["a"]
            b_path = params["b"]
            if not Path(a_path).is_absolute():
                a_path = str(cwd / a_path)
            if not Path(b_path).is_absolute():
                b_path = str(cwd / b_path)
            try:
                ta = trace.load_trace(a_path)
                tb = trace.load_trace(b_path)
            except ValueError as exc:
                ctx.message(f"could not load a trace: {exc}")
                return STOP_END_TURN
            ra = trace.replay(ta)
            rb = trace.replay(tb)
            deltas = trace.diff_runs(ra, rb)
            ctx.stream_text(f"diff: {ta.get('name')} vs {tb.get('name')}\n"
                            + trace.render_diff(deltas) + "\n")
            return STOP_END_TURN

        if skill == "ask":
            if "agent" in params:
                call = "call_sibling"
                ctx.tool_call(call, f"Ask the {params['agent']} sibling", kind="run",
                              name="rewind-ask", raw_input=dict(params))
                ctx.tool_call_update(call, status="in_progress")
                try:
                    row = fed.run_sibling(params["agent"], params["question"])
                except fed.SiblingError as exc:
                    ctx.tool_call_update(call, status="failed",
                                         content=ctx.text_content(str(exc)[:200]))
                    ctx.message(f"the sibling missed: {exc}")
                    return STOP_END_TURN
                if not row.ok or not (row.text or "").strip():
                    ctx.tool_call_update(call, status="failed",
                                         content=ctx.text_content(row.error or "refused"))
                    ctx.message(f"the sibling missed: {row.error or 'refused'}")
                    return STOP_END_TURN
                ctx.tool_call_update(call, status="completed",
                                     content=ctx.text_content(
                                         f"{row.agent}: stopReason={row.stop_reason or 'end-turn'}"))
                ctx.stream_text((row.text or "").rstrip() + "\n\n"
                                f"Source: the {row.agent} sibling over ACP.\n")
                return STOP_END_TURN
            ctx.stream_text("no sibling named. try: ask <name>: <question>\n")
            return STOP_END_TURN

        ctx.stream_text(WELCOME + "\n")
        return STOP_END_TURN

    def on_cancel(self, session) -> None:
        return None


def main(argv=None) -> int:
    import logging
    logging.basicConfig(level="INFO", stream=sys.stderr,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    try:
        RewindAgent().run()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
