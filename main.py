"""Forecast GTA VI's launch-day Twitch viewers hour by hour.

Locked primary model (see README.md for the full specification):
  - Weighted median log-ratio of historical launch trajectories, by tier.
  - Per-hour weighted empirical quantiles, widened parametrically (k = 1.16).
  - Strict missing-input validation: invalid or missing hour-1 data fails
    visibly instead of being silently replaced.

Standard library only. No network. Deterministic: the same input always
produces byte-identical output.
"""

import json
import math
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

INPUT_DIR = Path(os.environ.get("MAKEFUN_INPUT_DIR", "/input"))
OUTPUT_DIR = Path(os.environ.get("MAKEFUN_OUTPUT_DIR", "/output"))

LEVELS = [0.05, 0.25, 0.5, 0.75, 0.95]
Z = [-1.645, -0.674, 0.0, 0.674, 1.645]

# Locked hyperparameters (fixed before any evaluation; never tuned here).
K = 1.16  # parametric widening: t(0.95, df=10)/z * sqrt(1 + 1/10)
BASELINE_SIGMA = 0.15  # house baseline spread, used only as a no-data fallback
ADJUST_HOURS = {6, 9, 13, 14, 15, 16, 17, 18, 19}
ADJUST_CAP = 0.15  # max |log| final-only hour-1 trend adjustment

# Analogue tiers, by exact comparable name. Weight 2: unlock verified by a
# named source AND a full 24-hour curve present in the kit. Weight 2 also
# covers the four practice launches, whose unlock is verified in the kit
# notes but whose full curves only appear in the final case (42
# comparables). Weight 1: console, rolling-midnight assumed, unlock
# unverified in the kit. Every other comparable: weight 0 (excluded).
TIER_WEIGHTS = {
    # Verified tier (weight 2).
    "Ghost of Y\u014dtei": 2.0,
    "The Legend of Zelda: Tears of the Kingdom": 2.0,
    "Marvel's Wolverine": 2.0,
    "Red Dead Redemption 2": 2.0,
    "God of War Ragnar\u00f6k": 2.0,
    "Marvel's Spider-Man 2": 2.0,
    # Assumed tier (weight 1).
    "FINAL FANTASY VII REBIRTH": 1.0,
    "The Last of Us Part II": 1.0,
    "Ghost of Tsushima": 1.0,
    "Stellar Blade": 1.0,
    "Saros": 1.0,
}

# Locked per-hour log-ratio SD profile, measured on the clean set.
# Used as a spread floor and as the fallback SD when fewer than 2 tier
# games have data for an hour. Linear interpolation between knots.
SIGMA_PROFILE = {
    2: 0.127, 4: 0.234, 6: 0.192, 9: 0.313, 12: 0.343,
    15: 0.456, 18: 0.483, 21: 0.402, 24: 0.365,
}


def round2(x):
    """x rounded to 2 decimals, half up, as the kit starter rounds it."""
    scaled = x * 100
    whole = math.floor(scaled)
    if scaled - whole >= 0.5:
        whole += 1
    value = whole / 100
    return int(value) if value == int(value) else value


def parse_time(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def sigma_profile(h):
    """Locked SD profile, linearly interpolated between knots."""
    knots = sorted(SIGMA_PROFILE)
    if h <= knots[0]:
        return SIGMA_PROFILE[knots[0]]
    if h >= knots[-1]:
        return SIGMA_PROFILE[knots[-1]]
    for a, b in zip(knots, knots[1:]):
        if a <= h <= b:
            f = (h - a) / (b - a)
            return SIGMA_PROFILE[a] * (1 - f) + SIGMA_PROFILE[b] * f
    raise AssertionError("unreachable")


def weighted_quantile(pairs, tau):
    """Smallest value whose cumulative weight reaches tau of the total.

    pairs: list of (value, weight), weights > 0. Deterministic.
    """
    total = sum(w for _, w in pairs)
    if total <= 0:
        raise ValueError("empty weighted distribution")
    cum = 0.0
    for value, w in sorted(pairs):
        cum += w
        if cum >= tau * total:
            return value
    return sorted(pairs)[-1][0]


def weighted_mean_sd(pairs):
    """Weighted population mean and SD. pairs: list of (value, weight)."""
    total = sum(w for _, w in pairs)
    mean = sum(v * w for v, w in pairs) / total
    var = sum(w * (v - mean) ** 2 for v, w in pairs) / total
    return mean, math.sqrt(var)


def day_curve(comparable):
    """Hour-1..24 viewer counts for one comparable, keyed by hour.

    Missing hours are absent (never interpolated). Returns (curve, ok)
    where curve maps hour -> viewers, using the comparable's own
    day_start as hour 1.
    """
    start = parse_time(comparable["day_start"])
    curve = {}
    for row in comparable.get("hourly", []):
        v = row.get("viewers")
        if not (isinstance(v, (int, float)) and math.isfinite(v) and v > 0):
            continue
        try:
            t = parse_time(row["from"])
        except (KeyError, TypeError, ValueError):
            continue
        offset = (t - start).total_seconds() / 3600.0
        h = int(offset) + 1
        if 1 <= h <= 24 and offset == int(offset):
            curve[h] = v
    return curve


def hour1_trend_adjustment(case):
    """Final-only hour-1 trend adjustment, in log units, or 0.0.

    Uses the last history point and the latest earlier point at least
    30 minutes before it. Capped at +/-ADJUST_CAP. Returns 0.0 when the
    history is missing, sparse, or unusable -- never a fabricated value.
    """
    history = case.get("history") or []
    points = []
    for row in history:
        v = row.get("viewers")
        if isinstance(v, (int, float)) and math.isfinite(v) and v > 0:
            try:
                points.append((parse_time(row["at"]), v))
            except (KeyError, TypeError, ValueError):
                continue
    if len(points) < 2:
        return 0.0
    points.sort()
    t_last, v_last = points[-1]
    prev = None
    for t, v in reversed(points[:-1]):
        if (t_last - t) >= timedelta(minutes=30):
            prev = (t, v)
            break
    if prev is None:
        return 0.0
    trend = math.log(v_last / prev[1])
    adj = 0.5 * trend
    return max(-ADJUST_CAP, min(ADJUST_CAP, adj))


def baseline_quantiles(p1, h):
    """House baseline persistence forecast for one hour (no-data fallback)."""
    return [p1 * math.exp(z * BASELINE_SIGMA * math.sqrt(h - 1)) for z in Z]


def forecast_case(case, name):
    if case.get("levels") != LEVELS:
        raise ValueError(f"{name} asks for levels {case.get('levels')}, not {LEVELS}")
    known = [k for k in case.get("known", []) if k.get("hour") == 1]
    if not known:
        raise ValueError(f"{name} has no hour-1 print")
    p1 = known[0].get("viewers")
    if not isinstance(p1, (int, float)) or not math.isfinite(p1) or p1 <= 0:
        raise ValueError(f"{name} has an invalid hour-1 print: {p1!r}")
    p1 = float(p1)

    # Tier log-ratios per hour, from the comparables in this case only.
    # A game-hour with a missing value is excluded, never interpolated.
    log_ratios = {h: [] for h in range(2, 25)}
    for comp in case.get("comparables", []):
        w = TIER_WEIGHTS.get(comp.get("name"))
        if not w:
            continue
        curve = day_curve(comp)
        v1 = curve.get(1)
        if v1 is None:
            continue
        for h in range(2, 25):
            vh = curve.get(h)
            if vh is not None:
                log_ratios[h].append((math.log(vh / v1), w))

    adj = hour1_trend_adjustment(case)

    rows = []
    for row in sorted(case["forecast"], key=lambda r: r["hour"]):
        h = row["hour"]
        if h < 2 or h > 24:
            raise ValueError(f"{name} asks to forecast hour {h}")
        pairs = log_ratios[h]
        if len(pairs) == 0:
            qs = baseline_quantiles(p1, h)
        else:
            median = weighted_quantile(pairs, 0.5)
            _, sd_pool = weighted_mean_sd(pairs)
            sd = sd_pool if len(pairs) >= 2 else sigma_profile(h)
            sd = max(sd, sigma_profile(h))  # locked spread floor
            qs = []
            for tau, z in zip(LEVELS, Z):
                q_emp = weighted_quantile(pairs, tau)
                widened_emp = median + (q_emp - median) * K
                parametric = median + z * sd * K
                if tau < 0.5:
                    log_q = min(widened_emp, parametric)
                elif tau > 0.5:
                    log_q = max(widened_emp, parametric)
                else:
                    log_q = median
                if h in ADJUST_HOURS:
                    log_q += adj
                qs.append(p1 * math.exp(log_q))
        qs = [round2(q) for q in qs]
        for q in qs:
            if not isinstance(q, (int, float)) or not math.isfinite(q):
                raise ValueError(f"{name} hour {h}: non-finite quantile")
            if q <= 0 or q > 1_000_000_000:
                raise ValueError(f"{name} hour {h}: quantile out of range: {q}")
        for a, b in zip(qs, qs[1:]):
            if b < a:
                raise ValueError(f"{name} hour {h}: quantiles cross: {qs}")
        rows.append({"hour": h, "q": qs})
    if [r["hour"] for r in rows] != list(range(2, 25)):
        raise ValueError(f"{name} did not produce hours 2..24")
    return {"hours": rows}


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for path in sorted(INPUT_DIR.glob("*.in")):
        case = json.loads(path.read_text(encoding="utf-8"))
        forecast = forecast_case(case, path.name)
        out = OUTPUT_DIR / f"{path.stem}.out"
        out.write_text(
            json.dumps(forecast, separators=(",", ":")) + "\n", encoding="utf-8"
        )


if __name__ == "__main__":
    main()
