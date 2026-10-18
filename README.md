# Rewind — the replay agent

> **Rewind** replays agent runs and mutates one variable to see what would have happened.
> Given a recorded agent run (a trace of tool calls, LLM calls, and decisions), it replays
> it deterministically (mocked, no real side effects), then mutates one step's input or
> output and replays again. The diff shows exactly where behavior diverged.

The word is coined: *rewind* — the rung after replay, where you don't just watch the
tape, you change one frame and see what breaks.

```text
        .----.
       /  _   \
       |=[_]==|
  (`.--.____.---.'\
   `-./'\\_.'
      |  /.  .||
       \:-: .'
  LGB   `-'                     (archive piece, credited, until the portrait lands)
```

## What it does

| You say | Rewind does |
|---|---|
| `replay run.json` | replays the run, reports every step (tool, args, response, output, decision) |
| `mutate run.json step 2 response "goodbye"` | replays with step 2's response changed; shows the divergence |
| `diff a.json b.json` | diffs two runs, shows which steps changed and how |
| `status` | shows the loaded trace (name, step count, metadata) |

The trace file is the whole contract: plain JSON, one list of steps, any tool can read or
write it. A mutation that cannot be applied honestly is refused, not faked.

## Run it

```bash
npm i -g rewind-agent     # or: npx rewind-agent  (packaged agent; needs python3)
rewind-agent              # launches agents/rewind/agent.py
```

Or from source:

```bash
git clone https://github.com/mrfentmen/rewind && cd rewind
python3 agents/rewind/agent.py
```

For forwarded questions, point it at a checkout of the answerer repo:

```bash
AWESOME_SERVERS_HOME=/path/to/awesome-servers rewind
```

Run the tests (26 of them, offline, including real child-process federation):

```bash
python3 agents/rewind/tests/test_agent.py
```

## The honest parts

- Replay is deterministic and mocked — no real side effects, no LLM calls.
- A mutation changes exactly one field at one step. Nothing is invented.
- Vendored lockwork is credited line by line: `kit/` from the ACP kit; `federation.py`
  from the polymath — vendored on 2026-10-18, kept standalone on purpose.

Built on top of: [mrfentmen/awesome-servers](https://github.com/mrfentmen/awesome-servers)
(the watcher runtime, the feeds) and
[mrfentmen/superagents](https://github.com/mrfentmen/superagents) (the polymath and the
sentry, the first two agents this one borrows transport and lockwork from) and
[mrfentmen/fedora-hat](https://github.com/mrfentmen/fedora-hat) and
[mrfentmen/instink](https://github.com/mrfentmen/instink) and
[mrfentmen/contextink](https://github.com/mrfentmen/contextink).