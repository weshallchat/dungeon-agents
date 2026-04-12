"""
run.py — Entry point. Game loop: A → [DM if queried] → B → [DM if queried] → repeat.
Usage: python run.py [--seed SEED] [--turn-limit N] [--size N]
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
import uuid
from datetime import datetime

from dungeon import (
    WorldState,
    build_world,
    check_termination,
    find_cell,
    snapshot_grid,
    Cell,
    StuckTracker,
)
from agents import run_explorer_turn, maybe_run_dm
from tracer import export_traces, flush, write_summary, run_dir


def run_game(seed: int | None = None, turn_limit: int = 50, size: int = 8) -> dict:
    run_id = datetime.utcnow().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
    print(f"\n=== Run {run_id} | seed={seed} ===")

    world = build_world(size=size, seed=seed)
    rng = random.Random(seed)
    stuck_tracker = StuckTracker()
    trace_urls: list[str] = []

    print(f"Agent A starts at {world.agent_positions['A']}")
    print(f"Agent B starts at {world.agent_positions['B']}")
    key_loc = find_cell(world.grid, Cell.KEY)
    door_loc = find_cell(world.grid, Cell.LOCKED_DOOR)
    exit_loc = find_cell(world.grid, Cell.EXIT)
    print(f"Key: {key_loc}  Door: {door_loc}  Exit: {exit_loc}\n")

    outcome = "turn_limit"
    reason = f"turn limit of {turn_limit} reached"

    while True:
        print(f"--- Turn {world.turn} ---")

        # --- Agent A ---
        old_pos_a = list(world.agent_positions["A"])
        event_a = run_explorer_turn(world, "A", run_id, rng)
        new_pos_a = world.agent_positions["A"]
        success_a = "cannot" not in event_a["action"]["result"] and not event_a["action"]["anomaly"]
        stuck_tracker.record("A", old_pos_a, new_pos_a, success_a)
        _track_door_block(world, stuck_tracker, "A", event_a)
        if "_trace_url" in event_a:
            trace_urls.append(event_a["_trace_url"])
        print(f"  A: {event_a['action']['tool']}({event_a['action']['args']}) → {event_a['action']['result'][:60]}")

        # DM reactive — fires if A sent it a message
        dm_event = maybe_run_dm(world, run_id)
        if dm_event:
            if "_trace_url" in dm_event:
                trace_urls.append(dm_event["_trace_url"])
            print(f"  DM → {dm_event['action']['args'].get('to','?')}: {dm_event['action']['args'].get('content','')[:60]}")

        done, outcome, reason = check_termination(world, stuck_tracker, turn_limit)
        if done:
            break

        # --- Agent B ---
        old_pos_b = list(world.agent_positions["B"])
        event_b = run_explorer_turn(world, "B", run_id, rng)
        new_pos_b = world.agent_positions["B"]
        success_b = "cannot" not in event_b["action"]["result"] and not event_b["action"]["anomaly"]
        stuck_tracker.record("B", old_pos_b, new_pos_b, success_b)
        _track_door_block(world, stuck_tracker, "B", event_b)
        if "_trace_url" in event_b:
            trace_urls.append(event_b["_trace_url"])
        print(f"  B: {event_b['action']['tool']}({event_b['action']['args']}) → {event_b['action']['result'][:60]}")

        # DM reactive — fires if B sent it a message
        dm_event = maybe_run_dm(world, run_id)
        if dm_event:
            if "_trace_url" in dm_event:
                trace_urls.append(dm_event["_trace_url"])
            print(f"  DM → {dm_event['action']['args'].get('to','?')}: {dm_event['action']['args'].get('content','')[:60]}")

        done, outcome, reason = check_termination(world, stuck_tracker, turn_limit)
        if done:
            break

        # Snapshot grid for DM stale view
        world.history.append(snapshot_grid(world.grid))
        world.turn += 1

    print(f"\n=== GAME OVER: {outcome} ===")
    print(f"Reason: {reason}")
    print(f"Turns played: {world.turn}")

    # Write outputs
    write_summary(
        run_id=run_id,
        outcome=outcome,
        reason=reason,
        total_turns=world.turn,
        final_positions=dict(world.agent_positions),
        seed=seed,
        langfuse_trace_url=trace_urls[0] if trace_urls else None,
    )
    export_traces(run_id, trace_urls)
    flush()

    if trace_urls:
        print(f"\nLangfuse trace: {trace_urls[0]}")
    print(f"Events: runs/{run_id}/events.jsonl")
    print(f"Summary: runs/{run_id}/summary.json")

    return {"run_id": run_id, "outcome": outcome, "reason": reason, "turns": world.turn}


def _track_door_block(world: WorldState, tracker: StuckTracker, agent_id: str, event: dict):
    """Update door-blocked counter: True if agent tried to move into locked door."""
    tool = event["action"]["tool"]
    result = event["action"]["result"]
    blocked_at_door = tool == "move" and "locked door" in result
    tracker.record_door_blocked(agent_id, blocked_at_door)


def main():
    parser = argparse.ArgumentParser(description="Run dungeon agents simulation")
    parser.add_argument("--seed", type=int, default=None, help="Random seed for reproducibility")
    parser.add_argument("--turn-limit", type=int, default=50, help="Max turns before failure")
    parser.add_argument("--size", type=int, default=8, help="Grid size (NxN)")
    args = parser.parse_args()

    seed = args.seed if args.seed is not None else random.randint(0, 99999)
    run_game(seed=seed, turn_limit=args.turn_limit, size=args.size)


if __name__ == "__main__":
    main()
