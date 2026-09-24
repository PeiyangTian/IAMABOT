# IAMABOT

A competition bot for [MechMania](https://mechmania.github.io/) 2026 — a real-time
strategy game where two fleets fight over a payload. Python strategy, with a Rust FFI
shim so the bot calls the engine's own pathfinding instead of reimplementing it.
**The team finished seventh.**

**This is a three-person team project.** Yixuan ZHANG wrote the controller and the
regression arena; tracira and I contributed on top of it. Setup instructions are in
[`SETUP.md`](SETUP.md); `git log` and `git blame` are the authoritative record of who
wrote what.

## My contribution — Peiyang Tian

**v10: the local test suite was green while we were losing on the server.**

By v9 our regression arena reported 87 wins out of 88 against the full opponent roster.
The real match database told a different story — against the four teams that mattered we
were 1–17:

| Opponent | Real server record (IAMABOT's view) |
|---|---|
| Gang | 0–5 |
| clankerbot | 1–5 |
| JaniceKeepTalking | 0–4 |
| Team Name | 0–3 |

The local matrix had not surfaced any of it, because the opponent models it played against
were built from replays that were by then months stale — and those teams had kept
developing.

So I went to the real data:

1. Pulled every IAMABOT match against six named teams from the competition's public API,
   and reconstructed our complete head-to-head history.
2. Downloaded the per-tick NDJSON logs and wrote a parser for them. The format is
   delta-encoded — after the first tick, fleets arrive as `{added, changed, removed}` — so
   the parser replays the deltas to rebuild full fleet state at every tick, then measures
   build order, casualty curves, engagement distance, formation spacing, map control split,
   and payload progress.
3. Checked the v9 opponent models against what the teams were actually doing. Three of the
   four had changed their play entirely; the fourth we had modelled correctly but reproduced
   far too weakly.
4. Found two fixes that the measurements pointed at directly — the endgame never converted
   miners into fighters, and economy over-investment left fewer combat units at equal
   supply — and validated both through the arena under its no-regression rule: a change
   ships only if it loses no game the baseline won, on the full roster, from both sides.
5. Rebuilt four opponent models from the real replays and added them back to the arena, so
   the next time the server and the local matrix disagree, the matrix catches it first.

The write-up, in Chinese, is [`strategy/STRATEGY_v10.md`](strategy/STRATEGY_v10.md); its
final section documents the API calls and log format so the study can be repeated. Earlier
versions — [v6.1](strategy/STRATEGY_v6.1.md), [v7](strategy/STRATEGY_v7.md),
[v8](strategy/STRATEGY_v8.md), [v9](strategy/STRATEGY_v9.md) — trace how the strategy got
there.

**Known limits, from that write-up:** each team was reconstructed from only one or two
matches, so the models are a snapshot of opponents that keep changing; and the economy
change is global, with a measured cost against one style of opponent that was judged worth
the gain against the three teams actually beating us.

## How the bot works

Credit for the design below goes to the team; it is summarised here so the repository reads
on its own. [`strategy/README.md`](strategy/README.md) has the full version.

- **Fire control.** Before a trigger is pulled the shot is replayed exactly as the engine
  resolves it — the shooter's post-move pose, the ray stopping at the first hull, splash on
  everything within range of that point — and only fires if it lands under both the
  predicted and the stationary enemy layout.
- **Target assignment.** Aims are allocated globally by value against time-to-land, capped
  per target, so one volley never wastes five shots on one invulnerable bot.
- **Regression arena** ([`tools/arena`](tools/arena/README.md)). Every tactical change is
  judged on the same opponents, from both sides, against a recorded baseline. `results/`
  keeps the raw JSONL behind each decision.

## Layout

| Path | What |
|---|---|
| `strategy/` | the controller and the strategy write-ups |
| `tools/arena/` | regression harness, opponent roster, replay analysis |
| `core/`, `native/`, `__main__.py` | starterpack and generated bindings, from [mechmania/cli](https://github.com/mechmania/cli) — not our work |

Upstream team repository: [asher0913/IAMABOT](https://github.com/asher0913/IAMABOT).
