"""
analyze.py — Post-processing script.

Reads all runs in runs/, computes per-run and aggregate stats,
generates LLM incident reports, selects top 10 runs, writes analysis.json.

Usage: python analyze.py
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import anthropic
from dotenv import load_dotenv

load_dotenv()

_client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
MODEL = "claude-haiku-4-5-20251001"

RUNS_DIR = Path("runs")
ANALYSIS_PATH = Path("analysis.json")

# Event types tracked for "distinct_event_types" ranking
TRACKED_EVENT_TYPES = {
    "spurious_block",
    "message_delayed",
    "key_pickup",
    "door_encounter",
    "door_unlocked",
    "message_sent",
    "dm_response",
}


# ---------------------------------------------------------------------------
# Per-run stats
# ---------------------------------------------------------------------------

def compute_run_stats(run_path: Path) -> dict | None:
    summary_path = run_path / "summary.json"
    events_path = run_path / "events.jsonl"

    if not summary_path.exists():
        return None

    with open(summary_path) as f:
        summary = json.load(f)

    events: list[dict] = []
    if events_path.exists():
        with open(events_path) as f:
            for line in f:
                line = line.strip()
                if line:
                    events.append(json.loads(line))

    anomaly_count = 0
    distinct_event_types: set[str] = set()
    key_pickup_turn: int | None = None
    door_encounter_turn: int | None = None
    door_unlocked_turn: int | None = None
    messages_sent = 0
    dm_interactions = 0
    progress_score_final = 0.0

    for ev in events:
        action = ev.get("action", {})
        tool = action.get("tool", "")
        raw_result = action.get("result", {})
        # Support both structured dicts and legacy string results
        if isinstance(raw_result, dict):
            result = raw_result
        else:
            result = {}
        anomaly = action.get("anomaly", False)
        anomaly_reason = action.get("anomaly_reason")
        turn = ev.get("turn", 0)
        event_type = ev.get("event_type", "")

        # Anomalies
        if anomaly:
            anomaly_count += 1
            if anomaly_reason == "spurious_block":
                distinct_event_types.add("spurious_block")
            elif anomaly_reason == "message_delayed":
                distinct_event_types.add("message_delayed")

        # Key pickup
        if tool == "pick_up" and result.get("status") == "success" and result.get("item") == "key":
            if key_pickup_turn is None:
                key_pickup_turn = turn
            distinct_event_types.add("key_pickup")

        # Door encounter (move blocked by locked door)
        if tool == "move" and result.get("reason") == "locked_door":
            if door_encounter_turn is None:
                door_encounter_turn = turn
            distinct_event_types.add("door_encounter")

        # Door unlocked
        if tool == "unlock_door" and result.get("status") == "success":
            if door_unlocked_turn is None:
                door_unlocked_turn = turn
            distinct_event_types.add("door_unlocked")

        # Messages sent
        if tool == "send_message":
            messages_sent += 1
            distinct_event_types.add("message_sent")

        # DM interactions
        if event_type == "dm_response":
            dm_interactions += 1
            distinct_event_types.add("dm_response")

        # Track latest progress score
        gss = ev.get("game_state_summary", {})
        if gss.get("progress_score") is not None:
            progress_score_final = gss["progress_score"]

    return {
        "run_id": summary.get("run_id", run_path.name),
        "seed": summary.get("seed"),
        "outcome": summary.get("outcome", "unknown"),
        "total_turns": summary.get("total_turns", 0),
        "anomaly_count": anomaly_count,
        "distinct_event_types": sorted(distinct_event_types),
        "key_pickup_turn": key_pickup_turn,
        "door_encounter_turn": door_encounter_turn,
        "door_unlocked_turn": door_unlocked_turn,
        "messages_sent": messages_sent,
        "dm_interactions": dm_interactions,
        "progress_score_final": progress_score_final,
        "selected_for_repo": False,
        "_events": events,  # kept in memory, not written to JSON
    }


# ---------------------------------------------------------------------------
# Aggregate stats
# ---------------------------------------------------------------------------

def compute_aggregate(run_records: list[dict]) -> dict:
    outcome_distribution: dict[str, int] = {}
    turns_by_outcome: dict[str, list[int]] = {}
    anomaly_rate_by_run: list[dict] = []
    total_dm_interactions = sum(r["dm_interactions"] for r in run_records)

    for r in run_records:
        outcome = r["outcome"]
        outcome_distribution[outcome] = outcome_distribution.get(outcome, 0) + 1
        turns_by_outcome.setdefault(outcome, []).append(r["total_turns"])
        anomaly_rate_by_run.append({"run_id": r["run_id"], "seed": r["seed"], "anomaly_count": r["anomaly_count"]})

    avg_turns_by_outcome = {
        outcome: round(sum(turns) / len(turns), 1)
        for outcome, turns in turns_by_outcome.items()
    }

    anomaly_rate_by_run.sort(key=lambda x: x["anomaly_count"], reverse=True)

    if total_dm_interactions > 0:
        stale_data_decisions = _count_stale_data_decisions(run_records)
    else:
        stale_data_decisions = "deferred — no DM interactions in current runs"

    return {
        "outcome_distribution": outcome_distribution,
        "avg_turns_by_outcome": avg_turns_by_outcome,
        "anomaly_rate_by_run": anomaly_rate_by_run,
        "stale_data_decisions": stale_data_decisions,
    }


def _count_stale_data_decisions(run_records: list[dict]) -> int:
    """
    Count turns across all runs where a DM response was followed by an action
    that contradicted what the DM said (i.e., world_truth differed from DM belief).
    Approximated as: dm_response events where stale_grid differs from actual_grid_snapshot.
    """
    contradictions = 0
    for r in run_records:
        for ev in r.get("_events", []):
            if ev.get("event_type") != "dm_response":
                continue
            belief = ev.get("agent_belief", {})
            truth = ev.get("world_truth", {})
            stale = belief.get("stale_grid")
            actual = truth.get("actual_grid_snapshot")
            if stale and actual and stale != actual:
                contradictions += 1
    return contradictions


# ---------------------------------------------------------------------------
# Incident report generation
# ---------------------------------------------------------------------------

def _build_condensed_summary(run_record: dict, summary: dict) -> str:
    """Build a condensed event summary for the LLM incident report."""
    events = run_record.get("_events", [])
    notable = []

    for ev in events:
        action = ev.get("action", {})
        tool = action.get("tool", "")
        raw_result = action.get("result", {})
        result = raw_result if isinstance(raw_result, dict) else {}
        anomaly = action.get("anomaly", False)
        turn = ev.get("turn", 0)
        agent = ev.get("agent_id", "?")
        event_type = ev.get("event_type", "")

        if anomaly:
            notable.append(f"Turn {turn} Agent {agent}: {tool} → ANOMALY ({action.get('anomaly_reason')})")
        elif tool == "pick_up" and result.get("status") == "success":
            notable.append(f"Turn {turn} Agent {agent}: picked up key")
        elif tool == "move" and result.get("reason") == "locked_door":
            notable.append(f"Turn {turn} Agent {agent}: blocked at locked door")
        elif tool == "unlock_door" and result.get("status") == "success":
            notable.append(f"Turn {turn} Agent {agent}: unlocked door")
        elif tool == "send_message":
            to = action.get("args", {}).get("to", "?")
            content = action.get("args", {}).get("content", "")[:80]
            notable.append(f"Turn {turn} Agent {agent}: message to {to}: {content!r}")
        elif event_type == "dm_response":
            to = action.get("args", {}).get("to", "?")
            content = action.get("args", {}).get("content", "")[:80]
            notable.append(f"Turn {turn} DM → {to}: {content!r}")

    lines = [
        f"Seed: {run_record['seed']}",
        f"Outcome: {summary.get('outcome')} — {summary.get('reason')}",
        f"Total turns: {summary.get('total_turns')}",
        f"Anomalies: {run_record['anomaly_count']}",
        f"Final progress score: {run_record['progress_score_final']}",
        f"Key pickup turn: {run_record['key_pickup_turn']}",
        f"Door unlocked turn: {run_record['door_unlocked_turn']}",
        f"Messages sent: {run_record['messages_sent']}",
        f"DM interactions: {run_record['dm_interactions']}",
        "",
        "Notable events:",
    ] + notable[:40]  # cap to avoid token overflow

    return "\n".join(lines)


def generate_incident_report(run_record: dict, summary: dict) -> str:
    import time
    condensed = _build_condensed_summary(run_record, summary)
    for attempt in range(4):
        try:
            response = _client.messages.create(
                model=MODEL,
                max_tokens=300,
                system=(
                    "You are a technical analyst. Write a 3-5 sentence plain-English incident report "
                    "about this dungeon agent run. Reference specific turn numbers and agent IDs. "
                    "Explain what happened, why it failed (if it did), and what the most notable "
                    "decision points were."
                ),
                messages=[{"role": "user", "content": condensed}],
            )
            return response.content[0].text.strip()
        except Exception as e:
            if "rate_limit" in str(e) and attempt < 3:
                wait = 15 * (attempt + 1)
                print(f"rate limit, waiting {wait}s...", end=" ", flush=True)
                time.sleep(wait)
            else:
                raise


# ---------------------------------------------------------------------------
# Run selection
# ---------------------------------------------------------------------------

def select_top_runs(run_records: list[dict], n: int = 10) -> list[dict]:
    ranked = sorted(
        run_records,
        key=lambda r: (len(r["distinct_event_types"]), r["anomaly_count"]),
        reverse=True,
    )
    for r in ranked[:n]:
        r["selected_for_repo"] = True
    return run_records


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    if not RUNS_DIR.exists():
        print("No runs/ directory found. Run some simulations first.")
        return

    run_dirs = sorted(RUNS_DIR.iterdir())
    print(f"Found {len(run_dirs)} run directories.")

    run_records: list[dict] = []
    summaries: dict[str, dict] = {}

    for d in run_dirs:
        if not d.is_dir():
            continue
        stats = compute_run_stats(d)
        if stats is None:
            print(f"  Skipping {d.name} — no summary.json")
            continue
        run_records.append(stats)
        summary_path = d / "summary.json"
        with open(summary_path) as f:
            summaries[stats["run_id"]] = json.load(f)
        print(f"  {d.name}: outcome={stats['outcome']} turns={stats['total_turns']} anomalies={stats['anomaly_count']} events={len(stats['distinct_event_types'])}")

    if not run_records:
        print("No valid runs found.")
        return

    # Select top 10
    select_top_runs(run_records)
    selected = [r for r in run_records if r["selected_for_repo"]]
    print(f"\nSelected {len(selected)} runs for repo.")

    # Generate incident reports
    print("\nGenerating incident reports...")
    for r in run_records:
        run_id = r["run_id"]
        summary = summaries.get(run_id, {})
        if summary.get("incident_report"):
            print(f"  {run_id}: already has incident_report, skipping")
            continue
        print(f"  {run_id}: generating...", end=" ", flush=True)
        try:
            report = generate_incident_report(r, summary)
            summary["incident_report"] = report
            summary_path = RUNS_DIR / run_id / "summary.json"
            with open(summary_path, "w") as f:
                json.dump(summary, f, indent=2, default=str)
            print("done")
        except Exception as e:
            print(f"ERROR: {e}")

    # Compute aggregate
    aggregate = compute_aggregate(run_records)

    # Build output (strip internal _events key)
    output_records = []
    for r in run_records:
        rec = {k: v for k, v in r.items() if k != "_events"}
        # Add incident_report inline for convenience
        summary = summaries.get(r["run_id"], {})
        if summary.get("incident_report"):
            rec["incident_report"] = summary["incident_report"]
        output_records.append(rec)

    analysis = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_runs": len(run_records),
        "runs": output_records,
        "aggregate": aggregate,
    }

    with open(ANALYSIS_PATH, "w") as f:
        json.dump(analysis, f, indent=2, default=str)

    print(f"\nWrote {ANALYSIS_PATH}")
    print(f"Outcome distribution: {aggregate['outcome_distribution']}")
    print(f"Avg turns by outcome: {aggregate['avg_turns_by_outcome']}")


if __name__ == "__main__":
    main()
