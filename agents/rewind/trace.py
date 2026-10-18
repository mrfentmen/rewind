"""Rewind's lockwork: record, replay, and mutate agent runs.

A trace is a JSON list of steps (tool calls, LLM calls, decisions) with their inputs
and outputs. Rewind replays a trace deterministically (mocked, no real side effects)
and supports mutation: change one step's input/output and replay to see what would
have happened. The diff between the original and mutated run shows exactly where
behavior diverged.

Parsing is rules, not vibes: a trace is a JSON object with a "steps" list; each step
has a "type" (tool_call, llm_call, decision) and type-specific fields. A mutation
targets one step by index and changes one field. Nothing here reads a model's mind.
"""

from __future__ import annotations

import json
from pathlib import Path

VALID_TYPES = ("tool_call", "llm_call", "decision")


def load_trace(path: str) -> dict:
    """Load and validate a trace. ValueError names the problem exactly."""
    p = Path(path)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ValueError(f"no such trace file: {path}")
    except (ValueError, OSError) as exc:
        raise ValueError(f"trace file is not JSON: {exc}")
    if not isinstance(data, dict):
        raise ValueError("trace must be a JSON object")
    if "steps" not in data:
        raise ValueError("trace must have a 'steps' list")
    if not isinstance(data["steps"], list):
        raise ValueError("'steps' must be a list")
    for i, step in enumerate(data["steps"]):
        if not isinstance(step, dict):
            raise ValueError(f"step {i} must be an object")
        if "type" not in step:
            raise ValueError(f"step {i} missing 'type'")
        if step["type"] not in VALID_TYPES:
            raise ValueError(f"step {i} has unknown type '{step['type']}' "
                             f"(expected: {', '.join(VALID_TYPES)})")
    data.setdefault("name", p.stem)
    data.setdefault("metadata", {})
    return data


def save_trace(path: str, trace: dict) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(trace, indent=2) + "\n", encoding="utf-8")


def replay(trace: dict) -> list[dict]:
    """Replay a trace deterministically. Returns a list of replayed steps with
    their original inputs/outputs (mocked - no real side effects)."""
    out = []
    for i, step in enumerate(trace.get("steps", [])):
        entry = {"index": i, "type": step["type"]}
        if step["type"] == "tool_call":
            entry["tool"] = step.get("tool", "?")
            entry["args"] = step.get("args", {})
            entry["response"] = step.get("response", "")
            entry["status"] = "ok"
        elif step["type"] == "llm_call":
            entry["prompt"] = step.get("prompt", "")
            entry["output"] = step.get("output", "")
            entry["status"] = "ok"
        else:
            entry["decision"] = step.get("decision", "")
            entry["reason"] = step.get("reason", "")
            entry["status"] = "ok"
        out.append(entry)
    return out


def mutate(trace: dict, step_index: int, field: str, value) -> dict:
    """Return a copy of the trace with one field mutated at one step.
    ValueError names the problem exactly."""
    if not isinstance(trace, dict) or "steps" not in trace:
        raise ValueError("trace must be a valid trace object")
    steps = trace["steps"]
    if step_index < 0 or step_index >= len(steps):
        raise ValueError(f"step index {step_index} out of range (0..{len(steps) - 1})")
    step = steps[step_index]
    if field not in step:
        raise ValueError(f"step {step_index} has no field '{field}' "
                         f"(available: {', '.join(sorted(step.keys()))})")
    # deep copy
    import copy
    new_trace = copy.deepcopy(trace)
    new_trace["name"] = trace.get("name", "run") + f" (mutated step {step_index}.{field})"
    new_trace["steps"][step_index][field] = value
    new_trace.setdefault("metadata", {})
    new_trace["metadata"]["mutation"] = {
        "step": step_index, "field": field, "original": step[field],
        "new": value,
    }
    return new_trace


def diff_runs(original: list[dict], mutated: list[dict]) -> list[dict]:
    """Diff two replayed runs. Returns a list of deltas."""
    deltas = []
    n = max(len(original), len(mutated))
    for i in range(n):
        o = original[i] if i < len(original) else None
        m = mutated[i] if i < len(mutated) else None
        if o is None:
            deltas.append({"index": i, "change": "added", "mutated": m})
            continue
        if m is None:
            deltas.append({"index": i, "change": "removed", "original": o})
            continue
        # compare relevant fields
        changed_fields = []
        for key in ("response", "output", "decision", "reason", "args", "prompt"):
            if key in o or key in m:
                if o.get(key) != m.get(key):
                    changed_fields.append(key)
        if changed_fields:
            deltas.append({"index": i, "change": "modified", "fields": changed_fields,
                           "original": {k: o.get(k) for k in changed_fields},
                           "mutated": {k: m.get(k) for k in changed_fields}})
    return deltas


def render_replay(replayed: list[dict]) -> str:
    lines = []
    for step in replayed:
        i = step["index"]
        if step["type"] == "tool_call":
            lines.append(f"  [{i}] tool_call: {step['tool']}({step.get('args', {})}) "
                         f"-> {str(step.get('response', ''))[:80]}")
        elif step["type"] == "llm_call":
            lines.append(f"  [{i}] llm_call: {str(step.get('prompt', ''))[:60]}... "
                         f"-> {str(step.get('output', ''))[:80]}")
        else:
            lines.append(f"  [{i}] decision: {step.get('decision', '')} "
                         f"({step.get('reason', '')})")
    return "\n".join(lines) if lines else "  (empty trace)"


def render_diff(deltas: list[dict]) -> str:
    if not deltas:
        return "  no divergence - the mutation changed nothing observable"
    lines = []
    for d in deltas:
        i = d["index"]
        if d["change"] == "added":
            lines.append(f"  [{i}] ADDED in mutated run")
        elif d["change"] == "removed":
            lines.append(f"  [{i}] REMOVED in mutated run")
        else:
            lines.append(f"  [{i}] CHANGED: {', '.join(d['fields'])}")
            for k in d["fields"]:
                lines.append(f"       {k}: {str(d['original'].get(k, ''))[:60]} "
                             f"-> {str(d['mutated'].get(k, ''))[:60]}")
    return "\n".join(lines)
