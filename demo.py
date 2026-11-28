"""
CostGuard — Interactive Demo
=============================
Run this to demonstrate the full CostGuard pipeline:

    python demo.py

What it does:
  1. Asks for AWS credentials (real or press Enter to use demo mode)
  2. Connects to AWS RDS (or simulates it with realistic mock data)
  3. Lists all database instances
  4. Pulls 14 days of CloudWatch metrics per instance
  5. Analyses which are over-sized
  6. Generates downgrade recommendations with savings
  7. Writes the HTML report and opens it in your browser

Demo mode (no real AWS needed):
  Just press Enter at the credentials prompt.
  Three realistic instances are simulated:
    - idle-orders-db    → OVER-SIZED  → recommend downgrade, save $525.60/mo
    - busy-analytics-db → right-sized → leave alone
    - brand-new-db      → skipped     → not enough CloudWatch history yet
"""

from __future__ import annotations

import io
import os
import sys
import time
import webbrowser
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Force UTF-8 output on Windows
if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if sys.stderr.encoding != "utf-8":
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# ── colour helpers (no external lib needed) ───────────────────────────────────
RESET  = "\033[0m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
GREEN  = "\033[32m"
CYAN   = "\033[36m"
YELLOW = "\033[33m"
RED    = "\033[31m"
WHITE  = "\033[97m"
BLUE   = "\033[34m"

def c(text, *codes): return "".join(codes) + str(text) + RESET
def ok(msg):   print(f"  {c('✔', GREEN, BOLD)}  {msg}")
def info(msg): print(f"  {c('·', CYAN)}  {msg}")
def warn(msg): print(f"  {c('⚠', YELLOW, BOLD)}  {msg}")
def step(n, msg): print(f"\n{c(f'[{n}]', CYAN, BOLD)} {c(msg, WHITE, BOLD)}")
def hr(): print(f"\n{c('─' * 62, DIM)}\n")

def spin(msg, seconds=1.2):
    """Simple spinner."""
    frames = ["⠋","⠙","⠹","⠸","⠼","⠴","⠦","⠧","⠇","⠏"]
    end = time.time() + seconds
    i = 0
    while time.time() < end:
        print(f"\r  {c(frames[i % len(frames)], CYAN)}  {msg}  ", end="", flush=True)
        time.sleep(0.08)
        i += 1
    print(f"\r  {c('✔', GREEN, BOLD)}  {msg}  \n", flush=True)


# ══════════════════════════════════════════════════════════════════════════════
#  MOCK DATA  (used when demo mode is active — no real AWS needed)
# ══════════════════════════════════════════════════════════════════════════════

DEMO_INSTANCES = [
    {
        "instance_id": "idle-orders-db",
        "instance_type": "db.r5.2xlarge",
        "engine": "postgres",
        "region": "us-east-1",
        "monthly_cost": 700.80,
        "storage_allocated_gb": 500,
        "multi_az": False,
    },
    {
        "instance_id": "busy-analytics-db",
        "instance_type": "db.r5.2xlarge",
        "engine": "mysql",
        "region": "us-east-1",
        "monthly_cost": 700.80,
        "storage_allocated_gb": 1000,
        "multi_az": False,
    },
    {
        "instance_id": "brand-new-db",
        "instance_type": "db.r5.large",
        "engine": "postgres",
        "region": "us-east-1",
        "monthly_cost": 175.20,
        "storage_allocated_gb": 100,
        "multi_az": False,
    },
]

DEMO_METRICS = {
    "idle-orders-db": {
        "instance_id": "idle-orders-db",
        "cpu_avg_pct": 15.0, "cpu_p95_pct": 15.0,
        "cpu_max_pct": 15.0, "cpu_min_pct": 2.1,
        "memory_avg_pct": 15.0, "memory_max_pct": 15.0,
        "storage_used_gb": 210.0, "connections_avg": 8.3,
        "observation_window_days": 14,
    },
    "busy-analytics-db": {
        "instance_id": "busy-analytics-db",
        "cpu_avg_pct": 78.0, "cpu_p95_pct": 78.0,
        "cpu_max_pct": 78.0, "cpu_min_pct": 41.0,
        "memory_avg_pct": 90.0, "memory_max_pct": 90.0,
        "storage_used_gb": 780.0, "connections_avg": 241.6,
        "observation_window_days": 14,
    },
}

DEMO_SKIP = {
    "brand-new-db": "Only 0/336 expected CPU datapoints found (0% coverage). "
                    "Instance may be new or recently stopped."
}


# ══════════════════════════════════════════════════════════════════════════════
#  PIPELINE STEPS
# ══════════════════════════════════════════════════════════════════════════════

def step1_credentials() -> bool:
    """Ask for credentials; return True if real AWS, False for demo mode."""
    hr()
    print(c("  CostGuard — AWS RDS Cost Optimizer", WHITE, BOLD))
    print(c("  Week 9-10 Demo  ·  Person C — Recommendations & Reports", DIM))
    hr()
    print(c("  Enter your AWS credentials, or press Enter for demo mode.", CYAN))
    print(c("  (Demo mode simulates a real AWS account — no credentials needed)\n", DIM))

    key_id = input(c("  AWS Access Key ID     : ", CYAN)).strip()
    if not key_id:
        print(c("\n  → Running in DEMO MODE with simulated AWS data.\n", YELLOW, BOLD))
        return False

    secret = input(c("  AWS Secret Access Key : ", CYAN)).strip()
    region = input(c("  AWS Region [us-east-1]: ", CYAN)).strip() or "us-east-1"

    os.environ["AWS_ACCESS_KEY_ID"]     = key_id
    os.environ["AWS_SECRET_ACCESS_KEY"] = secret
    os.environ["AWS_DEFAULT_REGION"]    = region
    print(c(f"\n  → Credentials set. Region: {region}\n", GREEN))
    return True


def step2_list_instances(real_aws: bool) -> list[dict]:
    step(2, "Connecting to AWS RDS — listing database instances …")
    spin("Authenticating with AWS …", 1.0)

    if real_aws:
        sys.path.insert(0, str(Path(__file__).parent))
        from src.aws_connector import list_db_instances, AWSConnectionError
        try:
            spin("Calling rds:DescribeDBInstances …", 1.4)
            instances = list_db_instances()
        except AWSConnectionError as e:
            print(c(f"\n  ✖  Could not connect to AWS: {e}\n", RED))
            print(c("  Falling back to demo mode …\n", YELLOW))
            instances = DEMO_INSTANCES
    else:
        spin("Calling rds:DescribeDBInstances …", 1.4)
        instances = DEMO_INSTANCES

    print(f"  {c(len(instances), GREEN, BOLD)} RDS instance(s) found:\n")
    header = f"  {'Instance ID':<26}{'Type':<18}{'Engine':<10}{'Region':<14}{'$/mo'}"
    print(c(header, DIM))
    print(c("  " + "─" * 78, DIM))
    for db in instances:
        az = c(" [Multi-AZ]", BLUE) if db["multi_az"] else ""
        print(f"  {c(db['instance_id'],'', WHITE):<35}"
              f"{db['instance_type']:<18}"
              f"{db['engine']:<10}"
              f"{db['region']:<14}"
              f"${db['monthly_cost']:.2f}{az}")
    return instances


def step3_metrics(instances: list[dict], real_aws: bool) -> dict:
    step(3, "Pulling 14 days of CloudWatch metrics per instance …")

    if real_aws:
        sys.path.insert(0, str(Path(__file__).parent))
        from src.metrics import get_metrics_summary, MetricsCollectionError, InsufficientMetricsError
        metrics_map, skipped = {}, {}
        for db in instances:
            iid = db["instance_id"]
            spin(f"Fetching metrics: {iid} …", 1.0)
            try:
                metrics_map[iid] = get_metrics_summary(iid, db["instance_type"], db["storage_allocated_gb"])
                ok(f"{iid}  CPU avg {metrics_map[iid]['cpu_avg_pct']}%  |  Mem avg {metrics_map[iid]['memory_avg_pct']}%")
            except InsufficientMetricsError as e:
                warn(f"{iid}  →  skipped — {e}")
                skipped[iid] = str(e)
            except MetricsCollectionError as e:
                warn(f"{iid}  →  error — {e}")
                skipped[iid] = str(e)
        return {"metrics": metrics_map, "skipped": skipped}

    # Demo mode
    metrics_map, skipped = {}, {}
    for db in instances:
        iid = db["instance_id"]
        spin(f"Fetching metrics: {iid} …", 0.9)
        if iid in DEMO_SKIP:
            warn(f"{iid}  →  skipped — {DEMO_SKIP[iid]}")
            skipped[iid] = DEMO_SKIP[iid]
        else:
            m = DEMO_METRICS[iid]
            ok(f"{iid:<28} CPU avg {m['cpu_avg_pct']}%  |  Mem avg {m['memory_avg_pct']}%")
            metrics_map[iid] = m

    return {"metrics": metrics_map, "skipped": skipped}


def step4_analysis(instances: list[dict], metrics_data: dict) -> dict:
    step(4, "Analysing usage — applying over-sizing thresholds …")
    spin("Running analysis engine …", 0.8)

    sys.path.insert(0, str(Path(__file__).parent))
    from src.analysis import analyze_instance, insufficient_data_result

    metrics_map = metrics_data["metrics"]
    skipped     = metrics_data["skipped"]
    analyses    = {}

    for db in instances:
        iid = db["instance_id"]
        if iid in skipped:
            analyses[iid] = insufficient_data_result(iid, skipped[iid])
            continue
        if iid not in metrics_map:
            analyses[iid] = insufficient_data_result(iid, "no metrics returned")
            continue
        analyses[iid] = analyze_instance(metrics_map[iid])

    print()
    for iid, a in analyses.items():
        if a["status"] == "oversized":
            print(f"  {c('OVER-SIZED', RED, BOLD):<30} {c(iid, WHITE)}")
        elif a["status"] == "right_sized":
            print(f"  {c('right-sized', GREEN):<30} {c(iid, DIM)}")
        else:
            print(f"  {c('skipped', YELLOW):<30} {c(iid, DIM)}")

    return analyses


def step5_recommendations(instances: list[dict], metrics_data: dict, analyses: dict):
    step(5, "Generating recommendations and calculating savings …")
    spin("Running recommendation engine …", 0.9)

    from src.recommendations import recommend, total_savings
    from src.analysis import OVERSIZED

    metrics_map = metrics_data["metrics"]
    recs = []

    for db in instances:
        iid = db["instance_id"]
        a   = analyses.get(iid, {})
        m   = metrics_map.get(iid)
        if a.get("status") == OVERSIZED and m is not None:
            rec = recommend(db, m, a)
            if rec:
                recs.append(rec)

    if not recs:
        print(c("  No downgrades recommended — all instances are right-sized.\n", GREEN))
        return [], {}

    totals = total_savings(recs)
    print()
    header = f"  {'Instance':<26}{'Current':<18}{'Recommended':<18}{'Save/mo':<12}{'Save/yr':<12}{'Risk'}"
    print(c(header, DIM))
    print(c("  " + "─" * 90, DIM))
    for r in recs:
        risk_col = GREEN if r["risk_level"] == "low" else YELLOW if r["risk_level"] == "medium" else RED
        print(
            f"  {c(r['instance_id'], WHITE):<35}"
            f"{r['current_type']:<18}"
            f"{c(r['recommended_type'], GREEN):<27}"
            f"${r['monthly_savings']:<11.2f}"
            f"${r['annual_savings']:<11.2f}"
            f"{c(r['risk_level'].upper(), risk_col, BOLD)}"
        )

    mo  = f"${totals['monthly']:,.2f}/month"
    yr  = f"${totals['annual']:,.2f}/year"
    print(f"\n  {c('Total potential savings:', BOLD)} "
          f"{c(mo, GREEN, BOLD)} = {c(yr, GREEN, BOLD)}\n")

    return recs, totals


def step6_report(instances, metrics_data, analyses, recs, totals):
    step(6, "Generating HTML report …")
    spin("Rendering Jinja2 template …", 0.8)

    from src.report import generate_html_report

    metrics_map = metrics_data["metrics"]
    # Add None entries for skipped instances
    for db in instances:
        if db["instance_id"] not in metrics_map:
            metrics_map[db["instance_id"]] = None

    out_dir = Path(__file__).parent / "reports"
    out_dir.mkdir(exist_ok=True)
    out_path = str(out_dir / "costguard_report.html")

    path = generate_html_report(instances, metrics_map, analyses, recs, totals or {"monthly": 0, "annual": 0}, output_path=out_path)
    ok(f"Report saved → {c(path, CYAN, BOLD)}")

    print()
    ans = input(c("  Open report in browser? [Y/n]: ", CYAN)).strip().lower()
    if ans != "n":
        webbrowser.open(f"file:///{path.replace(os.sep, '/')}")
        ok("Opened in browser!")


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    try:
        real_aws    = step1_credentials()
        instances   = step2_list_instances(real_aws)
        metrics_data= step3_metrics(instances, real_aws)
        analyses    = step4_analysis(instances, metrics_data)
        recs, totals= step5_recommendations(instances, metrics_data, analyses)
        step6_report(instances, metrics_data, analyses, recs, totals)

        hr()
        print(c("  Done! CostGuard analysis complete.\n", GREEN, BOLD))

    except KeyboardInterrupt:
        print(c("\n\n  Cancelled by user.\n", YELLOW))
        sys.exit(0)


if __name__ == "__main__":
    main()
