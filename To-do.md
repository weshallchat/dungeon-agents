# Dungeon Agents — Implementation To-Do

Work through these phases in order. Do not move to the next phase until the current one is verifiably working. Each task has a clear done condition.

---

## Phase 0: Project Setup

- [ ] Initialize git repo and make first commit (empty skeleton)
- [ ] Create directory structure:
  ```
  dungeon.py / agents.py / tracer.py / run.py
  viewer/index.html
  runs/
  .env / .env.example / .gitignore
  requirements.txt / agents.md / skills.md
  ```
- [ ] Add to `.gitignore`: `.env` (add `runs/` temporarily during dev; remove before Phase 5)
- [ ] Create `.env.example`:
  ```
  LANGFUSE_PUBLIC_KEY=pk-...
  LANGFUSE_SECRET_KEY=sk-...
  LANGFUSE_HOST=https://cloud.langfuse.com
  ANTHROPIC_API_KEY=sk-ant-...
  ```
- [ ] `requirements.txt`: `anthropic`, `langfuse`, `python-dotenv`
- [ ] Verify env loading: quick `python -c "from dotenv import load_dotenv; load_dotenv(); import os; print(os.getenv('ANTHROPIC_API_KEY')[:8])"`
- [ ] Commit: `chore: project skeleton and dependencies`

---

## Phase 1: The Dungeon World (`dungeon.py`)

### 1.1 Cell Types and Grid

- [ ] Define cell types (enum or constants): `EMPTY`, `WALL`, `KEY`, `LOCKED_DOOR`, `OPEN_DOOR`, `EXIT`
- [ ] Implement `generate_grid(size=8, seed=None) -> Grid`:
  - Place walls randomly (~15% of cells)
  - Place exactly one KEY, one LOCKED_DOOR, one EXIT
  - Ensure EXIT is not adjacent to LOCKED_DOOR
  - Validate full connectivity (BFS from a random empty cell — all non-wall cells reachable). Re-generate if not.
- [ ] Implement `place_agents(grid) -> (pos_A, pos_B)`: random empty cells, not on items, not on each other

### 1.2 World State

- [ ] Implement `WorldState` dataclass:
  - `grid` — 2D cell array
  - `agent_positions` — `{"A": [r,c], "B": [r,c]}`
  - `inventories` — `{"A": [], "B": []}`
  - `door_state` — `"locked"` | `"unlocked"`
  - `message_queues` — `{"A": [], "B": [], "DM": []}` — each message is `{"from": str, "content": str, "deliver_on_turn": int}`
  - `turn` — global int counter, starts at 1
  - `seed` — stored for summary.json
  - `history` — list of last N grid snapshots for DM stale view (keep last 5)

### 1.3 Observable State Builders

- [ ] `get_explorer_state(world, agent_id) -> dict`: returns position, inventory, visible 5-cell fog-of-war view, pending messages for this agent on this turn
- [ ] `get_dm_state(world) -> dict`: returns the full grid as it was 3 turns ago (from `world.history`). If fewer than 3 turns have passed, returns current state. DM also receives its pending messages.

### 1.4 Tool Execution

Implement `execute_tool(world, agent_id, tool_name, args, rng) -> (result_str, anomaly: bool, anomaly_reason: str | None)`:

- [ ] `move(direction)`: validate direction ∈ {north, south, east, west}. Check in-bounds and not wall/locked_door. Apply ~10% spurious block (seeded RNG) → `anomaly_reason: "spurious_block"`. On success, update position.
- [ ] `observe()`: return current + 4 adjacent cells. Always succeeds.
- [ ] `pick_up(item)`: item must be in agent's current cell. Add to inventory, remove from grid. Error if not present.
- [ ] `send_message(to, content)`: `to` ∈ {"A", "B", "DM"}. Enqueue with `deliver_on_turn = world.turn + 1`. Apply ~15% extra delay (deliver on turn + 2 instead) → `anomaly_reason: "message_delayed"`.
- [ ] `unlock_door()`: agent must be on LOCKED_DOOR cell and hold the key. Set `door_state = "unlocked"`, change cell to `OPEN_DOOR`.

### 1.5 Termination Checks

- [ ] `check_termination(world, stuck_tracker) -> (done: bool, outcome: str, reason: str)`:
  - `success`: both A and B are on EXIT cell
  - `turn_limit`: `world.turn > 50`
  - `stuck`: both A and B have same position for 3 consecutive turns with no successful actions
  - `key_blocked`: key-holder's position unchanged for 3 turns AND other agent has failed to pass LOCKED_DOOR for 3 turns
- [ ] Implement `StuckTracker` to track per-agent position history and action success history

- [ ] **Done condition**: `python -c "from dungeon import generate_grid, WorldState, place_agents; w = WorldState(generate_grid()); print(w.grid)"` runs cleanly.

---

## Phase 2: The Agent Loop (`agents.py`)

Refer to `agents.md` and `skills.md` for agent context design and tool schemas.

### 2.1 Tool Schemas

- [ ] Define all 5 explorer tools as Anthropic SDK tool dicts (see `skills.md` for exact schemas)
- [ ] Define 1 DM tool: `respond_to_agent(to, content)` — the only action DM can take

### 2.2 Explorer Agent Turn

- [ ] Implement `run_explorer_turn(world, agent_id, tracer, rng) -> dict` (returns the event):
  1. Deliver pending messages from queue (where `deliver_on_turn <= world.turn`)
  2. Call `get_explorer_state()` to build context
  3. Build messages array: system prompt + user message with state as JSON
  4. Record `t_start = time.time()`
  5. Call `anthropic.messages.create(model=..., tools=EXPLORER_TOOLS, tool_choice={"type": "any"}, messages=...)`
  6. Capture raw text content from response (may be empty string if model only returns tool use)
  7. Extract tool name and args from `response.content`
  8. Call `execute_tool(world, agent_id, tool_name, args, rng)`
  9. Build and return event dict (see tracer.py)

### 2.3 DM Reactive Handler

- [ ] Implement `maybe_run_dm(world, tracer) -> dict | None`:
  1. Check if DM has pending messages with `deliver_on_turn <= world.turn`. If none, return None immediately (no LLM call, no event).
  2. Deliver all pending DM messages (mark as delivered)
  3. Call `get_dm_state()` for the N=3 stale board view
  4. Build messages array: system prompt + delivered messages + stale board state
  5. Call Anthropic API with DM tool (`respond_to_agent`)
  6. Enqueue DM's response into target agent's message queue with `deliver_on_turn = world.turn + 1`
  7. Build and return a `dm_response` event

### 2.4 Game Loop (`run.py`)

- [ ] Implement `run_game(seed=None) -> (list[Event], summary)`:
  ```python
  world = WorldState(generate_grid(seed=seed), seed=seed)
  rng = random.Random(seed)
  events = []
  stuck_tracker = StuckTracker()

  while True:
      events.append(run_explorer_turn(world, "A", tracer, rng))
      dm_event = maybe_run_dm(world, tracer)   # fires only if A messaged DM
      if dm_event: events.append(dm_event)
      done, outcome, reason = check_termination(world, stuck_tracker)
      if done: break

      events.append(run_explorer_turn(world, "B", tracer, rng))
      dm_event = maybe_run_dm(world, tracer)   # fires only if B messaged DM
      if dm_event: events.append(dm_event)
      done, outcome, reason = check_termination(world, stuck_tracker)
      if done: break

      world.history.append(snapshot(world.grid))
      world.turn += 1
  ```
- [ ] `run.py` entry point: parse optional `--seed` arg, call `run_game`, print summary to stdout

- [ ] **Done condition**: `python run.py` completes without error. Agents take turns. Game ends. DM only fires when messaged — verify by checking that DM events in `events.jsonl` only appear on turns where a `send_message` to "DM" was logged.

---

## Phase 3: Tracing (`tracer.py`)

### 3.1 Structured Event Log

- [ ] Implement `build_event(...)  -> dict` using the schema from `prompt.md`:
  - `agent_belief` — what the agent was told (from `get_explorer_state()` output before action)
  - `world_truth.adjacent_cell_contents` — ground truth for the same 5 cells at that moment
  - `world_truth.key_location` — actual key position from `world.grid` (None if picked up)
  - `world_truth.door_state`
  - `llm.raw_response` — the raw text content from the API response
- [ ] Implement `append_event(run_id, event)`: serialize to JSON, append line to `runs/<run_id>/events.jsonl`
- [ ] Implement `write_summary(run_id, outcome, reason, total_turns, final_positions, seed)`: write `runs/<run_id>/summary.json`

### 3.2 Langfuse Integration

- [ ] Initialize Langfuse client from env vars at module load
- [ ] In `run_explorer_turn`: wrap each turn in a Langfuse **trace** (`name="explorer_turn"`)
  - Inside: one **generation** for the LLM call (input=messages, output=raw_response, model, usage, latency)
  - Inside: one **span** for tool execution (name=tool_name, input=args, output=result, metadata={anomaly, anomaly_reason})
- [ ] In `maybe_run_dm`: wrap in a Langfuse **trace** (`name="dm_response"`) with same generation + span structure — only when the DM is actually called
- [ ] After `run_game` completes: call `langfuse.flush()`
- [ ] Export traces: call Langfuse SDK export for the run's trace IDs → write to `runs/<run_id>/langfuse_export.json`
- [ ] Print Langfuse trace URL to stdout after each run

- [ ] **Done condition**: After `python run.py`: `runs/<run_id>/events.jsonl` exists with complete events. Open Langfuse URL — every turn is a trace with generation + tool span nested inside. `langfuse_export.json` exists and is non-empty.

---

## Phase 4: The Legibility Viewer (`viewer/index.html`)

Single HTML file. No server. No build step. Works by opening directly in a browser.

### 4.1 File Loading

- [ ] Add `<input type="file" accept=".jsonl">` at the top of the page
- [ ] On file select: read with `FileReader`, split by newline, parse each line as JSON, store as `events[]` array
- [ ] Also accept a `summary` (optionally load `summary.json` via a second file input)
- [ ] Once loaded, render all three views

### 4.2 Incident Summary

- [ ] Compute from `events[]`:
  - Total turns, outcome, failure reason
  - Anomaly count and list: `{turn, agent_id, tool, anomaly_reason}`
  - Belief divergence count and list: turns where `agent_belief.visible_cells[dir] != world_truth.adjacent_cell_contents[dir]` for any direction, OR where `world_truth.key_location` differs from what agent expected
- [ ] Render as a compact header block at the top of the page

### 4.3 Timeline View

- [ ] Render a `<table>` with columns: `Turn | Agent | Tool | Args | Result | Flags`
- [ ] Flag icons: 🔴 anomaly, 🟡 belief divergence, 📬 message delay
- [ ] Clicking a row expands it (toggle) to show `agent_belief` and `world_truth` as two side-by-side `<pre>` blocks
- [ ] Highlight diverging fields in the expanded view (red text for fields that differ)
- [ ] DM `dm_response` events should be shown as a distinct row with a different background color (e.g., muted blue) to distinguish them from explorer turns

### 4.4 Message Trace

- [ ] Filter events to `send_message` tool calls and `message_received` / `dm_response` event types
- [ ] Render as a vertical timeline: Agent A column (left), DM column (center), Agent B column (right)
- [ ] Each message is a labeled row with an arrow SVG showing direction
- [ ] Normal messages: black arrow. Delayed messages: red arrow with "delayed +1" label
- [ ] Show turn number at send and receive points

### 4.5 Belief Divergence Utility

- [ ] Implement `findDivergences(events)` in JavaScript:
  - For each event, compare `agent_belief.visible_cells` to `world_truth.adjacent_cell_contents`
  - Also flag any turn where DM gave information that contradicts `world_truth` (cross-reference DM `dm_response` events with ground truth at that turn)
  - Return array of `{turn, agent_id, field, believed, actual}`
- [ ] Use this in both the Incident Summary and to highlight timeline rows

- [ ] **Done condition**: Open `viewer/index.html` in a browser. Load a real `events.jsonl`. Incident summary is populated. Timeline shows all turns. At least one row is highlighted for anomaly or belief divergence. Message trace renders correctly.

---

## Phase 5: Runs and Cleanup

- [ ] Run at least 3 simulations: `python run.py`, varying seeds
- [ ] Target variety: at least one success, one turn_limit failure, one stuck/key_blocked failure
- [ ] If agents always succeed trivially, temporarily set turn limit to 20 to force failures
- [ ] Verify the DM gives at least one piece of stale information that a viewer user can spot
- [ ] Remove `runs/` from `.gitignore` so outputs are committed
- [ ] Verify `viewer/index.html` loads all 3 runs correctly
- [ ] Write `README.md`:
  - Setup: `pip install -r requirements.txt`, copy `.env.example` to `.env`
  - Run simulation: `python run.py` or `python run.py --seed 42`
  - Open viewer: open `viewer/index.html` in a browser, load a file from `runs/<id>/events.jsonl`
  - Model used and why
- [ ] Final commit: `feat: dungeon agents simulation with traces and legibility viewer`

---

## Validation Checklist

- [ ] `python run.py` runs end-to-end without errors
- [ ] `events.jsonl` has a complete event for every turn (A, B, DM) with all schema fields populated
- [ ] `summary.json` has outcome, reason, seed, total turns
- [ ] `langfuse_export.json` is non-empty and Langfuse URL is printed to stdout
- [ ] Viewer loads `events.jsonl` and renders all three views
- [ ] At least one anomaly event is visible in the viewer
- [ ] At least one belief divergence is visible in the viewer (DM gave stale info that differs from ground truth)
- [ ] `.env` is not committed; `.env.example` is committed
- [ ] 3+ run directories are committed under `runs/`
- [ ] Commit history shows incremental progress across all phases
