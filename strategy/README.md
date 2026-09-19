# IAMABOT strategy

中文完整说明见 [`STRATEGY_v10.md`](STRATEGY_v10.md)（Chinese write-up of the current strategy, built from real server replay downloads of DIBSFA/Potatoes/Gang/JaniceKeepTalking/clankerbot/Team Name; `STRATEGY_v9.md`, `STRATEGY_v8.md`, `STRATEGY_v7.md` and `STRATEGY_v6.1.md` are earlier versions). Regression matches: [`tools/arena`](../tools/arena/README.md).

`main.py` always selects `AdvancedStrategy` (in `brain.py`) in a tournament match. The
controller is stateful and side agnostic because the engine mirrors the map for team B.

## Design

The rules below come from the engine source (`mm-engine`), not only the wiki prose.

- **Fire control.** Before a trigger is pulled, the shot is replayed exactly as the engine
  resolves it: the shooter's post-move, post-turn pose, the ray stopping at the first enemy
  hull, the payload, a deposit, a wall or the map edge, and splash on every enemy within
  `splash + radius` of that point. A shot is only fired if it lands on a vulnerable enemy
  under both the predicted (pos + vel) and the stationary enemy layout. Shooters claim
  victims per tick, so one volley never lands five shots on one invulnerable bot.
- **Target assignment.** Aims are assigned globally by value (healers, bots in the capture
  circle, low health, one-shot kills) against the time until the shot can land (turning at
  3°/tick, cooldown, invulnerability), with a cap per target and no lines through the
  payload, which is solid and eats shots along the corridor.
- **Positioning (press).** When enemy fighters exist, every battle bot closes on its
  nearest one and stands to shoot just inside blaster range (9.3 of 10), so all guns engage
  at once, converge on the enemy's nearest bodies, and the enemy must walk into our fire.
  With a clear local edge (1.5x) the distance closes to 7.5 to finish the fight; bots at
  4 hp or less back out of reach while still shooting and return once healed to 8. Bots stay one splash diameter apart and off solid
  hulls (a shot into the payload or a deposit splashes bots hugging it). With no enemy
  fighter around, the army takes the capture circle and pushes. Head to head, pressing beat
  every firing-position planner we tried, including our own v5.
- **Deposit campers.** When half or more of the enemy's fighting power sits within 11 of our
  deposit and we are clearly stronger around the payload (sustained 120 ticks), the army
  stops chasing them and fights only within 11 of the payload, pushing it; it resumes normal
  play once the payload fight evens up (60-tick exit hysteresis).
- **Healers.** Healers are paired with patients globally, preferring heals that can land
  this tick, stand behind the patient relative to the enemy, and fire only after the final
  move is known (range, arc and line of sight are checked on the post-move pose).
- **Economy.** A balanced opening (3 extractors, 10 battle bots, 4 healers) starts the economy
  at once while still winning the first fight; the top teams open with 7-10 extractors and
  an all-fighter opening falls behind them by mid game. Past the opening the steady state is
  deliberately lean (up to four extractors, spread on slots around our deposit) with 36% of
  the fighting force as healers: real server replays of Gang, clankerbot and JaniceKeepTalking
  (see `STRATEGY_v10.md` 3) show they all run a small, fixed extractor count and 30-38%
  healers, out-gunning a heavier-economy build slot for slot. Deposit guards are only sent
  in a size that can win locally; against a larger raid the extractors run instead.
- **Endgame.** Tokens are worthless once production stops. In the last 80 ticks before the
  cutoff, `_endgame_convert` stops building economy and, once the fleet is full, self-destructs
  extractors one at a time and rushes battle bots into the freed slots — the same trick real
  DIBSFA and Gang use (measured: extractors 8→0 at the exact tick production stops). One
  extractor is always kept back as the hideout body. If the payload sits on our half from the
  endgame on, every gun goes for the bodies holding the circle at close range and two bots
  walk in to push it back.
- **Compute.** Hot loops use plain floats and call the engine helpers through the raw C entry
  points; a typical tick costs ~0.5 ms, far under the per-tick refill.

## Local verification

```sh
mm-cli run --quiet
```

`MM_IAMABOT_LOCAL_AB=advanced-a|advanced-b` pits the production strategy against the simple
`ReferenceStrategy` from either side; the tournament never sets it.

`strategy/brain_team.py` keeps the team's earlier strategies (`FinalStrategy` and others)
from the `main` branch; it is not imported by `main.py`.
