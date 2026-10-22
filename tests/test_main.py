"""
End-to-end test of src.main: moto-mocked RDS + CloudWatch, running the
full Connector -> Metrics -> Analysis -> Recommendations pipeline.
"""

import os
import boto3
import pytest
from datetime import datetime, timedelta, timezone
from moto import mock_aws

from src.main import main


@pytest.fixture(autouse=True)
def aws_credentials():
    os.environ["AWS_ACCESS_KEY_ID"] = "testing"
    os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
    os.environ["AWS_DEFAULT_REGION"] = "us-east-1"


def _create_db(instance_id, instance_type):
    boto3.client("rds", region_name="us-east-1").create_db_instance(
        DBInstanceIdentifier=instance_id,
        DBInstanceClass=instance_type,
        Engine="postgres",
        MasterUsername="admin",
        MasterUserPassword="dummy-password-123",
        AllocatedStorage=100,
    )


def _seed_metrics(instance_id, cpu, memory_free_bytes, days=14):
    client = boto3.client("cloudwatch", region_name="us-east-1")
    now = datetime.now(timezone.utc)
    dims = [{"Name": "DBInstanceIdentifier", "Value": instance_id}]
    data = []
    for h in range(days * 24):
        ts = now - timedelta(hours=days * 24 - h)
        data += [
            {"MetricName": "CPUUtilization", "Dimensions": dims, "Timestamp": ts, "Value": cpu, "Unit": "Percent"},
            {"MetricName": "FreeableMemory", "Dimensions": dims, "Timestamp": ts, "Value": memory_free_bytes, "Unit": "Bytes"},
        ]
    for i in range(0, len(data), 1000):
        client.put_metric_data(Namespace="AWS/RDS", MetricData=data[i:i + 1000])


@mock_aws
def test_full_pipeline_recommends_downgrade_and_skips_new_db(capsys):
    gb = 1024 ** 3
    _create_db("idle-orders-db", "db.r5.2xlarge")
    _seed_metrics("idle-orders-db", cpu=15.0, memory_free_bytes=int(64 * 0.85 * gb))  # 15% used

    _create_db("busy-analytics-db", "db.r5.large")
    _seed_metrics("busy-analytics-db", cpu=78.0, memory_free_bytes=int(16 * 0.10 * gb))  # 90% used

    _create_db("brand-new-db", "db.t3.medium")  # no metrics at all

    assert main() == 0
    out = capsys.readouterr().out

    assert "Found 3 RDS instance(s)" in out
    assert "OVER-SIZED" in out
    assert "db.r5.large" in out
    assert "Total potential savings: $525.60/month = $6,307.20/year" in out
    assert "1 instance(s) skipped" in out
