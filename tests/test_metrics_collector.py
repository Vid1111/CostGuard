"""
Tests for src.metrics.collector.

Uses moto to mock CloudWatch. Since moto doesn't generate realistic
RDS metrics on its own, each test manually seeds datapoints with
put_metric_data before calling get_metrics_summary() — this lets us
assert the aggregation math (averages, percentages, coverage checks)
is actually correct, not just that the code runs without crashing.
"""

import os
import pytest
import boto3
from datetime import datetime, timedelta, timezone
from moto import mock_aws

from src.metrics.collector import (
    get_metrics_summary,
    MetricsCollectionError,
    InsufficientMetricsError,
    MIN_OBSERVATION_DAYS,
)
from src.metrics.instance_memory import get_total_memory_gb, is_known_instance_type


@pytest.fixture(autouse=True)
def aws_credentials():
    os.environ["AWS_ACCESS_KEY_ID"] = "testing"
    os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
    os.environ["AWS_DEFAULT_REGION"] = "us-east-1"


def _seed_full_history(instance_id: str, cpu_values, memory_free_bytes, connections, days=14):
    """
    Push one hourly datapoint per metric, per hour, for `days` days,
    so a test instance has full (100%) CloudWatch coverage — matching
    what a real, continuously-running instance would have.
    """
    client = boto3.client("cloudwatch", region_name="us-east-1")
    now = datetime.now(timezone.utc)
    hours = days * 24

    metric_data = []
    for h in range(hours):
        ts = now - timedelta(hours=hours - h)
        metric_data.append({
            "MetricName": "CPUUtilization",
            "Dimensions": [{"Name": "DBInstanceIdentifier", "Value": instance_id}],
            "Timestamp": ts, "Value": cpu_values, "Unit": "Percent",
        })
        metric_data.append({
            "MetricName": "FreeableMemory",
            "Dimensions": [{"Name": "DBInstanceIdentifier", "Value": instance_id}],
            "Timestamp": ts, "Value": memory_free_bytes, "Unit": "Bytes",
        })
        metric_data.append({
            "MetricName": "DatabaseConnections",
            "Dimensions": [{"Name": "DBInstanceIdentifier", "Value": instance_id}],
            "Timestamp": ts, "Value": connections, "Unit": "Count",
        })
        metric_data.append({
            "MetricName": "FreeStorageSpace",
            "Dimensions": [{"Name": "DBInstanceIdentifier", "Value": instance_id}],
            "Timestamp": ts, "Value": 400 * (1024 ** 3), "Unit": "Bytes",  # 400GB free
        })

    # put_metric_data caps at 1000 entries/call
    for i in range(0, len(metric_data), 1000):
        client.put_metric_data(Namespace="AWS/RDS", MetricData=metric_data[i:i + 1000])


@mock_aws
def test_oversized_instance_shows_low_utilization():
    """
    Mirrors the real-example scenario from the project brief: an
    instance using only a small fraction of its provisioned capacity.
    """
    # db.r5.2xlarge has 64GB RAM; freeing ~54.4GB means ~15% memory used
    _seed_full_history(
        "prod-orders-db",
        cpu_values=15.0,
        memory_free_bytes=int(64 * 0.85 * (1024 ** 3)),
        connections=12,
    )

    summary = get_metrics_summary("prod-orders-db", "db.r5.2xlarge", allocated_storage_gb=500)

    assert summary["instance_id"] == "prod-orders-db"
    assert summary["cpu_avg_pct"] == 15.0
    assert summary["cpu_max_pct"] == 15.0
    assert 14.0 <= summary["memory_avg_pct"] <= 16.0
    assert summary["connections_avg"] == 12.0
    assert summary["observation_window_days"] == 14
    assert summary["storage_used_gb"] == 100.0  # 500 allocated - 400 free


@mock_aws
def test_healthy_instance_shows_high_utilization():
    _seed_full_history(
        "busy-analytics-db",
        cpu_values=78.0,
        memory_free_bytes=int(16 * 0.10 * (1024 ** 3)),  # only 10% free
        connections=140,
    )

    summary = get_metrics_summary("busy-analytics-db", "db.r5.large", allocated_storage_gb=200)

    assert summary["cpu_avg_pct"] == 78.0
    assert summary["memory_avg_pct"] >= 85.0


@mock_aws
def test_unknown_instance_type_skips_memory_percentage():
    """
    An instance type not in instance_memory.py should still return
    CPU/connections/storage — memory_avg_pct should be None rather
    than a misleading guess or a crash.
    """
    assert is_known_instance_type("db.future.massive") is False
    _seed_full_history("weird-instance", cpu_values=50.0, memory_free_bytes=1024, connections=5)

    summary = get_metrics_summary("weird-instance", "db.future.massive", allocated_storage_gb=100)

    assert summary["cpu_avg_pct"] == 50.0
    assert summary["memory_avg_pct"] is None
    assert summary["memory_max_pct"] is None


@mock_aws
def test_no_data_raises_insufficient_metrics_error():
    """An instance CloudWatch has never heard of should fail loudly, not return zeros."""
    with pytest.raises(InsufficientMetricsError, match="Not enough data"):
        get_metrics_summary("nonexistent-db", "db.t3.medium", allocated_storage_gb=20)


@mock_aws
def test_partial_coverage_below_threshold_raises_insufficient_metrics_error():
    """
    Only a few hours of data (e.g. a database created a day ago) should
    not be enough to trust for a 14-day judgment.
    """
    client = boto3.client("cloudwatch", region_name="us-east-1")
    now = datetime.now(timezone.utc)
    # only 5 hours of data, nowhere near the ~336 hours expected for 14 days
    for h in range(5):
        client.put_metric_data(
            Namespace="AWS/RDS",
            MetricData=[{
                "MetricName": "CPUUtilization",
                "Dimensions": [{"Name": "DBInstanceIdentifier", "Value": "brand-new-db"}],
                "Timestamp": now - timedelta(hours=5 - h),
                "Value": 10.0,
                "Unit": "Percent",
            }],
        )

    with pytest.raises(InsufficientMetricsError, match="coverage"):
        get_metrics_summary("brand-new-db", "db.t3.medium", allocated_storage_gb=20)


def test_window_below_minimum_rejected_before_calling_aws():
    """The 14-day minimum is enforced before any AWS call is made at all."""
    with pytest.raises(InsufficientMetricsError, match="below the 14-day minimum"):
        get_metrics_summary("any-db", "db.t3.medium", allocated_storage_gb=20, observation_window_days=3)


def test_missing_credentials_raise_clear_error(monkeypatch):
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("AWS_SECRET_ACCESS_KEY", raising=False)

    with pytest.raises(MetricsCollectionError, match="Missing AWS credentials"):
        get_metrics_summary("any-db", "db.t3.medium", allocated_storage_gb=20)


class TestInstanceMemory:
    def test_known_type_returns_gb(self):
        assert get_total_memory_gb("db.r5.2xlarge") == 64

    def test_unknown_type_returns_none(self):
        assert get_total_memory_gb("db.made.up") is None

    def test_is_known_instance_type(self):
        assert is_known_instance_type("db.t3.micro") is True
        assert is_known_instance_type("db.made.up") is False
