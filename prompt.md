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

**Open issue:** In runs to date, agents never message the DM even when instructed to. Before running the 50-sim batch, decide whether to strengthen the DM mention in the explorer system prompt. Without DM interactions, the "stale data decisions" metric (see analysis.json) cannot be computed.

### Game Loop

- Global turn counter starts at 1 and increments after each full round (A + B = one round).
- Each turn: build the agent's context, deliver pending messages, call the LLM, execute the tool call, record the event.
- Message delivery: a message sent on turn N is delivered to the recipient at the start of turn N+1.
- The game ends when:
  - Both explorer agents reach the exit (success), OR
  - A turn limit is hit (50 turns) (failure — `turn_limit`), OR
  - Standard stuck: both agents have same position and no successful actions for 3 consecutive turns each (failure — `stuck`), OR
  - Key-blocked stuck: key-holder is stuck AND other agent has been unable to pass the locked door for 3 consecutive turns (failure — `key_blocked`).
- The door stays open permanently once unlocked.

### Tool Failure Simulation

- `move` has a ~10% chance of returning "path blocked unexpectedly" even if the cell is valid. `anomaly_reason: "spurious_block"`.
- `send_message` has a ~15% chance of being delayed an extra turn. `anomaly_reason: "message_delayed"`.
- Failures must be seeded (pass `seed` into the RNG at run start) for reproducibility.
- Log all anomalies explicitly — do not silently swallow them.

---

## Part 2: The Traces

### Observability Integration (Langfuse)

- Integrate Langfuse v3 (context manager API) to capture agent traces.
- **Session grouping**: every turn within a run belongs to the same Langfuse session, identified by `run_id`. This allows viewing a full run end-to-end in one Langfuse session view.
- Every LLM call must be a Langfuse **generation** within the session with:
  - Full prompt (messages array) as input
  - Full raw LLM text response as output
  - Model name, prompt tokens, completion tokens, latency
  - Metadata: `run_id`, `turn`, `agent_id`, `outcome` (added at run end via session update)
- Every tool execution must be a Langfuse **span** (child of the generation) with:
  - Tool name, structured input args, structured result object
  - `anomaly: bool`, `anomaly_reason: str | null`
- **Progress score**: attach a Langfuse score to each turn's trace:
  - `0.0` = start state
  - `0.33` = key picked up by either agent
  - `0.66` = door unlocked
  - `1.0` = both agents at exit
  - Score name: `"progress"`, value: float, comment: current milestone label
- At run end, export all trace URLs to `runs/<run_id>/langfuse_export.json`.

### Structured Event Log

Write a structured JSON event record for every agent step. Schema must support diagnosis after the fact and aggregation across runs.

**Tool results must be structured objects, not raw strings.** Each tool has a defined result schema:

```json
// move result
{"status": "success" | "failed" | "anomaly", "new_position": [r, c] | null, "reason": "wall" | "out_of_bounds" | "locked_door" | "spurious_block" | null}

// observe result
{"status": "success", "cells": {"current": "EMPTY", "north": "WALL", ...}}

// pick_up result
{"status": "success" | "failed", "item": "key", "reason": null | "no_item_here"}

// send_message result
{"status": "sent" | "delayed", "to": "B", "deliver_on_turn": 5}

// unlock_door result
{"status": "success" | "failed", "reason": null | "no_key" | "not_on_door"}

// respond_to_agent result (DM)
{"status": "sent", "to": "A", "deliver_on_turn": 5}
```

**Full event schema:**

```json
{
  "run_id": "...",
  "turn": 4,
  "agent_id": "A" | "B" | "DM",
  "event_type": "tool_call" | "anomaly" | "dm_response",
  "timestamp_ms": 1712345678901,
  "game_state_summary": {
    "turn": 4,
    "key_held_by": "B" | null,
    "door_state": "locked" | "unlocked",
    "agent_positions": {"A": [r, c], "B": [r, c]},
    "both_at_exit": false,
    "progress_score": 0.33
  },
  "agent_belief": {
    "position": [r, c],
    "inventory": [],
    "visible_cells": {"current": "EMPTY", "north": "WALL", ...},
    "pending_messages": []
  },
  "world_truth": {
    "actual_position": [r, c],
    "adjacent_cell_contents": {"current": "EMPTY", "north": "WALL", ...},
    "door_state": "locked" | "unlocked",
    "key_location": [r, c] | null
  },
  "action": {
    "tool": "move",
    "args": {"direction": "north"},
    "result": {"status": "anomaly", "new_position": null, "reason": "spurious_block"},
    "anomaly": true,
    "anomaly_reason": "spurious_block"
  },
  "llm": {
    "prompt_tokens": 300,
    "completion_tokens": 50,
    "latency_ms": 820,
    "raw_response": "..."
  }
}
```

Key design goals:
- `game_state_summary` provides macro context on every event — no need to reconstruct from the stream.
- Structured `action.result` objects make aggregation across runs trivial (count failures by `reason`, etc.).
- `agent_belief` vs `world_truth` divergences surface stale-information failures.

Append all events to: `runs/<run_id>/events.jsonl` (one JSON object per line).

---

## Part 3: The Legibility Layer (`viewer/index.html`)

A single HTML file, no server, no build step. Opens directly in a browser. Loads a local `events.jsonl` via `<input type="file">` and optionally an `analysis.json` for cross-run aggregate views.

### View 1: Incident Summary (top of page)

**LLM-generated narrative** (produced by post-processing script `analyze.py`, stored in `summary.json` as `incident_report` field):
- A 3-5 sentence plain-English account of what happened, why it failed, and what the most notable decision points were.
- Should reference specific turn numbers and agent IDs.
- Example: "Agent B picked up the key on turn 3 but spent 47 turns oscillating in the north-east quadrant. Agent A never left the top-left corner. The run ended at the turn limit with the door never reached. The 12 spurious blocks on Agent A's movements likely contributed to its failure to explore."

**Computed stats block:**
- Outcome, total turns, anomaly count, belief divergence count, key milestone turns (when key picked up, when door unlocked)
- Bullet list: each anomaly with turn, agent, tool, reason
- Bullet list: each belief divergence with turn, agent, what was believed vs. what was true

### View 2: Key-Moment Grid Visualization

Show a small dungeon grid for each "notable" turn. A turn is notable if:
- It is turn 1 (initial state)
- An anomaly occurred (spurious_block, message_delayed)
- The key was picked up
- The door was encountered (agent tried to move into locked door)
- The door was unlocked
- A message was sent or received

Each grid is:
- An 8×8 (or NxN) CSS grid rendered in HTML/CSS — no canvas, no SVG
- Cells colour-coded: WALL=dark, EMPTY=mid-grey, KEY=yellow, LOCKED_DOOR=red, OPEN_DOOR=green, EXIT=bright green
- Agent positions shown as coloured circles overlaid on cells (A=green, B=blue)
- A label below each grid: `"Turn N — [what happened]"`
- Grids laid out horizontally in a scrollable row

### View 3: Timeline Table

Table: `Turn | Agent | Tool | Args | Result | Reasoning | Flags`
- **Reasoning column**: one-line truncated text from `llm.raw_response` — the agent's stated reason for choosing that tool. Shown as italic grey text, truncated to ~80 chars.
- Flag icons: ⚠ anomaly, ◆ belief divergence, ⧗ message delay
- Anomaly rows: red left border. Divergence rows: yellow left border. DM rows: blue left border.
- Clicking a row expands it inline to show `agent_belief` and `world_truth` JSON side-by-side, with diverging fields highlighted.

### View 7: Agent Movement Trace

Interactive grid showing both agents' movement paths through the dungeon over all turns.

- Full revealed map built from all events (fog-of-war cleared progressively)
- **Slider**: scrub to any turn — grid updates to show agent positions at that turn
- **Play/Pause button**: auto-steps through turns at a fixed interval (~400ms/turn)
- Agent A path shown as faded green trail dots on previously visited cells
- Agent B path shown as faded blue trail dots on previously visited cells
- Current positions shown as solid coloured circles (A=green, B=blue)
- Turn counter and game state summary (door state, key holder) displayed alongside
- Data sourced from `game_state_summary.agent_positions` on each event

### View 4: Charts

Three small charts rendered with plain `<canvas>` (no chart library):
1. **Anomaly rate over turns** — bar chart, one bar per turn, height = number of anomalies that turn (0 or 1 per agent). Colour bars by anomaly type.
2. **Latency per turn** — line chart, one point per turn, y = LLM latency in ms, colour-coded by agent (A=green, B=blue).
3. **Token usage per turn** — stacked bar chart, prompt tokens vs. completion tokens per turn.

All three charts are the same width, stacked vertically, with shared x-axis (turn number).

### View 5: Message Trace

- Two-column layout: Agent A (left), DM (centre), Agent B (right).
- Each message as a labelled arrow from sender to receiver with turn number.
- Delayed messages in red with "+1 delay" label.
- If no messages: show "No messages exchanged in this run."

### View 6: Cross-Run Aggregate (loads analysis.json)

Second file input: `analysis.json`. When loaded, renders:
- **Anomaly distribution** — bar chart: anomaly count per run, sorted descending. Colour by outcome type.
- **Turns per outcome type** — box plot or grouped bar chart: distribution of total turns by outcome (success / turn_limit / stuck / key_blocked).
- **Stale data decisions** — once DM interactions exist: per-run count of turns where agent acted on DM info that was contradicted by world_truth. Deferred until DM interactions are present in runs.
- **Distinct event types per run** — table showing which "interesting" events each run had (key_pickup, door_encounter, message_sent, spurious_block, etc.) — used to show selection criteria for the best 10.

Keep visual design minimal and intentional. No CSS frameworks. Plain CSS, hand-written. Every layout choice deliberate.

---

## Part 4: Post-Processing (`analyze.py`)

A standalone script that reads all runs in `runs/` and produces:

1. **`analysis.json`** at repo root:
```json
{
  "generated_at": "...",
  "total_runs": 50,
  "runs": [
    {
      "run_id": "...",
      "seed": 42,
      "outcome": "turn_limit",
      "total_turns": 51,
      "anomaly_count": 13,
      "distinct_event_types": ["spurious_block", "key_pickup", "door_encounter"],
      "key_pickup_turn": 3,
      "door_unlocked_turn": null,
      "messages_sent": 0,
      "dm_interactions": 0,
      "progress_score_final": 0.33,
      "selected_for_repo": true
    }
  ],
  "aggregate": {
    "outcome_distribution": {"turn_limit": 48, "success": 1, "stuck": 1},
    "anomaly_rate_by_run": [...],
    "avg_turns_by_outcome": {...},
    "stale_data_decisions": "deferred — no DM interactions in current runs"
  }
}
```

2. **LLM-generated `incident_report`** for each run: a 3-5 sentence narrative generated by one Claude call per run that reads the run's `events.jsonl` and `summary.json`. The report is written back into the run's `summary.json` as `"incident_report": "..."`.

3. **Selection of best 10 runs**: ranked by number of distinct event types (key_pickup, door_encounter, message_sent, spurious_block, dm_response, door_unlocked). Ties broken by anomaly count. Mark `"selected_for_repo": true` in `analysis.json`. Only these 10 runs' full `events.jsonl` files are committed to the repo.

---

## Part 5: Running and Output

- Run a single sim: `python run.py [--seed N]`
- Run 50 sims: `python run.py --batch 50` (generates seeds 1–50)
- Post-process all runs: `python analyze.py` (reads `runs/`, writes `analysis.json`, generates incident reports)
- View: open `viewer/index.html`, load `events.jsonl` from any committed run, optionally load `analysis.json`

Output structure per run (`runs/<run_id>/`):
- `events.jsonl` — structured event log
- `summary.json` — outcome, reason, seed, Langfuse URL, `incident_report` (added by analyze.py)
- `langfuse_export.json` — trace URLs

Commit to repo:
- `analysis.json` — always (all 50 runs)
- `runs/<id>/` — only the 10 selected runs' full directories

---

## Implementation Notes

- Use `claude-haiku-4-5-20251001` for explorer and DM agents.
- Use `claude-haiku-4-5-20251001` for incident report generation in `analyze.py` (cheap per call).
- Langfuse v3 context manager API throughout — no `.trace()` method.
- Game loop is a plain Python `while` loop. No graph framework.
- Load credentials from `.env` via `python-dotenv`.
- Refer to `agents.md` for agent design and `skills.md` for tool schemas.

```
/
├── dungeon.py          # world state, grid, tool execution
├── agents.py           # LLM agent turns, DM handler
├── tracer.py           # event logging, Langfuse integration
├── run.py              # game loop, CLI (--seed, --batch)
├── analyze.py          # post-processing: analysis.json, incident reports, run selection
├── viewer/index.html   # legibility layer — all 6 views
├── runs/               # only 10 selected runs committed
├── analysis.json       # aggregate data across all 50 runs
├── agents.md
├── skills.md
├── .env.example
└── requirements.txt
```

---

## What Success Looks Like

- `python run.py --batch 50` runs 50 simulations end-to-end.
- `python analyze.py` produces `analysis.json` with all 50 runs summarised and selects the best 10.
- `viewer/index.html` loads any committed run and shows: incident report, key-moment grids, timeline, charts, and message trace.
- Loading `analysis.json` into the viewer renders cross-run aggregate charts.
- At least one committed run has a DM interaction (once DM prompt issue is resolved).
- The trace schema is clean enough that `analysis.py` can aggregate across 50 runs with simple JSON field reads — no string parsing.
