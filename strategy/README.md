# IAMABOT strategy

`main.py` always selects `AdvancedStrategy` in a tournament match. The controller in
`brain.py` is stateful and side agnostic because the engine mirrors the map for team B.

## Design

- Rushes while production is enabled and builds toward 18 Battle, 8 Extractor and 6
  Healer bots.
- Assigns extractors to separate legal locations around the deposit; they join the
  payload when production ends.
- Places the combat fleet in two separated payload rings instead of stacking units in one
  splash-damage target.
- Keeps two nearby Battle bots as home defenders when the deposit is threatened and uses
  a late raider group against exposed enemy support units.
- Scores targets by objective pressure, class, missing health, kill opportunity and splash
  value. Ready shooters reserve separate targets so a target's temporary invulnerability
  does not waste the rest of the volley.
- Assigns no more than the configured healer-stack cap to one ally and predicts movement
  before checking heal range and firing arc.
- Falls back to a cheaper controller when the engine's compute bank drops below 100,000.

## Local verification

Run the production strategy against itself:

```sh
mm-cli run --quiet
```

Run the production strategy against the active reference controller from both sides:

```sh
MM_IAMABOT_LOCAL_AB=advanced-a mm-cli run --quiet
MM_IAMABOT_LOCAL_AB=advanced-b mm-cli run --quiet
```

The environment variable is only a local test hook. If it is absent, both sides returned
by `get_strategy` use the production controller.
