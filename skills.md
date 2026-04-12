# Tool Schemas and Failure Rules

This file defines all tools available to agents. Use it to implement tool schemas in `agents.py` and tool execution in `dungeon.py`.

---

## Explorer Agent Tools (Agent A and Agent B)

Pass these as the `tools` parameter to `anthropic.messages.create()`.

### `move`
```json
{
  "name": "move",
  "description": "Move one cell in a cardinal direction. Fails if the target cell is a wall, out of bounds, or locked door.",
  "input_schema": {
    "type": "object",
    "properties": {
      "direction": {
        "type": "string",
        "enum": ["north", "south", "east", "west"],
        "description": "Direction to move"
      }
    },
    "required": ["direction"]
  }
}
```
**Execution**:
- Map direction to grid delta: north=(-1,0), south=(+1,0), east=(0,+1), west=(0,-1)
- Check new position is in bounds
- Check cell type is not WALL or LOCKED_DOOR
- Apply spurious block: `if rng.random() < 0.10` → return `"path blocked unexpectedly"`, `anomaly=True`, `anomaly_reason="spurious_block"` (do NOT update position)
- On success: update `world.agent_positions[agent_id]`
- Return: `"moved north to [2, 3]"` or `"cannot move north: wall"`

### `observe`
```json
{
  "name": "observe",
  "description": "Observe the current cell and all 4 adjacent cells.",
  "input_schema": {
    "type": "object",
    "properties": {},
    "required": []
  }
}
```
**Execution**:
- Return the 5-cell view as a formatted string (same as `visible_cells` in observable state)
- Never fails, never anomaly

### `pick_up`
```json
{
  "name": "pick_up",
  "description": "Pick up an item in your current cell.",
  "input_schema": {
    "type": "object",
    "properties": {
      "item": {
        "type": "string",
        "enum": ["key"],
        "description": "The item to pick up"
      }
    },
    "required": ["item"]
  }
}
```
**Execution**:
- Check `world.grid[pos]` contains the named item
- If yes: add to `world.inventories[agent_id]`, set cell to EMPTY
- If no: return `"no key here"`
- No anomaly on this tool

### `send_message`
```json
{
  "name": "send_message",
  "description": "Send a message to the other explorer or the Dungeon Master. Delivered on the next turn.",
  "input_schema": {
    "type": "object",
    "properties": {
      "to": {
        "type": "string",
        "enum": ["A", "B", "DM"],
        "description": "Recipient agent ID"
      },
      "content": {
        "type": "string",
        "description": "Message content"
      }
    },
    "required": ["to", "content"]
  }
}
```
**Execution**:
- Apply delay: `if rng.random() < 0.15` → `deliver_on_turn = world.turn + 2`, `anomaly=True`, `anomaly_reason="message_delayed"`. Otherwise `deliver_on_turn = world.turn + 1`.
- Enqueue: `world.message_queues[to].append({"from": agent_id, "content": content, "deliver_on_turn": deliver_on_turn})`
- Return: `"message sent to B"` or `"message sent to DM (delayed)"`

### `unlock_door`
```json
{
  "name": "unlock_door",
  "description": "Unlock the locked door. You must be standing on the door cell and holding the key.",
  "input_schema": {
    "type": "object",
    "properties": {},
    "required": []
  }
}
```
**Execution**:
- Check `world.agent_positions[agent_id]` is a LOCKED_DOOR cell
- Check `"key" in world.inventories[agent_id]`
- If both: set `world.door_state = "unlocked"`, set cell to OPEN_DOOR. Return `"door unlocked"`.
- If not on door: return `"you are not standing on the door"`
- If no key: return `"you do not have the key"`
- No anomaly on this tool

---

## DM Agent Tools

### `respond_to_agent`
```json
{
  "name": "respond_to_agent",
  "description": "Send a response to an explorer agent.",
  "input_schema": {
    "type": "object",
    "properties": {
      "to": {
        "type": "string",
        "enum": ["A", "B"],
        "description": "The explorer agent to respond to"
      },
      "content": {
        "type": "string",
        "description": "The DM's response"
      }
    },
    "required": ["to", "content"]
  }
}
```
**Execution**:
- Enqueue in recipient's message queue with `deliver_on_turn = world.turn + 1`
- No anomaly, no failure mode on this tool
- Return: `"response sent to A"`

---

## Failure Injection Rules

| Tool | Failure type | Probability | Condition | anomaly_reason |
|---|---|---|---|---|
| `move` | Spurious block | 10% | Checked after validating move is legal | `spurious_block` |
| `send_message` | Extra delay | 15% | Always (even valid messages) | `message_delayed` |

**RNG seeding**: Initialize one `random.Random(seed)` object in `run_game()`. Pass it to every `execute_tool()` call. This ensures runs are fully reproducible from the seed.

**Logging requirement**: Every anomaly must appear in the event with `action.anomaly = true` and `action.anomaly_reason` set. Do not log anomalies as errors or exceptions — they are expected, instrumented behaviors.

---

## Tool Call Format (Anthropic SDK)

The Anthropic API returns tool calls in `response.content`. Extract them as:

```python
for block in response.content:
    if block.type == "tool_use":
        tool_name = block.name
        tool_args = block.input  # dict
        break

# Raw text (may be empty string if model only returns tool use)
raw_text = " ".join(
    block.text for block in response.content if block.type == "text"
)
```

Always capture `raw_text` even if empty — log it as `llm.raw_response` in the event.
