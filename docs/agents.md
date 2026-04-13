# Agent Design Reference

This file defines the three agents in the simulation. Use it to implement `agents.py`.

---

## Agent A and Agent B (Explorer Agents)

**Role**: Navigate the dungeon, find the key, unlock the door, reach the exit together.

**What they know per turn** (built by `get_explorer_state()`):
```json
{
  "position": [row, col],
  "inventory": ["key"],
  "visible_cells": {
    "current": "EMPTY",
    "north": "WALL",
    "south": "KEY",
    "east": "EMPTY",
    "west": "LOCKED_DOOR"
  },
  "pending_messages": [
    {"from": "B", "content": "I see the key to the south"}
  ]
}
```

**System prompt** (keep under 100 words):
```
You are an explorer in a dungeon on an 8x8 grid. You can only see the cell you are on and
the 4 adjacent cells. Your goal: find the key, unlock the locked door, and reach the exit.
One of you must carry the key to unlock the door. Coordinate with the other agent by sending
messages. You must use exactly one tool per turn.
```

**API call parameters**:
- `model`: `claude-haiku-4-5-20251001`
- `tools`: all 5 explorer tools (see `docs/skills.md`)
- `tool_choice`: `{"type": "any"}` — forces exactly one tool call per turn
- User message: the observable state as a JSON block

**Context building**:
- Deliver all messages where `deliver_on_turn <= current_turn` before building state
- Include delivered messages in `pending_messages` field of state
- Remove delivered messages from queue after delivery

---

## Dungeon Master Agent (DM)

**Role**: Answer questions from explorer agents. Has full board visibility but sees it N=3 turns stale. Cannot move or interact with items.

**What it knows per turn** (built by `get_dm_state()`):
```json
{
  "stale_turn": 1,
  "current_turn": 4,
  "stale_grid": [
    ["EMPTY", "WALL", "KEY", ...],
    ...
  ],
  "pending_messages": [
    {"from": "A", "content": "Where is the key?"}
  ]
}
```

- `stale_grid` is `world.history[-3]` (the grid snapshot from 3 turns ago). If fewer than 3 turns have elapsed, use the earliest available snapshot.
- `stale_turn` tells the DM (and is logged) how old its view is.

**System prompt** (keep under 100 words):
```
You are the Dungeon Master. You can see the full dungeon grid, but only as it was 3 turns ago.
You cannot move or pick up items. Agents may ask you questions. Answer only what you can observe
from your stale view. Be explicit that your information may be outdated. Use respond_to_agent
to reply.
```

**API call parameters**:
- `model`: `claude-haiku-4-5-20251001`
- `tools`: `[respond_to_agent]` only (see `docs/skills.md`)
- `tool_choice`: `{"type": "any"}`

**When DM is called**:
- The DM does NOT take a dedicated turn. It is purely reactive.
- After each explorer agent's turn completes, check if DM has pending messages (`world.message_queues["DM"]` has entries with `deliver_on_turn <= world.turn`).
- If yes: deliver those messages to DM, call the LLM once with all pending messages in context, extract the `respond_to_agent` call, enqueue the response with `deliver_on_turn = world.turn + 1`.
- If no pending messages: skip entirely. No LLM call, no event logged.
- The DM sends one response per invocation (one tool call). If multiple messages arrived, the DM should address them all in a single response to the most recent sender (or the one who asked a question).

**Why the DM creates belief divergence**:
- If the key was at [3,4] on turn 1 and Agent A picks it up on turn 2, the DM still believes the key is at [3,4] when answering on turn 4.
- If Agent B asks "where is the key?" on turn 3, DM says [3,4]. Agent B goes there and finds nothing.
- This mismatch is captured in `world_truth.key_location` vs. what the DM told Agent B.

---

## Turn Order and Message Delivery

```
Turn 1: Agent A acts   → if A messaged DM, DM responds immediately after A's turn
Turn 1: Agent B acts   → if B messaged DM, DM responds immediately after B's turn
world.turn += 1
Turn 2: Agent A acts   → receives messages queued for turn 2 (from B or DM)
...
```

- Global `world.turn` increments once per full round (after both A and B have acted).
- Within a round, A goes first, then B. DM has no fixed turn — it fires reactively after whichever agent messaged it.
- `deliver_on_turn` uses the global turn counter: a message sent on turn N is delivered on turn N+1.
- If both A and B message DM in the same round, DM is called twice (once after A's turn, once after B's turn).

---

## Conversation History

Each agent maintains no persistent memory across turns. Every turn is a fresh API call. The agent's "memory" is only what is included in the current user message (observable state + delivered messages). Do not pass prior turn history to the API.
