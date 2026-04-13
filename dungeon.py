"""
dungeon.py — World state, grid generation, observable state builders, tool execution.
All tool results are structured dicts for clean aggregation across runs.
"""

from __future__ import annotations

import copy
import random
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ---------------------------------------------------------------------------
# Cell types
# ---------------------------------------------------------------------------

class Cell(str, Enum):
    EMPTY       = "EMPTY"
    WALL        = "WALL"
    KEY         = "KEY"
    LOCKED_DOOR = "LOCKED_DOOR"
    OPEN_DOOR   = "OPEN_DOOR"
    EXIT        = "EXIT"


DIRECTION_DELTAS = {
    "north": (-1,  0),
    "south": ( 1,  0),
    "east":  ( 0,  1),
    "west":  ( 0, -1),
}

PASSABLE = {Cell.EMPTY, Cell.OPEN_DOOR, Cell.EXIT, Cell.KEY}


# ---------------------------------------------------------------------------
# Grid generation
# ---------------------------------------------------------------------------

def generate_grid(size: int = 8, seed: int | None = None) -> list[list[Cell]]:
    rng = random.Random(seed)
    while True:
        grid = _attempt_grid(size, rng)
        if grid is not None:
            return grid


def _attempt_grid(size: int, rng: random.Random) -> list[list[Cell]] | None:
    grid = [[Cell.EMPTY] * size for _ in range(size)]

    for r in range(size):
        for c in range(size):
            if rng.random() < 0.15:
                grid[r][c] = Cell.WALL

    empty_cells = [(r, c) for r in range(size) for c in range(size) if grid[r][c] == Cell.EMPTY]
    if len(empty_cells) < 6:
        return None

    rng.shuffle(empty_cells)
    key_pos  = empty_cells[0]
    door_pos = empty_cells[1]
    exit_pos = empty_cells[2]

    if _adjacent(exit_pos, door_pos):
        return None

    grid[key_pos[0]][key_pos[1]]   = Cell.KEY
    grid[door_pos[0]][door_pos[1]] = Cell.LOCKED_DOOR
    grid[exit_pos[0]][exit_pos[1]] = Cell.EXIT

    if not _is_connected(grid, size):
        return None

    return grid


def _adjacent(a: tuple, b: tuple) -> bool:
    return abs(a[0] - b[0]) + abs(a[1] - b[1]) == 1


def _is_connected(grid: list[list[Cell]], size: int) -> bool:
    non_wall = [(r, c) for r in range(size) for c in range(size) if grid[r][c] != Cell.WALL]
    if not non_wall:
        return False
    visited = set()
    queue = deque([non_wall[0]])
    visited.add(non_wall[0])
    while queue:
        r, c = queue.popleft()
        for dr, dc in DIRECTION_DELTAS.values():
            nr, nc = r + dr, c + dc
            if 0 <= nr < size and 0 <= nc < size and (nr, nc) not in visited:
                if grid[nr][nc] != Cell.WALL:
                    visited.add((nr, nc))
                    queue.append((nr, nc))
    return len(visited) == len(non_wall)


def find_cell(grid: list[list[Cell]], cell_type: Cell) -> tuple[int, int] | None:
    for r, row in enumerate(grid):
        for c, cell in enumerate(row):
            if cell == cell_type:
                return (r, c)
    return None


def snapshot_grid(grid: list[list[Cell]]) -> list[list[str]]:
    return [[cell.value for cell in row] for row in grid]


# ---------------------------------------------------------------------------
# World State
# ---------------------------------------------------------------------------

@dataclass
class Message:
    from_agent: str
    content: str
    deliver_on_turn: int


@dataclass
class WorldState:
    grid: list[list[Cell]]
    size: int
    seed: int | None
    agent_positions: dict[str, list[int]]
    inventories: dict[str, list[str]]
    door_state: str
    message_queues: dict[str, list[Message]]
    turn: int
    history: list[list[list[str]]]


def build_world(size: int = 8, seed: int | None = None) -> WorldState:
    grid = generate_grid(size, seed)
    agent_positions = _place_agents(grid, seed)
    return WorldState(
        grid=grid,
        size=size,
        seed=seed,
        agent_positions=agent_positions,
        inventories={"A": [], "B": []},
        door_state="locked",
        message_queues={"A": [], "B": [], "DM": []},
        turn=1,
        history=[snapshot_grid(grid)],
    )


def _place_agents(grid: list[list[Cell]], seed: int | None) -> dict[str, list[int]]:
    rng = random.Random(seed)
    candidates = [
        (r, c)
        for r, row in enumerate(grid)
        for c, cell in enumerate(row)
        if cell == Cell.EMPTY
    ]
    rng.shuffle(candidates)
    return {"A": list(candidates[0]), "B": list(candidates[1])}


# ---------------------------------------------------------------------------
# Observable state builders
# ---------------------------------------------------------------------------

def _get_adjacent_cells(world: WorldState, pos: list[int]) -> dict[str, str]:
    r, c = pos
    result = {"current": world.grid[r][c].value}
    for direction, (dr, dc) in DIRECTION_DELTAS.items():
        nr, nc = r + dr, c + dc
        if 0 <= nr < world.size and 0 <= nc < world.size:
            result[direction] = world.grid[nr][nc].value
        else:
            result[direction] = Cell.WALL.value
    return result


def get_explorer_state(world: WorldState, agent_id: str, delivered_messages: list[Message]) -> dict:
    pos = world.agent_positions[agent_id]
    return {
        "position": pos,
        "inventory": world.inventories[agent_id],
        "visible_cells": _get_adjacent_cells(world, pos),
        "pending_messages": [
            {"from": m.from_agent, "content": m.content}
            for m in delivered_messages
        ],
    }


def get_dm_state(world: WorldState) -> dict:
    stale_idx = max(0, len(world.history) - 3 - 1)
    stale_grid = world.history[stale_idx]
    stale_turn = max(1, world.turn - 3)
    return {
        "stale_turn": stale_turn,
        "current_turn": world.turn,
        "stale_grid": stale_grid,
    }


def get_world_truth(world: WorldState, agent_id: str) -> dict:
    pos = world.agent_positions[agent_id]
    return {
        "actual_position": pos,
        "adjacent_cell_contents": _get_adjacent_cells(world, pos),
        "door_state": world.door_state,
        "key_location": find_cell(world.grid, Cell.KEY),
    }


# ---------------------------------------------------------------------------
# Progress score + game state summary
# ---------------------------------------------------------------------------

def compute_progress_score(world: WorldState) -> float:
    exit_pos = find_cell(world.grid, Cell.EXIT)
    pos_a = tuple(world.agent_positions["A"])
    pos_b = tuple(world.agent_positions["B"])

    if exit_pos and pos_a == tuple(exit_pos) and pos_b == tuple(exit_pos):
        return 1.0
    if world.door_state == "unlocked":
        return 0.66
    if any("key" in world.inventories[a] for a in ["A", "B"]):
        return 0.33
    return 0.0


def compute_milestone_label(score: float) -> str:
    if score >= 1.0:
        return "complete"
    if score >= 0.66:
        return "door_unlocked"
    if score >= 0.33:
        return "key_acquired"
    return "start"


def build_game_state_summary(world: WorldState) -> dict:
    key_holder = next(
        (a for a in ["A", "B"] if "key" in world.inventories[a]), None
    )
    exit_pos = find_cell(world.grid, Cell.EXIT)
    pos_a = tuple(world.agent_positions["A"])
    pos_b = tuple(world.agent_positions["B"])
    both_at_exit = bool(
        exit_pos and pos_a == tuple(exit_pos) and pos_b == tuple(exit_pos)
    )
    score = compute_progress_score(world)
    return {
        "turn": world.turn,
        "key_held_by": key_holder,
        "door_state": world.door_state,
        "agent_positions": {k: list(v) for k, v in world.agent_positions.items()},
        "both_at_exit": both_at_exit,
        "progress_score": score,
        "milestone": compute_milestone_label(score),
    }


# ---------------------------------------------------------------------------
# Message delivery
# ---------------------------------------------------------------------------

def deliver_messages(world: WorldState, agent_id: str) -> list[Message]:
    due = [m for m in world.message_queues[agent_id] if m.deliver_on_turn <= world.turn]
    world.message_queues[agent_id] = [
        m for m in world.message_queues[agent_id] if m.deliver_on_turn > world.turn
    ]
    return due


# ---------------------------------------------------------------------------
# Tool execution — all results are structured dicts
# ---------------------------------------------------------------------------

def execute_tool(
    world: WorldState,
    agent_id: str,
    tool_name: str,
    tool_args: dict,
    rng: random.Random | None,
) -> tuple[dict, bool, str | None]:
    """
    Execute a tool call.
    Returns (result_dict, anomaly, anomaly_reason).
    result_dict is always a structured dict — never a raw string.
    """
    if tool_name == "move":
        return _tool_move(world, agent_id, tool_args, rng)
    elif tool_name == "observe":
        return _tool_observe(world, agent_id)
    elif tool_name == "pick_up":
        return _tool_pick_up(world, agent_id, tool_args)
    elif tool_name == "send_message":
        return _tool_send_message(world, agent_id, tool_args, rng)
    elif tool_name == "unlock_door":
        return _tool_unlock_door(world, agent_id)
    elif tool_name == "respond_to_agent":
        return _tool_respond_to_agent(world, agent_id, tool_args)
    else:
        return {"status": "failed", "reason": f"unknown_tool:{tool_name}"}, False, None


def _tool_move(world: WorldState, agent_id: str, args: dict, rng: random.Random | None):
    direction = args.get("direction", "")
    if direction not in DIRECTION_DELTAS:
        return {"status": "failed", "new_position": None, "reason": "invalid_direction"}, False, None

    dr, dc = DIRECTION_DELTAS[direction]
    r, c = world.agent_positions[agent_id]
    nr, nc = r + dr, c + dc

    if not (0 <= nr < world.size and 0 <= nc < world.size):
        return {"status": "failed", "new_position": None, "reason": "out_of_bounds"}, False, None

    target_cell = world.grid[nr][nc]
    if target_cell == Cell.WALL:
        return {"status": "failed", "new_position": None, "reason": "wall"}, False, None
    if target_cell == Cell.LOCKED_DOOR:
        return {"status": "failed", "new_position": None, "reason": "locked_door"}, False, None

    if rng and rng.random() < 0.10:
        return {"status": "anomaly", "new_position": None, "reason": "spurious_block"}, True, "spurious_block"

    world.agent_positions[agent_id] = [nr, nc]
    return {"status": "success", "new_position": [nr, nc], "reason": None}, False, None


def _tool_observe(world: WorldState, agent_id: str):
    cells = _get_adjacent_cells(world, world.agent_positions[agent_id])
    return {"status": "success", "cells": cells}, False, None


def _tool_pick_up(world: WorldState, agent_id: str, args: dict):
    item = args.get("item", "")
    r, c = world.agent_positions[agent_id]
    cell = world.grid[r][c]

    if item == "key" and cell == Cell.KEY:
        world.grid[r][c] = Cell.EMPTY
        world.inventories[agent_id].append("key")
        return {"status": "success", "item": "key", "reason": None}, False, None
    return {"status": "failed", "item": item, "reason": "no_item_here"}, False, None


def _tool_send_message(world: WorldState, agent_id: str, args: dict, rng: random.Random | None):
    to = args.get("to", "")
    content = args.get("content", "")

    if to not in ("A", "B", "DM"):
        return {"status": "failed", "to": to, "deliver_on_turn": None, "reason": "invalid_recipient"}, False, None
    if to == agent_id:
        return {"status": "failed", "to": to, "deliver_on_turn": None, "reason": "cannot_message_self"}, False, None

    anomaly = False
    anomaly_reason = None
    deliver_on = world.turn + 1
    if rng and rng.random() < 0.15:
        deliver_on = world.turn + 2
        anomaly = True
        anomaly_reason = "message_delayed"

    world.message_queues[to].append(Message(
        from_agent=agent_id,
        content=content,
        deliver_on_turn=deliver_on,
    ))
    status = "delayed" if anomaly else "sent"
    return {"status": status, "to": to, "deliver_on_turn": deliver_on}, anomaly, anomaly_reason


def _tool_unlock_door(world: WorldState, agent_id: str):
    r, c = world.agent_positions[agent_id]
    if world.grid[r][c] != Cell.LOCKED_DOOR:
        return {"status": "failed", "reason": "not_on_door"}, False, None
    if "key" not in world.inventories[agent_id]:
        return {"status": "failed", "reason": "no_key"}, False, None

    world.grid[r][c] = Cell.OPEN_DOOR
    world.door_state = "unlocked"
    return {"status": "success", "reason": None}, False, None


def _tool_respond_to_agent(world: WorldState, _agent_id: str, args: dict):
    to = args.get("to", "")
    content = args.get("content", "")
    if to not in ("A", "B"):
        return {"status": "failed", "to": to, "deliver_on_turn": None, "reason": "invalid_recipient"}, False, None

    deliver_on = world.turn + 1
    world.message_queues[to].append(Message(
        from_agent="DM",
        content=content,
        deliver_on_turn=deliver_on,
    ))
    return {"status": "sent", "to": to, "deliver_on_turn": deliver_on}, False, None


# ---------------------------------------------------------------------------
# Termination / stuck tracking
# ---------------------------------------------------------------------------

@dataclass
class StuckTracker:
    position_history: dict[str, list] = field(default_factory=lambda: {"A": [], "B": []})
    action_success: dict[str, list] = field(default_factory=lambda: {"A": [], "B": []})
    door_blocked_turns: dict[str, int] = field(default_factory=lambda: {"A": 0, "B": 0})

    WINDOW = 3

    def record(self, agent_id: str, old_pos: list[int], new_pos: list[int], success: bool):
        self.position_history[agent_id].append(tuple(old_pos))
        self.action_success[agent_id].append(success)
        if len(self.position_history[agent_id]) > self.WINDOW:
            self.position_history[agent_id].pop(0)
        if len(self.action_success[agent_id]) > self.WINDOW:
            self.action_success[agent_id].pop(0)

    def record_door_blocked(self, agent_id: str, blocked: bool):
        if blocked:
            self.door_blocked_turns[agent_id] += 1
        else:
            self.door_blocked_turns[agent_id] = 0

    def is_stuck(self, agent_id: str) -> bool:
        hist = self.position_history[agent_id]
        succ = self.action_success[agent_id]
        if len(hist) < self.WINDOW:
            return False
        return len(set(hist)) == 1 and not any(succ)


def check_termination(
    world: WorldState,
    stuck_tracker: StuckTracker,
    turn_limit: int = 50,
) -> tuple[bool, str, str]:
    pos_a = tuple(world.agent_positions["A"])
    pos_b = tuple(world.agent_positions["B"])
    exit_pos = find_cell(world.grid, Cell.EXIT)

    if exit_pos and pos_a == tuple(exit_pos) and pos_b == tuple(exit_pos):
        return True, "success", "both agents reached the exit"

    if world.turn > turn_limit:
        return True, "turn_limit", f"turn limit of {turn_limit} reached"

    if stuck_tracker.is_stuck("A") and stuck_tracker.is_stuck("B"):
        return True, "stuck", "both agents stuck for 3+ consecutive turns"

    for holder, other in [("A", "B"), ("B", "A")]:
        if "key" in world.inventories[holder]:
            if stuck_tracker.is_stuck(holder):
                if stuck_tracker.door_blocked_turns[other] >= stuck_tracker.WINDOW:
                    return True, "key_blocked", (
                        f"agent {holder} holds key but is stuck; "
                        f"agent {other} blocked at door for {stuck_tracker.WINDOW}+ turns"
                    )

    return False, "", ""
