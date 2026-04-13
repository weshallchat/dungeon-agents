# Dungeon Agents — 3-Minute Video Script

**Format:** Talking head + screen recording of the viewer  
**Pace:** ~150 words/minute → ~450 words total  
**Sections:** Intro (20s) · 5 Design Decisions (2m 10s) · Close (30s)

---

## [0:00 – 0:20] Opening

The goal of this project wasn't to build a great dungeon game.  
It was to build a system where, after something goes wrong,  
you can open a single HTML file and immediately understand *why*.

I built a two-agent LLM simulation — both agents running claude-haiku —  
navigating a dungeon with fog of war, a locked door, and a shared key.  
68 runs, structured traces on every event, and a viewer to diagnose each one.

Here are the five design decisions that shaped everything.

---

## [0:20 – 0:50] Decision 1: Every event carries full game state

The first decision was in the event schema.  
Every single event — every tool call, every anomaly — carries a `game_state_summary`:  
the door state, who holds the key, both agent positions, and a progress score from 0 to 1.

The tradeoff: the events.jsonl files are larger than they need to be.  
But the payoff is that `analyze.py` can aggregate across 68 runs  
with simple field reads — no state reconstruction, no stream replay.  
You can sort runs by progress score in one line.

---

## [0:50 – 1:20] Decision 2: Belief vs. truth on every event

Each event also records two views of the world:  
what the agent *believed* — its fog-of-war state —  
and what was *actually true*.

This makes belief divergence a first-class observable.  
When the DM gives an agent stale directions,  
you can see the exact turn where the agent's map contradicted reality.

The tradeoff: it doubles the data stored per event.  
And for most turns with no divergence, it's redundant.  
But the viewer's timeline tab highlights those divergences instantly —  
without that dual record, you'd have to reconstruct it post-hoc.

---

## [1:20 – 1:50] Decision 3: Injected chaos with a seeded RNG

Move has a 10% chance of a spurious block. Messages have a 15% chance of a one-turn delay.  
Both are seeded — pass the same seed and you get the exact same failure pattern.

The tradeoff: this is synthetic noise, not real infrastructure failure.  
It doesn't model the full complexity of a production system.  
But it gives you *reproducible* interesting runs.  
Seed 26 always hits 21 anomalies. Seed 46 always produces all six distinct event types.  
That reproducibility is what makes the viewer useful as a diagnostic tool —  
you're not chasing a fluke.

---

## [1:50 – 2:20] Decision 4: Reactive DM with stale state

The Dungeon Master agent doesn't get its own scheduled turn.  
It only fires when an explorer sends it a message — within the same turn cycle.  
And its view of the board is always three turns behind.

The tradeoff: this is a realistic model of a support system with latency.  
But it meant agents rarely queried the DM unless explicitly prompted —  
the system prompt had to be tuned to encourage it.  
And when the DM does respond with outdated coordinates,  
the belief-vs-truth records catch it.

---

## [2:20 – 2:50] Decision 5: Single HTML viewer, no build step

The legibility layer is a single `index.html` — no framework, no server, no build.  
You drag a `events.jsonl` file onto it and everything renders client-side.  
Plain CSS grids for the dungeon, plain canvas for the charts.

The tradeoff: it doesn't scale to millions of events.  
But for a diagnostic tool used by one engineer on one run at a time,  
zero setup cost is the right call.  
The viewer loads a run from any machine, offline, in under a second.

---

## [2:50 – 3:00] Close

The core bet was: structured traces plus a zero-friction viewer  
is more useful than a sophisticated model doing clever things.  
Out of 68 runs, only 2 succeeded.  
But every failure is fully legible — which is the point.

---

*End of script.*
