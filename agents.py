"""
agents.py — LLM agent turns for explorer agents (A, B) and the reactive DM.
Uses Langfuse v3 context-manager API for tracing.
"""

from __future__ import annotations

import json
import os
import time

import anthropic
from dotenv import load_dotenv

from dungeon import (
    Cell,
    WorldState,
    build_game_state_summary,
    compute_milestone_label,
    compute_progress_score,
    deliver_messages,
    execute_tool,
    find_cell,
    get_dm_state,
    get_explorer_state,
    get_world_truth,
    snapshot_grid,
)
from tracer import (
    append_event,
    build_event,
    get_langfuse,
    get_trace_url,
)

load_dotenv()

_client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
MODEL = "claude-haiku-4-5-20251001"

# ---------------------------------------------------------------------------
# Tool schemas
# ---------------------------------------------------------------------------

EXPLORER_TOOLS = [
    {
        "name": "move",
        "description": "Move one cell in a cardinal direction. Fails if target is a wall, out of bounds, or locked door.",
        "input_schema": {
            "type": "object",
            "properties": {
                "direction": {
                    "type": "string",
                    "enum": ["north", "south", "east", "west"],
                    "description": "Direction to move",
                }
            },
            "required": ["direction"],
        },
    },
    {
        "name": "observe",
        "description": "Observe your current cell and all 4 adjacent cells.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "pick_up",
        "description": "Pick up an item in your current cell.",
        "input_schema": {
            "type": "object",
            "properties": {
                "item": {
                    "type": "string",
                    "enum": ["key"],
                    "description": "The item to pick up",
                }
            },
            "required": ["item"],
        },
    },
    {
        "name": "send_message",
        "description": "Send a message to the other explorer (A or B) or the Dungeon Master (DM). Delivered on the next turn.",
        "input_schema": {
            "type": "object",
            "properties": {
                "to": {
                    "type": "string",
                    "enum": ["A", "B", "DM"],
                    "description": "Recipient agent ID",
                },
                "content": {"type": "string", "description": "Message content"},
            },
            "required": ["to", "content"],
        },
    },
    {
        "name": "unlock_door",
        "description": "Unlock the locked door. You must be standing on the door cell and holding the key.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
]

DM_TOOLS = [
    {
        "name": "respond_to_agent",
        "description": "Send a response to an explorer agent.",
        "input_schema": {
            "type": "object",
            "properties": {
                "to": {
                    "type": "string",
                    "enum": ["A", "B"],
                    "description": "The explorer agent to respond to",
                },
                "content": {"type": "string", "description": "Your response"},
            },
            "required": ["to", "content"],
        },
    }
]

EXPLORER_SYSTEM = (
    "You are an explorer in an 8x8 dungeon grid. You can only see your current cell "
    "and the 4 adjacent cells. Goal: find the key, unlock the locked door, and both "
    "explorers must reach the EXIT. One of you must carry the key to unlock the door. "
    "Coordinate by sending messages. You must use exactly one tool per turn. "
    "Move every turn to explore — do not observe from the same position repeatedly. "
    "The Dungeon Master (DM) has a full map of the dungeon (may be 3 turns outdated). "
    "Send a message to DM asking for the key or exit location if you cannot find them after a few turns. "
    "Before calling a tool, write one sentence explaining your reasoning: what you observe and why you chose this action."
)

DM_SYSTEM = (
    "You are the Dungeon Master. You see the full dungeon grid, but only as it was "
    "3 turns ago — your information may be stale. You cannot move or pick up items. "
    "Answer agent questions based on your stale view. Always mention that your info "
    "may be outdated. Use respond_to_agent to reply. One tool call per turn."
)


# ---------------------------------------------------------------------------
# Explorer agent turn
# ---------------------------------------------------------------------------

def run_explorer_turn(
    world: WorldState,
    agent_id: str,
    run_id: str,
    rng,
) -> dict:
    lf = get_langfuse()

    # 1. Deliver pending messages
    delivered = deliver_messages(world, agent_id)

    # 2. Build observable state (belief) and ground truth
    belief = get_explorer_state(world, agent_id, delivered)
    truth = get_world_truth(world, agent_id)

    # 3. Build messages for LLM
    user_content = f"Turn {world.turn}. Your state:\n" + json.dumps(belief, indent=2, default=str)
    messages = [{"role": "user", "content": user_content}]
    full_prompt = [{"role": "system", "content": EXPLORER_SYSTEM}] + messages

    trace_url = None

    with lf.start_as_current_span(
        name="explorer_turn",
        input={"turn": world.turn, "agent_id": agent_id, "belief": belief},
        metadata={"run_id": run_id, "turn": world.turn, "agent_id": agent_id, "session_id": run_id},
    ):
        # 4. LLM call inside a generation span (with retry on transient errors)
        t_start = time.time()
        with lf.start_as_current_generation(
            name="llm_call",
            model=MODEL,
            input=full_prompt,
        ) as gen:
            for _attempt in range(3):
                try:
                    response = _client.messages.create(
                        model=MODEL,
                        max_tokens=512,
                        system=EXPLORER_SYSTEM,
                        tools=EXPLORER_TOOLS,
                        tool_choice={"type": "any"},
                        messages=messages,
                    )
                    break
                except Exception:
                    if _attempt < 2:
                        time.sleep(10 * (_attempt + 1))
                    else:
                        raise
            latency_ms = (time.time() - t_start) * 1000
            tool_name, tool_args, raw_response = _extract_tool_call(response)
            gen.update(
                output=raw_response or f"[tool: {tool_name}]",
                usage={"input": response.usage.input_tokens, "output": response.usage.output_tokens},
                metadata={"latency_ms": round(latency_ms, 1)},
            )

        # 5. Tool execution inside a span
        with lf.start_as_current_span(
            name=f"tool:{tool_name}",
            input=tool_args,
        ) as tool_span:
            tool_result, anomaly, anomaly_reason = execute_tool(world, agent_id, tool_name, tool_args, rng)
            tool_span.update(
                output={"result": tool_result},
                metadata={"anomaly": anomaly, "anomaly_reason": anomaly_reason},
            )

        # 6. Attach progress score to the current trace
        progress_score = compute_progress_score(world)
        milestone = compute_milestone_label(progress_score)
        try:
            lf.score_current_trace(
                name="progress",
                value=progress_score,
                comment=milestone,
            )
        except Exception:
            pass

        trace_url = get_trace_url()

    # 7. Determine event type
    event_type = "anomaly" if anomaly else "tool_call"

    # 8. Build and log event
    game_state_summary = build_game_state_summary(world)
    event = build_event(
        run_id=run_id,
        turn=world.turn,
        agent_id=agent_id,
        event_type=event_type,
        agent_belief=belief,
        world_truth=truth,
        tool_name=tool_name,
        tool_args=tool_args,
        tool_result=tool_result,
        anomaly=anomaly,
        anomaly_reason=anomaly_reason,
        prompt_tokens=response.usage.input_tokens,
        completion_tokens=response.usage.output_tokens,
        latency_ms=latency_ms,
        raw_response=raw_response,
        game_state_summary=game_state_summary,
    )
    append_event(run_id, event)
    event["_trace_url"] = trace_url
    return event


# ---------------------------------------------------------------------------
# DM reactive handler
# ---------------------------------------------------------------------------

def maybe_run_dm(world: WorldState, run_id: str) -> dict | None:
    """Call DM only if it has pending messages. Returns event or None."""
    due = deliver_messages(world, "DM")
    if not due:
        return None

    lf = get_langfuse()

    # Build DM stale state
    dm_state = get_dm_state(world)
    incoming = "\n".join(f"  From {m.from_agent}: {m.content}" for m in due)
    user_content = (
        f"Turn {world.turn}. Incoming messages:\n{incoming}\n\n"
        f"Your stale board view (as of turn {dm_state['stale_turn']}):\n"
        + json.dumps(dm_state["stale_grid"], indent=2)
    )
    messages = [{"role": "user", "content": user_content}]
    full_prompt = [{"role": "system", "content": DM_SYSTEM}] + messages

    trace_url = None

    with lf.start_as_current_span(
        name="dm_response",
        input={"turn": world.turn, "messages": [{"from": m.from_agent, "content": m.content} for m in due]},
        metadata={"run_id": run_id, "turn": world.turn, "agent_id": "DM", "session_id": run_id},
    ):
        t_start = time.time()
        with lf.start_as_current_generation(
            name="llm_call",
            model=MODEL,
            input=full_prompt,
        ) as gen:
            for _attempt in range(3):
                try:
                    response = _client.messages.create(
                        model=MODEL,
                        max_tokens=512,
                        system=DM_SYSTEM,
                        tools=DM_TOOLS,
                        tool_choice={"type": "any"},
                        messages=messages,
                    )
                    break
                except Exception:
                    if _attempt < 2:
                        time.sleep(10 * (_attempt + 1))
                    else:
                        raise
            latency_ms = (time.time() - t_start) * 1000
            tool_name, tool_args, raw_response = _extract_tool_call(response)
            gen.update(
                output=raw_response or f"[tool: {tool_name}]",
                usage={"input": response.usage.input_tokens, "output": response.usage.output_tokens},
                metadata={"latency_ms": round(latency_ms, 1)},
            )

        with lf.start_as_current_span(name=f"tool:{tool_name}", input=tool_args) as tool_span:
            tool_result, _, _ = execute_tool(world, "DM", tool_name, tool_args, rng=None)
            tool_span.update(output={"result": tool_result})

        trace_url = get_trace_url()

    # Build event — belief is DM's stale view, truth is current full grid
    belief = {
        "stale_turn": dm_state["stale_turn"],
        "current_turn": dm_state["current_turn"],
        "stale_grid": dm_state["stale_grid"],
        "pending_messages": [{"from": m.from_agent, "content": m.content} for m in due],
    }
    truth = {
        "actual_grid_snapshot": snapshot_grid(world.grid),
        "door_state": world.door_state,
        "key_location": find_cell(world.grid, Cell.KEY),
        "agent_positions": world.agent_positions,
    }

    game_state_summary = build_game_state_summary(world)
    event = build_event(
        run_id=run_id,
        turn=world.turn,
        agent_id="DM",
        event_type="dm_response",
        agent_belief=belief,
        world_truth=truth,
        tool_name=tool_name,
        tool_args=tool_args,
        tool_result=tool_result,
        anomaly=False,
        anomaly_reason=None,
        prompt_tokens=response.usage.input_tokens,
        completion_tokens=response.usage.output_tokens,
        latency_ms=latency_ms,
        raw_response=raw_response,
        game_state_summary=game_state_summary,
    )
    append_event(run_id, event)
    event["_trace_url"] = trace_url
    return event


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_tool_call(response) -> tuple[str, dict, str]:
    """Return (tool_name, tool_args, raw_text) from an Anthropic response."""
    tool_name = "unknown"
    tool_args: dict = {}
    raw_text = ""

    for block in response.content:
        if block.type == "tool_use":
            tool_name = block.name
            tool_args = block.input or {}
        elif block.type == "text":
            raw_text += block.text

    return tool_name, tool_args, raw_text
