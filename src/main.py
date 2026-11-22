"""
CostGuard — Week 9-10 checkpoint

Connects to AWS, lists every RDS instance, pulls real CloudWatch usage
metrics (CPU, memory, storage, connections) for each one, flags the
over-sized instances, recommends a cheaper instance type with the
monthly/annual savings, and writes an HTML report.

Usage:
    python -m src.main                          # report in reports/
    python -m src.main --output my-report.html  # report at a chosen path
    python -m src.main --no-report              # console output only
"""

import argparse
import logging
import sys

from src.aws_connector import list_db_instances, AWSConnectionError
from src.metrics import get_metrics_summary, MetricsCollectionError, InsufficientMetricsError
from src.metrics.collector import MIN_OBSERVATION_DAYS
from src.analysis import analyze_instance, insufficient_data_result, OVERSIZED, INSUFFICIENT_DATA
from src.recommendations import build_recommendations, total_savings
from src.report import generate_report, ReportGenerationError


def _pct(value) -> str:
    return f"{value}%" if value is not None else "n/a"


def _money(value) -> str:
    return f"${value:,.2f}"


def _print_instance_table(instances: list[dict]) -> None:
    print(f"\nFound {len(instances)} RDS instance(s):\n")
    print(f"{'Instance ID':<25}{'Type':<18}{'Engine':<12}{'Region':<15}{'Multi-AZ':<10}{'Storage (GB)':<14}{'Est. $/mo':<10}")
    print("-" * 104)
    for db in instances:
        print(
            f"{db['instance_id']:<25}"
            f"{db['instance_type']:<18}"
            f"{db['engine']:<12}"
            f"{db['region']:<15}"
            f"{'yes' if db['multi_az'] else 'no':<10}"
            f"{db['storage_allocated_gb']:<14}"
            f"{_money(db['monthly_cost']):<10}"
        )
    print()


def _collect_and_analyze(instances: list[dict]) -> list[tuple[dict, dict | None, dict]]:
    """
    Fetch metrics for each instance and run the Analysis Engine on them.
    Returns (db_instance, metrics_or_None, analysis_result) per instance;
    metrics is None when CloudWatch had too little data or errored.
    """
    results = []
    for db in instances:
        try:
            metrics = get_metrics_summary(
                instance_id=db["instance_id"],
                instance_type=db["instance_type"],
                allocated_storage_gb=db["storage_allocated_gb"],
            )
            results.append((db, metrics, analyze_instance(metrics)))
        except InsufficientMetricsError as e:
            results.append((db, None, insufficient_data_result(db["instance_id"], f"skipped — {e}")))
        except MetricsCollectionError as e:
            results.append((db, None, insufficient_data_result(db["instance_id"], f"error — {e}")))
    return results


def _print_metrics_table(results) -> None:
    print(f"Usage metrics (last {MIN_OBSERVATION_DAYS} days):\n")
    header = (
        f"{'Instance ID':<25}{'CPU avg':<10}{'CPU p95':<10}{'CPU max':<10}{'CPU min':<10}"
        f"{'Mem avg':<10}{'Mem max':<10}{'Connections':<13}{'Storage used':<14}{'Verdict':<12}"
    )
    print(header)
    print("-" * len(header))

    for db, m, analysis in results:
        instance_id = db["instance_id"]
        if m is None:
            print(f"{instance_id:<25}  ({analysis['reason']})")
            continue
        storage = f"{m['storage_used_gb']}GB" if m["storage_used_gb"] is not None else "n/a"
        verdict = "OVER-SIZED" if analysis["status"] == OVERSIZED else "ok"
        print(
            f"{instance_id:<25}{_pct(m['cpu_avg_pct']):<10}{_pct(m['cpu_p95_pct']):<10}{_pct(m['cpu_max_pct']):<10}{_pct(m['cpu_min_pct']):<10}"
            f"{_pct(m['memory_avg_pct']):<10}{_pct(m['memory_max_pct']):<10}{m['connections_avg']:<13}{storage:<14}{verdict:<12}"
        )
    print()


def _print_recommendations(results, recommendations: list[dict], no_safe_downgrade: list[str]) -> None:
    skipped = sum(1 for _, _, a in results if a["status"] == INSUFFICIENT_DATA)

    if not recommendations:
        print("No downgrade recommendations — every instance with enough data is right-sized.\n")
    else:
        print("Recommendations:\n")
        header = f"{'Instance ID':<25}{'Current':<16}{'$/mo':<11}{'Recommended':<16}{'$/mo':<11}{'Save $/mo':<12}{'Save $/yr':<12}{'Risk':<8}"
        print(header)
        print("-" * len(header))
        for r in recommendations:
            print(
                f"{r['instance_id']:<25}{r['current_type']:<16}{_money(r['current_cost']):<11}"
                f"{r['recommended_type']:<16}{_money(r['recommended_cost']):<11}"
                f"{_money(r['monthly_savings']):<12}{_money(r['annual_savings']):<12}{r['risk_level']:<8}"
            )
        totals = total_savings(recommendations)
        print(f"\nTotal potential savings: ${totals['monthly']:,.2f}/month = ${totals['annual']:,.2f}/year\n")

    if no_safe_downgrade:
        print(f"Over-sized but no safe smaller size in its family: {', '.join(no_safe_downgrade)}")
    if skipped:
        print(f"{skipped} instance(s) skipped — not enough CloudWatch data to judge.")
    print()


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="costguard", description="Find over-sized AWS RDS databases.")
    out = parser.add_mutually_exclusive_group()
    out.add_argument("-o", "--output", metavar="PATH",
                     help="where to write the HTML report (default: reports/costguard-report-<timestamp>.html)")
    out.add_argument("--no-report", action="store_true", help="print to the console only")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or [])
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")

    try:
        instances = list_db_instances()
    except AWSConnectionError as e:
        print(f"\n Could not connect to AWS: {e}\n")
        return 1

    if not instances:
        print("\nNo RDS instances found in this account/region.\n")
        return 0

    _print_instance_table(instances)
    results = _collect_and_analyze(instances)
    _print_metrics_table(results)
    recommendations, no_safe_downgrade = build_recommendations(results)
    _print_recommendations(results, recommendations, no_safe_downgrade)

    if not args.no_report:
        try:
            path = generate_report(instances, results, recommendations, no_safe_downgrade, args.output)
        except ReportGenerationError as e:
            print(f"Could not create the HTML report: {e}\n")
            return 1
        print(f"HTML report written to {path}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
