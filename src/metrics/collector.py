"""
metrics.collector
-------------------
Week 5-6 deliverable: pull real usage metrics from CloudWatch for a
given RDS instance and return a MetricsSummary dict matching the
contract in docs/architecture.md, ready for the Analysis Engine to
compare against the over-sizing thresholds.

This module only reads data (cloudwatch:GetMetricStatistics) — it
never modifies anything in the account.
"""

import logging
from datetime import datetime, timedelta, timezone

from botocore.exceptions import ClientError, NoCredentialsError, EndpointConnectionError

from src.aws_connector.session import get_cloudwatch_client
from src.metrics.instance_memory import get_total_memory_gb

logger = logging.getLogger(__name__)

NAMESPACE = "AWS/RDS"
PERIOD_SECONDS = 3600  # 1-hour buckets
MIN_OBSERVATION_DAYS = 14  # see docs/architecture.md section 5
# below this fraction of expected hourly datapoints, we don't trust the
# average enough to call an instance over- or under-sized
MIN_DATA_COVERAGE = 0.5


class MetricsCollectionError(Exception):
    """Raised when we can't reach CloudWatch or the credentials are invalid."""


class InsufficientMetricsError(Exception):
    """
    Raised when CloudWatch has too little data for a reliable reading
    (e.g. a brand-new instance, or one outside its 14-day minimum
    observation window). Distinct from MetricsCollectionError because
    this isn't a failure to reach AWS — AWS answered fine, there just
    isn't enough history yet to trust the numbers.
    """


def _query_metric(client, metric_name: str, instance_id: str, start, end, stats: list[str]) -> list[dict]:
    """Call get_metric_statistics for one metric and return its datapoints, oldest first."""
    response = client.get_metric_statistics(
        Namespace=NAMESPACE,
        MetricName=metric_name,
        Dimensions=[{"Name": "DBInstanceIdentifier", "Value": instance_id}],
        StartTime=start,
        EndTime=end,
        Period=PERIOD_SECONDS,
        Statistics=stats,
    )
    return sorted(response["Datapoints"], key=lambda dp: dp["Timestamp"])


def _average(datapoints: list[dict], key: str = "Average") -> float | None:
    values = [dp[key] for dp in datapoints if key in dp]
    return round(sum(values) / len(values), 2) if values else None


def _max(datapoints: list[dict], key: str = "Maximum") -> float | None:
    values = [dp[key] for dp in datapoints if key in dp]
    return round(max(values), 2) if values else None


def _min(datapoints: list[dict], key: str = "Minimum") -> float | None:
    values = [dp[key] for dp in datapoints if key in dp]
    return round(min(values), 2) if values else None


def get_metrics_summary(
    instance_id: str,
    instance_type: str,
    allocated_storage_gb: float,
    observation_window_days: int = MIN_OBSERVATION_DAYS,
) -> dict:
    """
    Pull CPU, memory, storage, and connection metrics for one RDS
    instance over the given observation window and return a
    MetricsSummary dict (see docs/architecture.md section 4).

    instance_type and allocated_storage_gb come from the DBInstance
    the AWS Connector already produced for this instance — the
    Metrics Collector doesn't call RDS itself, only CloudWatch.

    Raises:
        InsufficientMetricsError — if observation_window_days is below
            the 14-day minimum, or CloudWatch has too little data to
            trust (e.g. instance is newer than the window).
        MetricsCollectionError — on any AWS/CloudWatch-side failure
            (bad credentials, network issue, access denied, etc).
    """
    if observation_window_days < MIN_OBSERVATION_DAYS:
        raise InsufficientMetricsError(
            f"observation_window_days={observation_window_days} is below the "
            f"{MIN_OBSERVATION_DAYS}-day minimum — results would be unreliable "
            f"(see docs/architecture.md section 5)."
        )

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=observation_window_days)
    expected_datapoints = observation_window_days * 24  # one per hour

    try:
        client = get_cloudwatch_client()

        cpu = _query_metric(client, "CPUUtilization", instance_id, start, end, ["Average", "Maximum"])
        memory = _query_metric(client, "FreeableMemory", instance_id, start, end, ["Average", "Minimum"])
        connections = _query_metric(client, "DatabaseConnections", instance_id, start, end, ["Average"])
        storage = _query_metric(client, "FreeStorageSpace", instance_id, start, end, ["Average"])

    except RuntimeError as e:
        # raised by session.get_session() when credentials are missing
        raise MetricsCollectionError(str(e)) from e

    except NoCredentialsError as e:
        raise MetricsCollectionError(
            "No AWS credentials found. Check your .env file (see SETUP.md)."
        ) from e

    except EndpointConnectionError as e:
        raise MetricsCollectionError(
            "Could not reach AWS — check your network connection and region."
        ) from e

    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "Unknown")
        if code == "AccessDenied":
            raise MetricsCollectionError(
                "AWS credentials are valid but lack permission to call "
                "cloudwatch:GetMetricStatistics. Check the IAM policy (see SETUP.md)."
            ) from e
        raise MetricsCollectionError(f"AWS returned an error ({code}): {e}") from e

    coverage = len(cpu) / expected_datapoints if expected_datapoints else 0
    if not cpu or coverage < MIN_DATA_COVERAGE:
        raise InsufficientMetricsError(
            f"Only {len(cpu)}/{expected_datapoints} expected CPU datapoints found "
            f"for '{instance_id}' over {observation_window_days} days "
            f"({coverage:.0%} coverage). Instance may be new, stopped, or the "
            f"identifier/region may be wrong. Not enough data for a reliable "
            f"over-sizing judgment."
        )

    cpu_avg_pct = _average(cpu, "Average")
    cpu_max_pct = _max(cpu, "Maximum")

    total_memory_gb = get_total_memory_gb(instance_type)
    memory_avg_pct = None
    memory_max_pct = None
    if total_memory_gb and memory:
        total_memory_bytes = total_memory_gb * (1024 ** 3)
        freeable_avg = _average(memory, "Average")
        freeable_min = _min(memory, "Minimum")  # least free memory = most memory used
        if freeable_avg is not None:
            memory_avg_pct = round(100 - (freeable_avg / total_memory_bytes * 100), 2)
        if freeable_min is not None:
            memory_max_pct = round(100 - (freeable_min / total_memory_bytes * 100), 2)
    elif not total_memory_gb:
        logger.warning(
            "Unknown instance type '%s' for %s — can't compute memory %%, "
            "add it to metrics/instance_memory.py.",
            instance_type,
            instance_id,
        )

    storage_used_gb = None
    if storage:
        free_avg_bytes = _average(storage, "Average")
        if free_avg_bytes is not None:
            storage_used_gb = round(allocated_storage_gb - (free_avg_bytes / (1024 ** 3)), 2)

    connections_avg = _average(connections, "Average") or 0.0

    summary = {
        "instance_id": instance_id,
        "cpu_avg_pct": cpu_avg_pct,
        "cpu_max_pct": cpu_max_pct,
        "memory_avg_pct": memory_avg_pct,
        "memory_max_pct": memory_max_pct,
        "storage_used_gb": storage_used_gb,
        "connections_avg": connections_avg,
        "observation_window_days": observation_window_days,
    }

    logger.info(
        "Metrics for %s: cpu_avg=%s%% memory_avg=%s%% (coverage=%.0f%%)",
        instance_id, cpu_avg_pct, memory_avg_pct, coverage * 100,
    )
    return summary


if __name__ == "__main__":
    # Quick manual check against a real/sandbox account:
    #   python -m src.metrics.collector <instance_id> <instance_type> <allocated_storage_gb>
    import sys

    logging.basicConfig(level=logging.INFO)
    if len(sys.argv) != 4:
        print("Usage: python -m src.metrics.collector <instance_id> <instance_type> <allocated_storage_gb>")
        sys.exit(1)

    try:
        result = get_metrics_summary(sys.argv[1], sys.argv[2], float(sys.argv[3]))
        print(result)
    except (MetricsCollectionError, InsufficientMetricsError) as err:
        print(f"Error: {err}")
