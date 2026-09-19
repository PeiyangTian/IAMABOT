# IAMABOT strategy

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
- **Positioning.** Firing positions are chosen against the enemy front: inside our range,
  with a clear line past walls and the payload, spread at least one splash diameter apart
  and off solid hulls (a shot into the payload or a deposit splashes bots hugging it).
  A couple of anchors contest the capture circle. The army presses when it is stronger,
  holds at parity and falls back out of range to collect reinforcements when outnumbered.
- **Healers.** Healers are paired with patients globally, preferring heals that can land
  this tick, stand behind the patient relative to the enemy, and fire only after the final
  move is known (range, arc and line of sight are checked on the post-move pose).
- **Economy.** The starting bank buys fighters (the first fight at the payload decides most
  games); extractors follow once the army holds its own, on spread slots around our deposit.
  Deposit guards are only sent in a size that can win locally; against a larger raid the
  extractors run instead of feeding guards in a few at a time.
- **Endgame.** Tokens are worthless once production stops, so extractors become capture
  bodies, and one bot hides to avoid losing to a fleet wipe.
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
