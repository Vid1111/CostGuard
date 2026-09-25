import logging
import sys

from src.aws_connector import list_db_instances, AWSConnectionError


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    try:
        instances = list_db_instances()
    except AWSConnectionError as e:
        print(f"\n Could not connect to AWS: {e}\n")
        return 1

    if not instances:
        print("\nNo RDS instances found in this account/region.\n")
        return 0

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
    return 0


if __name__ == "__main__":
    sys.exit(main())
