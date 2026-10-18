"""The polymath's federation layer: sibling agents, file-declared, spawn-and-merge.

(Vendored from mrfentmen/superagents agents/polymath/federation.py on 2026-10-18; kept standalone.)

Difference from the first repo's orchestrator: the registry is data (registry.json), not
code; each sibling carries topic words so an unformed question can be routed to the most
likely domain agent; and a sibling that cannot be reached is a named row in the answer,
never silence.

Sibling processes live in the other repo (awesome-servers): the checkout is located through
the AWESOME_SERVERS_HOME environment variable, which must contain acp/agents/<dir>/agent.py
of a local clone. The in-repo watch creature (sentry) is spawned from this repo directly.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
KIT = REPO / "kit"
MANIFEST = Path(__file__).resolve().parent / "registry.json"


def load_manifest() -> dict:
    try:
        return json.loads(MANIFEST.read_text())
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"the polymath registry is unreadable: {exc}") from exc


class SiblingError(RuntimeError):
    """A sibling could not start, answer in time, or finish its turn."""


def siblings() -> dict[str, dict]:
    man = load_manifest()
    return {row["name"]: row for row in man.get("siblings", []) if not row.get("skip")}


def sibling_names() -> list[str]:
    return sorted(siblings())


def _topic_clause(text: str) -> str:
    """The registry-driven strip: remove `for air and moon` mentions so place extraction
    cannot mistake a topic list for a place. Names come from the manifest, not hardcode."""
    names = "|".join(re.escape(n) for n in sibling_names())
    pattern = rf"\b(?:for|and|with)?\s*(?:(?:{names})\b\s*(?:,\s*|and|with)?\s*)+"
    return re.sub(pattern, " ", str(text or ""), flags=re.IGNORECASE)


def watch_handler() -> str:
    return str(load_manifest().get("watch_handler") or "sentry")


def watch_words() -> list[str]:
    return [w.lower() for w in load_manifest().get("watch_words", [])]


def catalog_rows() -> list[dict]:
    return [{"agent": name,
             "ask_example": row.get("ask", "").replace("{place}", "Denver"),
             "topics": row.get("topics", [])[:5]}
            for name, row in sorted(siblings().items())]


def ask_pair(text: str) -> tuple[str, str] | None:
    match = re.match(r"\s*ask\s+([A-Za-z][A-Za-z0-9-]*)\s*[:]\s*(.+?)\s*$", str(text or ""), re.I)
    if not match:
        return None
    name, question = match.group(1).lower(), match.group(2).strip()
    return (name, question) if name in siblings() and question else None


def topics_on(text: str) -> list[str]:
    words = str(text or "").lower()
    return [name for name, row in siblings().items()
            if any(re.search(rf"\b{re.escape(t)}", words) for t in row.get("topics", []))]


def find_ranking(text: str) -> list[dict]:
    """Rank sibling candidates for an unformed question; pure, offline, testable."""
    words = str(text or "").lower()
    scored = []
    for name, row in siblings().items():
        hits = [t for t in row.get("topics", []) if re.search(rf"\b{re.escape(t)}", words)]
        if hits:
            scored.append({"agent": name, "hits": hits, "score": len(hits)})
    return sorted(scored, key=lambda s: (-s["score"], s["agent"]))


def place_of(text: str) -> str | None:
    """The place in a request, with polite tails and any sibling-topic clause removed first:
    `brief me for air and moon in Denver` yields Denver, and `Buenos Aires please` yields
    Buenos Aires."""
    raw = str(text or "")
    cleaned = re.sub(r"\b(?:please|right now|today|tonight)\b.*$", "", raw, flags=re.I)
    cleaned = re.sub(r"[\s]+(?:,\s*$|\?$|!$|\.$)", "", cleaned.strip())
    clause = _topic_clause(cleaned)
    match = re.search(r"\b(?:in|on|at|for)\s+([A-Za-z][A-Za-z .'\-]{1,40}?)(?:[?.!,]|$)", clause)
    if not match:
        return None
    words = match.group(1).split()
    if not words or words[0].lower() in {"and", "the", "a", "me", "it", "this", "that", "us", "here"}:
        return None  # determiner-speak, not a place
    parts = [w for w in words if w.lower() not in {"and", "the", "a", "for", "in", "on", "at", "is"}]
    return " ".join(parts).strip(" .,?!")[:50] or None


class SiblingTurn:
    def __init__(self, agent: str) -> None:
        self.agent = agent
        self.ok = False
        self.text = ""
        self.stop_reason = ""
        self.error = ""

    def as_row(self) -> dict:
        return {"agent": self.agent, "ok": self.ok, "text": self.text,
                "stop_reason": self.stop_reason, "error": self.error}


def _child_command(row: dict) -> list[str]:
    """External sibling: the answerer repo's checkout; internal sibling: here."""
    fake = os.environ.get("POLYMATH_FAKE_SIBLING")
    if fake:
        return [sys.executable, fake]
    if row.get("internal"):
        return [sys.executable, str(REPO / "agents" / row["dir"] / "agent.py")]
    home = os.environ.get("AWESOME_SERVERS_HOME")
    if not home:
        raise SiblingError("the answerer repo checkout is not configured: set AWESOME_SERVERS_HOME "
                           "to a checkout of mrfentmen/awesome-servers")
    return [sys.executable, str(Path(home) / "acp" / "agents" / row["dir"] / "agent.py")]


def sibling_question(row: dict, question: str) -> str:
    if not question:
        raise SiblingError("no question to forward")
    template = row.get("ask") or ""
    if "{place}" in template and re.fullmatch(r"[A-Za-z][A-Za-z .'\-]{1,50}", question.strip()):
        return template.replace("{place}", question.strip())
    return question


def run_sibling(name: str, question: str, cwd: str | None = None,
                timeout: float = 90.0) -> SiblingTurn:
    turn = SiblingTurn(name)
    if KIT.as_posix() not in sys.path:
        sys.path.insert(0, KIT.as_posix())
    from acp_kit import AcpClient
    # in-repo creatures (the watch handler) are siblings too, just committed here
    row = siblings().get(name) or ({"name": name, "dir": name, "internal": True}
                                   if name == watch_handler() else None)
    if row is None:
        turn.error = f"unknown sibling: {name!r}"
        return turn
    try:
        command = _child_command(row)  # may raise the honest SiblingError for unconfigured homes
    except SiblingError as exc:
        turn.error = str(exc)
        return turn
    try:
        spawn_cwd = cwd or os.getcwd()
        with AcpClient(command=command, timeout=timeout) as client:
            client.initialize(name="polymath-bridge", version="1.0.0")
            session_id = client.new_session(cwd=spawn_cwd)
            result = client.prompt(sibling_question(row, question), session_id=session_id)
            turn.ok = result.get("stopReason") != "refusal"
            turn.text = (result.get("text") or "").strip()
            turn.stop_reason = result.get("stopReason") or ""
    except FileNotFoundError:
        turn.error = f"the {name} agent is not installed on this machine"
    except subprocess.TimeoutExpired:
        turn.error = f"the {name} agent did not answer within {timeout:g}s"
    except Exception as exc:  # a sibling failing is a row, not a crash
        turn.error = str(exc)[:200]
    return turn


def fan_out(questions: dict[str, str], cwd: str | None = None, timeout: float = 90.0,
            workers: int = 4) -> list[SiblingTurn]:
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {name: pool.submit(run_sibling, name, question, cwd, timeout)
                   for name, question in questions.items()}
        rows = [future.result() for name, future in sorted(futures.items())]
    return rows
