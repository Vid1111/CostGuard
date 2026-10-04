"""
CostGuard — Week 5-6 checkpoint

Connects to AWS, lists every RDS instance, then pulls real CloudWatch
usage metrics (CPU, memory, storage, connections) for each one. This
is the "Can show usage metrics for each database" deliverable for
weeks 5-6, built on top of the Week 3-4 AWS Connector.

Usage:
    python -m src.main
"""

import logging
import sys

from src.aws_connector import list_db_instances, AWSConnectionError
from src.metrics import get_metrics_summary, MetricsCollectionError, InsufficientMetricsError


def _print_instance_table(instances: list[dict]) -> None:
    print(f"\nFound {len(instances)} RDS instance(s):\n")
    print(f"{'Instance ID':<25}{'Type':<18}{'Engine':<12}{'Region':<15}{'Storage (GB)':<14}{'Est. $/mo':<10}")
    print("-" * 94)
    for db in instances:
        print(
            f"{db['instance_id']:<25}"
            f"{db['instance_type']:<18}"
            f"{db['engine']:<12}"
            f"{db['region']:<15}"
            f"{db['storage_allocated_gb']:<14}"
            f"${db['monthly_cost']:<9}"
        )
    print()


def _print_metrics_table(instances: list[dict]) -> None:
    print(f"Usage metrics (last {14} days):\n")
    header = f"{'Instance ID':<25}{'CPU avg':<10}{'CPU max':<10}{'Mem avg':<10}{'Mem max':<10}{'Connections':<13}{'Storage used':<13}"
    print(header)
    print("-" * len(header))

    for db in instances:
        instance_id = db["instance_id"]
        try:
            m = get_metrics_summary(
                instance_id=instance_id,
                instance_type=db["instance_type"],
                allocated_storage_gb=db["storage_allocated_gb"],
            )
            cpu_avg = f"{m['cpu_avg_pct']}%" if m["cpu_avg_pct"] is not None else "n/a"
            cpu_max = f"{m['cpu_max_pct']}%" if m["cpu_max_pct"] is not None else "n/a"
            mem_avg = f"{m['memory_avg_pct']}%" if m["memory_avg_pct"] is not None else "n/a"
            mem_max = f"{m['memory_max_pct']}%" if m["memory_max_pct"] is not None else "n/a"
            storage = f"{m['storage_used_gb']}GB" if m["storage_used_gb"] is not None else "n/a"

            print(f"{instance_id:<25}{cpu_avg:<10}{cpu_max:<10}{mem_avg:<10}{mem_max:<10}{m['connections_avg']:<13}{storage:<13}")

        except InsufficientMetricsError as e:
            print(f"{instance_id:<25}  (skipped — {e})")
        except MetricsCollectionError as e:
            print(f"{instance_id:<25}  (error — {e})")
    print()


def main() -> int:
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
    _print_metrics_table(instances)
    return 0


if __name__ == "__main__":
    sys.exit(main())
