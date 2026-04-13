# Dungeon Agents

A multi-agent dungeon simulation with structured observability traces and a legibility viewer. Two LLM-powered explorer agents navigate an 8×8 grid dungeon, attempting to find a key, unlock a door, and both reach the exit. A third Dungeon Master agent (with stale full-board visibility) can answer questions from explorers.

**Model used:** `claude-haiku-4-5-20251001` — fast and cheap, produces dumb-but-interesting traces.

---

## Setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Fill in ANTHROPIC_API_KEY, LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, LANGFUSE_HOST
```

## Run a simulation

```bash
source venv/bin/activate
python run.py              # random seed
python run.py --seed 42    # reproducible run
python run.py --seed 42 --turn-limit 30 --size 10
```

Output is written to `runs/<run_id>/`:
- `events.jsonl` — one structured JSON event per agent turn
- `summary.json` — outcome, reason, seed, Langfuse URL
- `langfuse_export.json` — trace URLs for the run

## View a run

Open `viewer/index.html` directly in a browser (no server needed).  
Click the file input and load any `runs/<run_id>/events.jsonl`.

The viewer shows:
- **Incident summary** — outcome, anomaly count, belief divergence count, issue list
- **Timeline** — every turn with expandable belief vs. ground truth diff
- **Message trace** — messages between agents with delay visualisation

## Committed runs

Ten runs are committed under `runs/`, selected from 68 total simulations by distinct event type coverage. Full structured records are in [`submission/best_runs.json`](submission/best_runs.json).

| Run ID | Seed | Outcome | Anomalies | Distinct event types |
|---|---|---|---|---|
| `20260412_215157_eb6647` | 3 | turn_limit | — | dm_response, message_delayed, message_sent, spurious_block |
| `20260412_215704_ee36c4` | 5 | turn_limit | — | dm_response, key_pickup, message_sent, spurious_block |
| `20260412_221157_f66a89` | 11 | turn_limit | — | dm_response, key_pickup, message_delayed, message_sent, spurious_block |
| `20260412_222029_37217a` | 15 | **success** | — | dm_response, key_pickup, message_delayed, message_sent, spurious_block |
| `20260412_223700_8bee4a` | 22 | stuck | — | door_encounter, key_pickup, message_delayed, message_sent, spurious_block |
| `20260412_224513_0dbb92` | 26 | turn_limit | — | dm_response, door_encounter, message_delayed, message_sent, spurious_block |
| `20260412_230118_ccf53b` | 33 | turn_limit | — | dm_response, key_pickup, message_delayed, message_sent, spurious_block |
| `20260413_020034_676870` | 44 | stuck | — | dm_response, message_delayed, message_sent, spurious_block |
| `20260413_020336_5e9e56` | 46 | turn_limit | — | **all 6**: dm_response, door_encounter, key_pickup, message_delayed, message_sent, spurious_block |
| `20260413_020405_61991a` | 46 | turn_limit | — | dm_response, message_delayed, message_sent, spurious_block |

## Architecture

```
dungeon.py       — grid generation, world state, fog-of-war, tool execution, termination
agents.py        — Anthropic API calls, Langfuse v3 tracing (context managers), DM handler
tracer.py        — events.jsonl writer, Langfuse client, summary/export helpers
run.py           — game loop (plain while loop), CLI entry point
viewer/          — single index.html, loads .jsonl client-side, no build step
docs/agents.md   — agent design reference
docs/skills.md   — tool schemas and failure injection rules
docs/prompt.md   — full build spec
docs/To-do.md    — implementation checklist
```

## What the traces show

- **Spurious blocks** (~10% of valid moves): agent expected a passable cell, tool rejected the move. Flagged as `anomaly: true, anomaly_reason: "spurious_block"`. Surfaced as belief divergences in the viewer — the grid showed the cell was empty but the move failed.
- **Message delays** (~15% of sends): message delivered one turn later than expected. Flagged as `anomaly_reason: "message_delayed"`.
- **Key held but unused** (seed 999): Agent B picks up the key on turn 3 but both agents oscillate without finding the door. The `world_truth.key_location: null` combined with `inventory: ["key"]` on every subsequent B event tells the story cleanly.

## Langfuse traces

Each run prints a Langfuse trace URL on completion. Every agent turn is a top-level span containing a generation (LLM call with input/output/tokens) and a child span (tool execution with result and anomaly metadata).
