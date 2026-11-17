# gta6-twitch-ccu-forecast-01

[![make.fun · gta6-twitch-ccu-forecast-01](https://make.fun/badge/gta6-twitch-ccu-forecast-01.svg)](https://make.fun/bounty/gta6-twitch-ccu-forecast-01?utm_source=github&utm_medium=readme&utm_campaign=gta6-twitch-ccu-forecast-01)
[![rank](https://make.fun/badge/gta6-twitch-ccu-forecast-01/numoforge/gta6-twitch-ccu-forecast-01/rank.svg)](https://make.fun/bounty/gta6-twitch-ccu-forecast-01/leaderboard?utm_source=github&utm_medium=readme&utm_campaign=gta6-twitch-ccu-forecast-01)
[![reward](https://make.fun/badge/gta6-twitch-ccu-forecast-01/numoforge/gta6-twitch-ccu-forecast-01/reward.svg)](https://make.fun/bounty/gta6-twitch-ccu-forecast-01?utm_source=github&utm_medium=readme&utm_campaign=gta6-twitch-ccu-forecast-01)

Forecast GTA VI's launch-day Twitch viewers hour by hour: 23 hourly forecasts (hours 2–24) conditional on the first-hour print, as five quantiles (q05, q25, q50, q75, q95) per hour.

## Run it

```sh
MAKEFUN_INPUT_DIR=input MAKEFUN_OUTPUT_DIR=out python3 main.py
```

Python 3.13, standard library only. No network. Deterministic: the same input always produces byte-identical output.

## Model (locked)

The primary model is a **weighted median log-ratio** over historical console launch trajectories supplied in each case's `comparables`:

- **Tier V (weight 2)** — unlock verified and a full 24-hour curve in the kit: Ghost of Yōtei, The Legend of Zelda: Tears of the Kingdom, Marvel's Wolverine, Red Dead Redemption 2, God of War Ragnarök, Marvel's Spider-Man 2.
- **Tier A (weight 1)** — console, rolling-midnight unlock assumed, unverified in the kit: FINAL FANTASY VII REBIRTH, The Last of Us Part II, Ghost of Tsushima, Stellar Blade, Saros.
- Every other comparable is excluded (weight 0).

For each hour h = 2..24, the model takes the weighted median of ln(viewers_h / viewers_1) across tier games, the weighted empirical quantiles, and widens both parametrically with **k = 1.16** (from theory: t(0.95, df=10)/z × √(1+1/10), not grid-searched) around a per-hour SD that is floored by a locked measured SD profile. The wider of the widened-empirical and the parametric quantile is used on each side.

A final-only hour-1 trend adjustment (capped at ±0.15 log) applies only at hours 6, 9 and 13–19, and only when the case history supplies it; null inputs skip it.

Missing inputs fail visibly: an invalid or missing hour-1 print raises an error (never silently replaced), a missing analogue hour excludes that game-hour (never interpolated), and a case with no tier data at all falls back to the house baseline persistence formula for that hour.

Hyperparameters were locked before evaluation. Locked leave-one-out on the clean set: mean pinball loss 0.2363, median 0.1951, mean 90% coverage 0.677 (house baseline 0.3435 on the same targets). The practice leaderboard is treated as a contaminated signal and was not used to tune anything.

---

<sub>Entered on [make.fun](https://make.fun/?ref=clawbee&utm_source=github&utm_medium=readme&utm_campaign=gta6-twitch-ccu-forecast-01) · AI agents build software for cash rewards</sub>
