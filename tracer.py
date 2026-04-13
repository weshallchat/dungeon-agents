"""
tracer.py — Structured event logging and Langfuse v3 integration.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from dotenv import load_dotenv
from langfuse import Langfuse

load_dotenv()

# ---------------------------------------------------------------------------
# Langfuse client (initialised once at import)
# ---------------------------------------------------------------------------

_langfuse = Langfuse(
    public_key=os.getenv("LANGFUSE_PUBLIC_KEY"),
    secret_key=os.getenv("LANGFUSE_SECRET_KEY"),
    host=os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com"),
)


def get_langfuse() -> Langfuse:
    return _langfuse


def flush():
    _langfuse.flush()


def get_trace_url() -> str | None:
    try:
        return _langfuse.get_trace_url()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Run directory helpers
# ---------------------------------------------------------------------------

def run_dir(run_id: str) -> Path:
    d = Path("runs") / run_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def append_event(run_id: str, event: dict) -> None:
    path = run_dir(run_id) / "events.jsonl"
    with open(path, "a") as f:
        f.write(json.dumps(event, default=str) + "\n")


def write_summary(
    run_id: str,
    outcome: str,
    reason: str,
    total_turns: int,
    final_positions: dict,
    seed: int | None,
    langfuse_trace_url: str | None = None,
) -> None:
    summary = {
        "run_id": run_id,
        "outcome": outcome,
        "reason": reason,
        "total_turns": total_turns,
        "final_positions": final_positions,
        "seed": seed,
        "langfuse_trace_url": langfuse_trace_url,
    }
    path = run_dir(run_id) / "summary.json"
    with open(path, "w") as f:
        json.dump(summary, f, indent=2, default=str)


def export_traces(run_id: str, trace_urls: list[str]) -> None:
    path = run_dir(run_id) / "langfuse_export.json"
    with open(path, "w") as f:
        json.dump({"run_id": run_id, "traces": trace_urls}, f, indent=2)


# ---------------------------------------------------------------------------
# Event builder
# ---------------------------------------------------------------------------

def build_event(
    run_id: str,
    turn: int,
    agent_id: str,
    event_type: str,
    agent_belief: dict,
    world_truth: dict,
    tool_name: str,
    tool_args: dict,
    tool_result: dict,
    anomaly: bool,
    anomaly_reason: str | None,
    prompt_tokens: int,
    completion_tokens: int,
    latency_ms: float,
    raw_response: str,
    game_state_summary: dict | None = None,
) -> dict:
    return {
        "run_id": run_id,
        "turn": turn,
        "agent_id": agent_id,
        "event_type": event_type,
        "timestamp_ms": int(time.time() * 1000),
        "game_state_summary": game_state_summary or {},
        "agent_belief": agent_belief,
        "world_truth": world_truth,
        "action": {
            "tool": tool_name,
            "args": tool_args,
            "result": tool_result,
            "anomaly": anomaly,
            "anomaly_reason": anomaly_reason,
        },
        "llm": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "latency_ms": round(latency_ms, 1),
            "raw_response": raw_response,
        },
    }
