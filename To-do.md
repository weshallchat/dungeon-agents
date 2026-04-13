# Dungeon Agents — Implementation To-Do (v2)

Phases 0–4 are complete. This document tracks the remaining work for the v2 improvements.

---

## Phase 6: Structured Tool Results + game_state_summary (`dungeon.py`, `tracer.py`)

### 6.1 Structured tool result objects

Replace all raw string returns from `execute_tool` with typed dicts:

- [ ] `move` → `{"status": "success"|"failed"|"anomaly", "new_position": [r,c]|null, "reason": null|"wall"|"out_of_bounds"|"locked_door"|"spurious_block"}`
- [ ] `observe` → `{"status": "success", "cells": {"current":..., "north":..., ...}}`
- [ ] `pick_up` → `{"status": "success"|"failed", "item": "key", "reason": null|"no_item_here"}`
- [ ] `send_message` → `{"status": "sent"|"delayed", "to": "B", "deliver_on_turn": N}`
- [ ] `unlock_door` → `{"status": "success"|"failed", "reason": null|"no_key"|"not_on_door"}`
- [ ] `respond_to_agent` → `{"status": "sent", "to": "A", "deliver_on_turn": N}`
- [ ] Update `agents.py` to read structured result objects instead of string matching (e.g. `result["status"] == "success"` not `"cannot" not in result`)
- [ ] Update `run.py` stdout logging to read from `result["status"]` and `result.get("reason")`

### 6.2 Progress score helper

- [ ] Implement `compute_progress_score(world) -> float` in `dungeon.py`:
  - `0.0` = neither agent has key, door locked
  - `0.33` = key held by either agent
  - `0.66` = door unlocked
  - `1.0` = both agents at EXIT cell
- [ ] Add `compute_milestone_label(score) -> str` → `"start" | "key_acquired" | "door_unlocked" | "complete"`

### 6.3 game_state_summary on every event

- [ ] Implement `build_game_state_summary(world) -> dict` in `dungeon.py`:
  ```python
  {
    "turn": world.turn,
    "key_held_by": agent_id_holding_key | None,
    "door_state": world.door_state,
    "agent_positions": dict(world.agent_positions),
    "both_at_exit": bool,
    "progress_score": float
  }
  ```
- [ ] Add `game_state_summary` parameter to `build_event()` in `tracer.py` and include it in the event dict
- [ ] Pass `game_state_summary` from `agents.py` when calling `build_event()`

- [ ] **Done condition**: Run one sim. Open `events.jsonl` — every event has `game_state_summary` with all 5 fields, and every `action.result` is a dict not a string.

---

## Phase 7: Langfuse Session Grouping + Progress Scores (`tracer.py`, `agents.py`)

### 7.1 Session grouping

- [ ] In `tracer.py`, initialise a Langfuse session at the start of each run:
  ```python
  session_id = run_id  # one session per run
  ```
- [ ] Pass `session_id=run_id` to every `lf.start_as_current_span()` call in `agents.py`
- [ ] This groups all turns of a run into one Langfuse session view

### 7.2 Progress score as Langfuse score

- [ ] After each explorer turn, call `lf.score_current_trace(...)` with:
  - `name="progress"`
  - `value=progress_score` (float 0.0–1.0)
  - `comment=milestone_label`
- [ ] After each run completes, update the session metadata with final outcome and reason

- [ ] **Done condition**: Open Langfuse. Find a run's session. All turns grouped under one session. Each turn has a `progress` score attached. Score increases monotonically when key is picked up or door is unlocked.

---

## Phase 8: DM Interaction Fix (`agents.py`)

**Blocker for stale data decisions metric.**

- [ ] Add one explicit line to `EXPLORER_SYSTEM` prompt: `"If you cannot find the key or exit, send a message to DM — they have a map of the dungeon (may be 3 turns outdated)."`
- [ ] Run 3 test sims and verify at least 1 DM interaction appears in `events.jsonl` (event_type: `dm_response`)
- [ ] If still no DM interactions after prompt fix, escalate to user for decision

---

## Phase 9: Batch Run + `analyze.py`

### 9.1 Batch mode in `run.py`

- [ ] Add `--batch N` CLI argument: runs N simulations with seeds 1 through N sequentially
- [ ] Print a summary table at the end of batch mode: seed | outcome | turns | anomalies
- [ ] Each run still writes its own `runs/<run_id>/` directory

### 9.2 `analyze.py` — aggregate analysis

- [ ] Read all run directories from `runs/`
- [ ] For each run, load `summary.json` and `events.jsonl`
- [ ] Compute per-run stats:
  - `anomaly_count` — count of events where `action.anomaly == true`
  - `distinct_event_types` — set of notable events: `spurious_block`, `message_delayed`, `key_pickup`, `door_encounter`, `door_unlocked`, `message_sent`, `dm_response`
  - `key_pickup_turn` — turn where `action.tool == "pick_up"` and `action.result.status == "success"`
  - `door_encounter_turn` — first turn where `action.result.reason == "locked_door"`
  - `door_unlocked_turn` — turn where `action.tool == "unlock_door"` and `action.result.status == "success"`
  - `messages_sent` — count of `send_message` tool calls
  - `dm_interactions` — count of `dm_response` events
  - `progress_score_final` — `game_state_summary.progress_score` from the last event
- [ ] Compute aggregate stats:
  - `outcome_distribution` — count per outcome type
  - `avg_turns_by_outcome` — mean total_turns grouped by outcome
  - `anomaly_rate_by_run` — list of `{run_id, anomaly_count}` sorted descending
  - `stale_data_decisions` — if `dm_interactions > 0` across any run: count turns where DM response contradicts `world_truth`; otherwise `"deferred — no DM interactions"`

### 9.3 Run selection

- [ ] Rank all runs by `len(distinct_event_types)` descending, break ties by `anomaly_count` descending
- [ ] Mark top 10 as `"selected_for_repo": true` in the per-run records
- [ ] Write `analysis.json` to repo root

### 9.4 LLM-generated incident reports

- [ ] For each run, call `claude-haiku-4-5-20251001` once with:
  - System: "You are a technical analyst. Write a 3-5 sentence plain-English incident report about this dungeon agent run. Reference specific turn numbers and agent IDs. Explain what happened, why it failed (if it did), and what the most notable decision points were."
  - User: condensed event summary (key events only — anomalies, pickups, door encounters, messages, final state — not all 101 events)
- [ ] Write the report back into `runs/<run_id>/summary.json` as `"incident_report": "..."`
- [ ] Include `incident_report` in `analysis.json` per-run record

- [ ] **Done condition**: `python analyze.py` runs on 50 runs. `analysis.json` exists at repo root with all fields. Each `summary.json` has an `incident_report`. Top 10 runs are marked.

### 9.5 Run the 50 simulations

- [ ] `python run.py --batch 50` with seeds 1–50
- [ ] Verify output: 50 run directories in `runs/`
- [ ] Run `python analyze.py`
- [ ] Delete the `events.jsonl` from the 40 non-selected runs (keep their `summary.json` for reference)
- [ ] Commit: selected 10 full run dirs + all 50 `summary.json` files + `analysis.json`

---

## Phase 10: Viewer v2 (`viewer/index.html`)

Rewrite the viewer to support all 6 views. Keep it a single HTML file with no build step.

### 10.1 File loading

- [ ] Two file inputs: `events.jsonl` (required) and `analysis.json` (optional, enables View 6)
- [ ] On events load: parse JSONL, derive grid size from events, render all single-run views
- [ ] On analysis load: render View 6 (cross-run aggregate)

### 10.2 View 1 — Incident Summary

- [ ] Display `summary.incident_report` if loaded (from a third optional `summary.json` input), else show "Load summary.json to see incident report"
- [ ] Computed stats block: outcome, turns, anomaly count, divergence count, milestone turns (key_pickup_turn, door_unlocked_turn)
- [ ] Issue lists: anomalies (red), divergences (yellow)

### 10.3 View 2 — Key-Moment Grid

- [ ] Implement `findKeyMoments(events) -> Event[]`: filter events where:
  - `ev.turn === 1` (initial state — use `game_state_summary` for positions)
  - `ev.action.anomaly === true` (spurious_block or message_delayed)
  - `ev.action.result.status === "success" && ev.action.tool === "pick_up"`
  - `ev.action.result.reason === "locked_door"` (door encounter)
  - `ev.action.tool === "unlock_door" && ev.action.result.status === "success"`
  - `ev.action.tool === "send_message"`
- [ ] Implement `renderGrid(event, label)`: returns an HTML element — 8×8 CSS grid
  - Read grid state from `world_truth.adjacent_cell_contents` for the 5 known cells; render rest as fog
  - Overlay agent positions from `game_state_summary.agent_positions`
  - Cell colours: WALL=`#1a1a1a`, EMPTY=`#2a2a2a`, KEY=`#c9a227`, LOCKED_DOOR=`#8b0000`, OPEN_DOOR=`#2d6a2d`, EXIT=`#4ec94e`, FOG=`#111`
  - Agent A: green circle `●`, Agent B: blue circle `●`
  - Label below: `"Turn N — what happened"`
- [ ] Render grids in a horizontally scrollable row
- [ ] Cap at 12 key moments to prevent overflow

### 10.4 View 3 — Timeline (existing, update for structured results)

- [ ] Update args/result display to read from structured `action.result` object
- [ ] Update divergence detection to use `action.result.status === "anomaly"` for spurious blocks
- [ ] Keep expand-on-click with belief/truth side-by-side
- [ ] **Add Reasoning column**: show first line of `ev.llm.raw_response` truncated to ~80 chars, italic grey. Empty for DM events with no text output.

### 10.5 View 4 — Charts (plain canvas, no library)

Implement three `<canvas>` charts sharing the same x-axis (turn number):

- [ ] **Anomaly rate bar chart**: one bar per turn (height 0 or 1 per agent). Two series: Agent A (green) and Agent B (blue). Red fill for anomaly bars.
- [ ] **Latency line chart**: one point per turn per agent. Y-axis: ms. Two lines, colour-coded by agent.
- [ ] **Token usage stacked bar**: one bar per turn. Bottom segment = prompt tokens, top = completion tokens. Two series (A, B) side by side per turn.
- [ ] Shared: x-axis labels every 5 turns, y-axis with 4–5 tick marks, legend, chart title
- [ ] Utility function `drawAxis(ctx, x, y, w, h, minV, maxV, nTicks)` to reuse across charts

### 10.6 View 5 — Message Trace (existing, minor updates)

- [ ] Show "No messages exchanged." if none — already done
- [ ] Show DM response events as distinct arrow style (dashed border)

### 10.7 View 6 — Cross-Run Aggregate (loads analysis.json)

- [ ] Second file input triggers this view
- [ ] **Anomaly distribution bar chart** (canvas): one bar per run (x), height = anomaly_count, colour by outcome
- [ ] **Turns per outcome grouped bars** (canvas): group bars by outcome type, show distribution
- [ ] **Stale data decisions**: if `aggregate.stale_data_decisions` is a string, show as a note; if it's a number, show as a stat
- [ ] **Run selection table**: list all 50 runs with columns: seed, outcome, anomalies, distinct_event_types (as badges), selected (✓/—)

- [ ] **Done condition**: Open `viewer/index.html`. Load seed-999 `events.jsonl` — see incident report (or placeholder), key-moment grids, timeline, all 3 charts populated with real data. Load `analysis.json` — see cross-run charts and selection table.

---

## Phase 12: Viewer — Agent Reasoning + Movement Trace

### 12.1 View 3 — Reasoning column
- [x] Add `Reasoning` column to timeline table
- [x] Source from `ev.llm.raw_response` — take first non-empty line, truncate to 80 chars
- [x] Style: italic, `color: #999`, max-width 300px
- [ ] Fix: add one-sentence reasoning instruction to `EXPLORER_SYSTEM` in `agents.py` so future runs populate `raw_response`
- [ ] Fix viewer: synthesize fallback reasoning from tool+args when `raw_response` is empty (covers committed runs)

### 12.2 Movement Trace (now tab 2)
- [x] Add movement tab with slider + play/pause
- [x] `buildPositionHistory(events)` → per-event `{turn, A, B, gss}`
- [x] Full revealed map with trail dots and agent circles
- [ ] Increase grid cell size to 36px
- [ ] Add scrollable event log panel to the right of the grid: every event entry with turn, agent, tool, result, position, inventory; current event highlighted; clicking row jumps slider
- [ ] Tab reorder: Summary · Movement · Timeline · Key Moments · Message Trace · Charts · Cross-Run

---

## Phase 11: Final Commit

- [ ] Verify all 10 selected runs load correctly in the viewer
- [ ] Verify `analysis.json` has all 50 runs summarised
- [ ] `git add` selected run dirs + `analysis.json` + all changed source files
- [ ] Update `README.md` with new scripts (`analyze.py`), new viewer views, and run selection criteria
- [ ] Final commit and push

---

## Validation Checklist

- [ ] Every event in `events.jsonl` has `game_state_summary` with all 5 fields
- [ ] Every `action.result` is a structured dict (no raw strings)
- [ ] Langfuse: each run is one session; each turn has a `progress` score
- [ ] `analysis.json` has all 50 runs; top 10 marked `selected_for_repo: true`
- [ ] Each `summary.json` has an `incident_report` string
- [ ] Viewer: all 6 views render with real data
- [ ] At least 1 DM interaction in the committed runs (pending Phase 8 fix)
- [ ] Commit history shows incremental progress
