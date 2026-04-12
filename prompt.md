# Dungeon Agents — Build Prompt

Build a multi-agent dungeon simulation in Python. The goal is not a great dungeon game — it is great observability traces and a legibility tool that helps a human diagnose what happened in a run.

---

## Part 1: The Simulation

### World

- Generate a random 8×8 grid (or larger) at the start of each run.
- Place the following on the grid: one key, one locked door, one exit, and a handful of wall/obstacle cells.
- Starting positions for both agents are random (not on obstacles, not on items, not on each other).
- Fog of war: each explorer agent can only observe its own current cell and the 4 adjacent cells (up, down, left, right). Agents do not have global map visibility.

### Agents

There are three agents:

**Explorer Agents (Agent A and Agent B)**
- Each is backed by an LLM (`claude-haiku-4-5-20251001`).
- Each has a tool set (see `skills.md`): move, observe, pick_up, send_message, unlock_door.
- Agents receive their observable state at the start of each turn. They make one tool call per turn.
- Agents can send messages to each other or to the Dungeon Master.

**Dungeon Master Agent (DM)**
- Also backed by an LLM (`claude-haiku-4-5-20251001`).
- Has visibility of the full board, but only as it was N=3 turns ago (stale state).
- Cannot move or interact with items — can only answer questions sent to it via the message system.
- Purely reactive: the DM has no dedicated turn. It is called immediately after whichever explorer agent messaged it, within the same turn. Its response is queued for delivery on the next turn.
- If no explorer sent a message to DM on a given turn, the DM is not called at all.
- The DM's stale view is the primary source of belief divergence: it may tell agents where items are based on outdated information.

### Game Loop

- Global turn counter starts at 1 and increments after each full round (A + B + DM = one round).
- Each turn within a round: build the agent's context, deliver pending messages, call the LLM, execute the tool call, record the event.
- Message delivery: a message sent on turn N is delivered to the recipient at the start of turn N+1 (their next turn).
- The game ends when:
  - Both explorer agents reach the exit (success), OR
  - A turn limit is hit (50 turns) (failure — `turn_limit`), OR
  - Standard stuck: both explorer agents have had the same position, performed no successful actions, for 3 consecutive turns each (failure — `stuck`), OR
  - Key-blocked stuck: the key-holder is stuck AND the other agent has been unable to pass the locked door for 3 consecutive turns (failure — `key_blocked`). This catches the case where progress is impossible but neither agent individually triggers the stuck condition.
- The shared objective: Agent A or B picks up the key, unlocks the door, and both agents reach the exit. Once the door is unlocked it stays open permanently.

### Tool Failure Simulation

- Introduce occasional realistic tool failures to make traces interesting:
  - `move` has a ~10% chance of returning "path blocked unexpectedly" even if the cell is valid. `anomaly_reason: "spurious_block"`.
  - `send_message` has a ~15% chance of being delayed an extra turn. `anomaly_reason: "message_delayed"`.
- Failures must be seeded (pass `seed` into the RNG at run start) for reproducibility.
- Log all anomalies explicitly in the event — do not silently swallow them.

---

## Part 2: The Traces

### Observability Integration

- Integrate Langfuse to capture agent traces.
- Every LLM call must be a Langfuse **generation** with:
  - Full prompt (messages array) as input
  - Full raw LLM text response as output (log the raw response even when the model only returns a tool call — capture whatever text content the model produced)
  - Model name, prompt tokens, completion tokens, latency
  - Metadata: `run_id`, `turn`, `agent_id`
- Every tool execution must be a Langfuse **span** (child of the generation) with:
  - Tool name and input args
  - Tool result string
  - `anomaly: bool`, `anomaly_reason: str | null`
- At the end of each run, export all traces for that run to `runs/<run_id>/langfuse_export.json` using the Langfuse SDK's export API. Also print the Langfuse trace URL to stdout.
- Flush the Langfuse client at the end of every run (`langfuse.flush()`).

### Structured Event Log

Write a structured JSON event record for every agent step (including DM turns). The schema must support diagnosing failures across runs, not just replaying one. Every event captures:

```json
{
  "run_id": "...",
  "turn": 4,
  "agent_id": "A" | "B" | "DM",
  "event_type": "tool_call" | "message_received" | "dm_response" | "anomaly",
  "timestamp_ms": 1712345678901,
  "agent_belief": {
    "position": [row, col],
    "inventory": [],
    "visible_cells": {
      "current": "EMPTY",
      "north": "WALL",
      "south": "KEY",
      "east": "EMPTY",
      "west": "LOCKED_DOOR"
    },
    "pending_messages": [],
    "known_map": {}
  },
  "world_truth": {
    "actual_position": [row, col],
    "adjacent_cell_contents": {
      "current": "EMPTY",
      "north": "WALL",
      "south": "EMPTY",
      "east": "EMPTY",
      "west": "LOCKED_DOOR"
    },
    "door_state": "locked" | "unlocked",
    "key_location": [row, col] | null
  },
  "action": {
    "tool": "move",
    "args": {"direction": "north"},
    "result": "...",
    "anomaly": false,
    "anomaly_reason": null
  },
  "llm": {
    "prompt_tokens": 300,
    "completion_tokens": 50,
    "latency_ms": 820,
    "raw_response": "..."
  }
}
```

Key design intent:
- `agent_belief.visible_cells` is what the agent was told. `world_truth.adjacent_cell_contents` is ground truth. These diverge when a DM gives stale advice that caused the agent to update its belief incorrectly, or when an anomaly occurs.
- `world_truth.key_location` lets a post-run tool immediately see if an agent believed the key was somewhere it wasn't.
- `agent_belief.known_map` is a running map the agent builds from its observations — compare against ground truth to surface stale beliefs.

Append all events to: `runs/<run_id>/events.jsonl` (one JSON object per line).

---

## Part 3: The Legibility Layer

Build a tool that lets a human answer three questions about any completed run:

1. **What happened?** — a turn-by-turn timeline
2. **Why did it happen?** — belief state at each decision point vs. what was actually true
3. **What should change next?** — anomalies, divergences, and failure causes surfaced clearly

### What to Build

A single `viewer/index.html` file. It must work by opening the file directly in a browser (no server required). Use a `<input type="file">` to load a local `events.jsonl` file, parse it client-side with JavaScript, and render the views below.

**Incident Summary (top of page)**
- Outcome (success / failure reason), total turns, anomaly count, belief divergence count
- Bullet list of each anomaly: turn, agent, tool, reason
- Bullet list of each belief divergence: turn, agent, what it believed vs. what was true

**Timeline View**
- Table: `Turn | Agent | Tool | Args summary | Result | Flags`
- Flags column shows icons/labels for: anomaly, belief divergence, message delay
- Clicking a row expands it inline to show the full `agent_belief` and `world_truth` JSON side-by-side

**Message Trace**
- Two-column layout: Agent A on left, DM in center, Agent B on right
- Each message rendered as a labeled arrow from sender to receiver
- Delayed messages (anomaly) rendered in a different color with a "delayed" label
- Show the turn number at send and receive

Keep the visual design minimal and intentional. No CSS frameworks (no Tailwind, no Bootstrap). Write all styles by hand. Every design choice should be deliberate — the goal is clarity, not completeness.

---

## Part 4: Running and Output

- Run with: `python run.py` (optional: `python run.py --seed 42`)
- Each run generates a unique `run_id` (timestamp + short UUID).
- Output written to `runs/<run_id>/`:
  - `events.jsonl` — structured event log, one JSON object per line
  - `summary.json` — outcome, total turns, final positions, failure reason, seed used
  - `langfuse_export.json` — exported Langfuse traces for this run
- Run the simulation at least 3 times, aiming for a mix of outcomes (success, turn_limit, stuck/key_blocked). Commit the full `runs/` directory — the reviewer should be able to load any run in the viewer without re-running.

---

## Implementation Notes

- Use `claude-haiku-4-5-20251001` for all three agents.
- Keep all system prompts short (< 100 words each). Do not prompt-engineer agents to play well.
- Use the Anthropic Python SDK (`anthropic`) directly for all LLM calls.
- Use LangChain or LangGraph only if there is a specific, concrete reason — not by default.
- Game loop is a plain Python `while` loop. No graph framework needed.
- Load Langfuse credentials from `.env` using `python-dotenv`. Required vars: `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST`, `ANTHROPIC_API_KEY`. Add `.env` to `.gitignore`. Commit `.env.example` with placeholder values.
- Refer to `agents.md` for agent design details and `skills.md` for tool schemas and failure rules.

```
/
├── dungeon.py          # world state, grid generation, item logic
├── agents.py           # LLM agent turns (explorer + DM)
├── tracer.py           # event logging, Langfuse integration
├── run.py              # entry point, game loop
├── viewer/
│   └── index.html      # legibility layer, runs entirely in browser
├── runs/               # committed run outputs (3+ runs)
├── agents.md           # agent design reference
├── skills.md           # tool schemas and failure rules
├── .env.example
└── requirements.txt
```

---

## What Success Looks Like

- `python run.py` completes end-to-end. `runs/<run_id>/events.jsonl` has a complete event for every turn including DM turns.
- At least one run has a visible belief divergence: the DM gave stale information, an agent acted on it, and the viewer shows the mismatch between `agent_belief` and `world_truth`.
- The viewer loads any `events.jsonl` and clearly answers: what happened, why, and what went wrong.
- The trace schema feels like something you would use to debug a real production multi-agent system.
