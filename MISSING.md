# MISSING.md — the claim-check for the rewind repo

## The name, checked (2026-10-18)

Searched on GitHub (API search, repos):

- `rewind` (repos): many hits — generic word, audio/video tools, git tools.
- `agent replay mutation` (repos): 12 hits — all noise except `codontrace` (1★, a small
  white-box Python toolkit for deterministic agent traces, causal replay, and mutation
  experiments).
- `agent replay` (repos): 3906 hits — mostly incidental (RL record-and-replay, crypto
  order book replay, macOS workflow recorders).

The slot this repo takes — an agent that replays agent runs and mutates one variable to
see what would have happened, with a clean diff — did not appear in those hits. The only
adjacent tool is `codontrace` (1★, a small toolkit, not an agent). The claim stays only
that the specific combination (agent-native replay + mutation + diff as a single tool)
is not there.

## What this repo does NOT claim

- It does not reproduce LLM non-determinism. Replay is deterministic by construction:
  tool responses and LLM outputs are mocked from the trace.
- It does not "time travel" in the OrcaReplay sense (record, replay, fork, debug any
  agent run with any model). OrcaReplay (285★) owns that word. Rewind is narrower:
  replay + mutate one variable + diff.
- It does not invent outputs. A mutation changes exactly one field at one step; everything
  else is replayed verbatim from the trace.

## What is genuinely absent (to build or not to build)

- The portrait: Yogi has the png; the README slot waits.
- Automatic trace recording (capturing a live agent run into a trace file) — not built;
  traces must be written by hand or by an external recorder.
- Multi-variable mutation (changing two things at once) — not built; single mutation
  keeps the diff readable.