"""
Sizing-policy comparison on a synthetic fleet with realistic spike patterns.

Policies:
  average  - over-sized if CPU avg < 20 and memory avg < 30; size so that
             projected AVERAGE CPU <= 75% and average memory <= 80%
  maximum  - same averages plus peaks below 50/60; size on the single
             busiest minute (CPU) and memory peak
  costguard- the real analyze_instance() + recommend() from the project

A recommendation is UNSAFE if, on the recommended size, the busiest CPU
minute we observed would need more than 100% of the new vCPUs, or the
memory peak would need more than 100% of the new RAM.

Five fleets of 1,000 synthetic databases (seeds 1-5); prints mean and
standard deviation of recommendations, savings and unsafe picks per policy.
No AWS account needed.

Run from the project root:
    python experiments/policy_comparison.py results/policy.json
"""
import json
import math
import os
import random
import statistics
import sys
from pathlib import Path

sys.path.insert(0, os.getcwd())
from src.analysis import analyze_instance
from src.aws_connector.pricing import estimate_monthly_cost
from src.recommendations import recommend
from src.recommendations.instance_sizes import smaller_sizes, get_vcpus, get_memory_gb

HOURS = 14 * 24
LADDER = ["db.t3.small", "db.t3.medium", "db.t3.large",
          "db.r5.large", "db.r5.xlarge", "db.r5.2xlarge", "db.r5.4xlarge",
          "db.m5.large", "db.m5.xlarge", "db.m5.2xlarge"]
PATTERNS = ["flat", "rare_spikes", "daily_batch", "busy_hours"]


def p95(values):
    v = sorted(values)
    return round(v[max(1, math.ceil(0.95 * len(v))) - 1], 2)


def make_db(i, rnd):
    itype = rnd.choice(LADDER)
    pattern = rnd.choice(PATTERNS)
    base = rnd.uniform(2, 18)                       # typical CPU %
    hourly_avg = [max(0.5, rnd.gauss(base, base * 0.15)) for _ in range(HOURS)]
    hourly_max = [a * rnd.uniform(1.1, 1.6) for a in hourly_avg]
    spike_hours = []
    if pattern == "rare_spikes":                    # a few one-off events (backups, migrations)
        spike_hours = rnd.sample(range(HOURS), rnd.randint(1, 8))
    elif pattern == "daily_batch":                  # 1 hour every day (~4% of hours)
        start = rnd.randrange(24)
        spike_hours = [d * 24 + start for d in range(14)]
    elif pattern == "busy_hours":                   # 3 busy hours every day (~12% of hours)
        start = rnd.randrange(22)
        spike_hours = [d * 24 + start + k for d in range(14) for k in range(3)]
    level = rnd.uniform(35, 95)
    for h in spike_hours:
        hourly_avg[h] = max(hourly_avg[h], level * rnd.uniform(0.6, 0.9))
        hourly_max[h] = max(hourly_max[h], min(100.0, level * rnd.uniform(0.95, 1.05)))
    hourly_max = [min(100.0, m) for m in hourly_max]
    mem_avg = rnd.uniform(5, 45)
    mem_max = min(100.0, mem_avg * rnd.uniform(1.02, 1.35))
    multi = rnd.random() < 0.3
    db = {"instance_id": f"db-{i:04d}", "instance_type": itype, "engine": "postgres", "region": "us-east-1",
          "monthly_cost": estimate_monthly_cost(itype, "us-east-1", multi), "storage_allocated_gb": 100,
          "multi_az": multi}
    m = {"instance_id": db["instance_id"],
         "cpu_avg_pct": round(statistics.mean(hourly_avg), 2),
         "cpu_p95_pct": p95(hourly_max),
         "cpu_max_pct": round(max(hourly_max), 2),
         "cpu_min_pct": round(min(hourly_avg), 2),
         "memory_avg_pct": round(mem_avg, 2), "memory_max_pct": round(mem_max, 2),
         "storage_used_gb": 50.0, "connections_avg": 5.0, "observation_window_days": 14}
    return db, m, pattern


def project(u, cur, new):
    return u * cur / new


def simple_policy(db, m, cpu_key, mem_key, peak_guard):
    """Average or maximum policy, written the obvious way."""
    if not (m["cpu_avg_pct"] < 20 and m["memory_avg_pct"] < 30):
        return None
    if peak_guard and not (m["cpu_max_pct"] < 50 and m["memory_max_pct"] < 60):
        return None
    cur = db["instance_type"]
    best = None
    for cand in smaller_sizes(cur):
        cpu = project(m[cpu_key], get_vcpus(cur), get_vcpus(cand))
        mem = project(m[mem_key], get_memory_gb(cur), get_memory_gb(cand))
        if cpu > 75 or mem > 80:
            break
        best = cand
    if not best:
        return None
    return {"recommended_type": best,
            "monthly_savings": round(db["monthly_cost"] - estimate_monthly_cost(best, "us-east-1", db["multi_az"]), 2)}


def costguard_policy(db, m):
    return recommend(db, m, analyze_instance(m))


def unsafe(db, m, rec):
    cur, new = db["instance_type"], rec["recommended_type"]
    cpu_peak = project(m["cpu_max_pct"], get_vcpus(cur), get_vcpus(new))
    mem_peak = project(m["memory_max_pct"], get_memory_gb(cur), get_memory_gb(new))
    return cpu_peak > 100 or mem_peak > 100


def run(seed):
    rnd = random.Random(seed)
    fleet = [make_db(i, rnd) for i in range(1000)]
    out = {}
    for name, fn in [("average", lambda d, m: simple_policy(d, m, "cpu_avg_pct", "memory_avg_pct", False)),
                     ("maximum", lambda d, m: simple_policy(d, m, "cpu_max_pct", "memory_max_pct", True)),
                     ("costguard", costguard_policy)]:
        recs, bad, by_pattern = 0, 0, {p: [0, 0] for p in PATTERNS}
        risk = {p: {"low": 0, "medium": 0, "high": 0} for p in PATTERNS}
        savings = 0.0
        for db, m, pattern in fleet:
            r = fn(db, m)
            if r:
                recs += 1
                savings += r["monthly_savings"]
                by_pattern[pattern][0] += 1
                if "risk_level" in r:
                    risk[pattern][r["risk_level"]] += 1
                if unsafe(db, m, r):
                    bad += 1
                    by_pattern[pattern][1] += 1
        out[name] = {"recs": recs, "savings": round(savings, 2), "unsafe": bad, "by_pattern": by_pattern, "risk": risk}
    out["patterns"] = {p: sum(1 for _, _, q in fleet if q == p) for p in PATTERNS}
    out["fleet_cost"] = round(sum(db["monthly_cost"] for db, _, _ in fleet), 2)
    return out


seeds = [1, 2, 3, 4, 5]
runs = [run(s) for s in seeds]
summary = {}
for pol in ("average", "maximum", "costguard"):
    summary[pol] = {k: {"mean": statistics.mean(r[pol][k] for r in runs), "sd": statistics.stdev(r[pol][k] for r in runs)}
                    for k in ("recs", "savings", "unsafe")}
    summary[pol]["by_pattern"] = {p: [sum(r[pol]["by_pattern"][p][j] for r in runs) for j in (0, 1)] for p in PATTERNS}
summary["costguard"]["risk"] = {p: {lv: sum(r["costguard"]["risk"][p][lv] for r in runs) for lv in ("low", "medium", "high")} for p in PATTERNS}
summary["fleet_cost"] = {"mean": statistics.mean(r["fleet_cost"] for r in runs)}
summary["patterns"] = {p: sum(r["patterns"][p] for r in runs) for p in PATTERNS}
summary["runs"] = runs
out_path = Path(sys.argv[1])
out_path.parent.mkdir(parents=True, exist_ok=True)
out_path.write_text(json.dumps(summary, indent=2))
print(json.dumps({k: v for k, v in summary.items() if k != "runs"}, indent=2))
