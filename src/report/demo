"""
report.demo
-------------
Build a sample HTML report from 12 dummy databases, without an AWS
account. The dummy data goes through the real Analysis Engine and
Recommendation Engine, so the report shows exactly what the tool would
produce for those usage figures.

    python -m src.report.demo                       # reports/costguard-demo.html
    python -m src.report.demo --output demo.html
"""

import argparse
import sys

from src.analysis import analyze_instance, insufficient_data_result
from src.aws_connector.pricing import estimate_monthly_cost
from src.recommendations import build_recommendations, total_savings
from src.report import generate_report

# (id, type, engine, region, multi_az, storage_gb,
#  cpu_avg, cpu_p95, cpu_max, mem_avg, mem_max, storage_used_gb, connections)
# a row with cpu_avg None means "not enough CloudWatch data"
SAMPLE_FLEET = [
    ("orders-prod",        "db.r5.2xlarge", "postgres", "us-east-1", True,  500, 12.0, 18.0, 31.0, 18.0, 24.0, 212.4, 48.0),
    ("analytics-replica",  "db.r5.4xlarge", "postgres", "us-east-1", False, 1000, 6.5, 11.0, 19.0, 14.0, 21.0, 640.0, 9.0),
    ("billing-prod",       "db.r5.xlarge",  "mysql",    "us-east-1", True,  300, 61.0, 82.0, 97.0, 71.0, 83.0, 190.2, 140.0),
    ("catalog-search",     "db.m5.2xlarge", "mysql",    "us-east-1", False, 400, 9.0, 14.0, 22.0, 17.0, 26.0, 155.0, 22.0),
    ("legacy-reports",     "db.m5.xlarge",  "mysql",    "us-east-1", False, 200, 4.0, 7.0, 12.0, 11.0, 14.0, 60.1, 3.0),
    ("staging-api",        "db.t3.large",   "postgres", "us-east-1", False, 100, 8.0, 12.0, 20.0, 20.0, 28.0, 34.0, 6.0),
    ("dev-sandbox",        "db.t3.medium",  "postgres", "us-east-1", False, 50,  3.0, 5.0, 9.0, 18.0, 22.0, 8.2, 1.0),
    ("auth-service",       "db.t3.micro",   "postgres", "us-east-1", False, 20,  6.0, 10.0, 18.0, 22.0, 26.0, 4.0, 12.0),
    ("payments-ledger",    "db.r5.2xlarge", "postgres", "us-east-1", True,  800, 14.0, 41.0, 88.0, 22.0, 31.0, 420.0, 75.0),
    ("events-ingest",      "db.m5.large",   "mysql",    "us-east-1", False, 250, 35.0, 58.0, 74.0, 52.0, 66.0, 201.0, 30.0),
    ("ml-features",        "db.r5.xlarge",  "postgres", "us-east-1", False, 300, 7.0, 12.0, 25.0, None, None, 120.0, 5.0),
    ("brand-new-db",       "db.t3.medium",  "postgres", "us-east-1", False, 50,  None, None, None, None, None, None, None),
]


def sample_pipeline():
    """Return (instances, results) for SAMPLE_FLEET, analysed by the real engine."""
    instances, results = [], []
    for (iid, itype, engine, region, multi_az, storage, cpu_avg, cpu_p95, cpu_max,
         mem_avg, mem_max, used, conns) in SAMPLE_FLEET:
        db = {
            "instance_id": iid, "instance_type": itype, "engine": engine, "region": region,
            "monthly_cost": estimate_monthly_cost(itype, region, multi_az),
            "storage_allocated_gb": storage, "multi_az": multi_az,
        }
        instances.append(db)
        if cpu_avg is None:
            results.append((db, None, insufficient_data_result(
                iid, "only 40/336 expected CPU datapoints (instance is newer than the 14-day window)")))
            continue
        metrics = {
            "instance_id": iid, "cpu_avg_pct": cpu_avg, "cpu_p95_pct": cpu_p95, "cpu_max_pct": cpu_max,
            "cpu_min_pct": 0.5, "memory_avg_pct": mem_avg, "memory_max_pct": mem_max,
            "storage_used_gb": used, "connections_avg": conns, "observation_window_days": 14,
        }
        results.append((db, metrics, analyze_instance(metrics)))
    return instances, results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write a sample CostGuard report from dummy data.")
    parser.add_argument("-o", "--output", default="reports/costguard-demo.html")
    args = parser.parse_args(argv or [])

    instances, results = sample_pipeline()
    recommendations, no_safe = build_recommendations(results)
    path = generate_report(instances, results, recommendations, no_safe, args.output)

    totals = total_savings(recommendations)
    print(f"{len(recommendations)} recommendation(s), "
          f"${totals['monthly']:,.2f}/month = ${totals['annual']:,.2f}/year")
    print(f"Sample report written to {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

