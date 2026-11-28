"""
End-to-end tests of src.main: moto-mocked RDS + CloudWatch, running the
full Connector -> Metrics -> Analysis -> Recommendations -> Report
pipeline exactly as `python -m src.main` does.
"""

import os
import boto3
import pytest
from datetime import datetime, timedelta, timezone
from moto import mock_aws

from src.main import main

GB = 1024 ** 3

# total RAM per type, to turn "% memory used" into FreeableMemory bytes
RAM_GB = {
    "db.t3.micro": 1, "db.t3.medium": 4, "db.t3.large": 8,
    "db.r5.large": 16, "db.r5.xlarge": 32, "db.r5.2xlarge": 64,
    "db.m5.large": 8, "db.m5.xlarge": 16, "db.m5.2xlarge": 32,
}


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


def _add_db(instance_id, instance_type, cpu=None, memory_used_pct=None):
    """Create a database; with cpu=None it gets no CloudWatch history at all."""
    _create_db(instance_id, instance_type)
    if cpu is not None:
        free = int(RAM_GB[instance_type] * (100 - memory_used_pct) / 100 * GB)
        _seed_metrics(instance_id, cpu=cpu, memory_free_bytes=free)


@mock_aws
def test_full_pipeline_recommends_downgrade_and_skips_new_db(capsys, tmp_path):
    _add_db("idle-orders-db", "db.r5.2xlarge", cpu=15.0, memory_used_pct=15)
    _add_db("busy-analytics-db", "db.r5.large", cpu=78.0, memory_used_pct=90)
    _add_db("brand-new-db", "db.t3.medium")  # no metrics at all

    report = tmp_path / "report.html"
    assert main(["--output", str(report)]) == 0
    out = capsys.readouterr().out

    assert "Found 3 RDS instance(s)" in out
    assert "OVER-SIZED" in out
    assert "db.r5.large" in out
    assert "Total potential savings: $525.60/month = $6,307.20/year" in out
    assert "1 instance(s) skipped" in out
    assert f"HTML report written to {report}" in out

    html = report.read_text(encoding="utf-8")
    assert "idle-orders-db" in html
    assert "$525.60" in html and "$6,307.20" in html
    assert "brand-new-db</b> was skipped" in html


@mock_aws
def test_twelve_database_fleet(capsys, tmp_path):
    """
    Week 11 asks for a run against 10+ databases. Every expected number
    below is worked out by hand from pricing.py and the sizing rules.
    """
    # over-sized, with a safe smaller size
    _add_db("orders-prod", "db.r5.2xlarge", cpu=15.0, memory_used_pct=15)    # -> r5.large, saves 700.80 - 175.20 = 525.60
    _add_db("catalog-search", "db.m5.2xlarge", cpu=9.0, memory_used_pct=17)  # -> m5.large, saves 560.64 - 140.16 = 420.48
    _add_db("legacy-reports", "db.m5.xlarge", cpu=4.0, memory_used_pct=11)   # -> m5.large, saves 280.32 - 140.16 = 140.16
    _add_db("staging-api", "db.t3.large", cpu=8.0, memory_used_pct=20)       # -> t3.small (80% mem), saves 99.28 - 24.82 = 74.46
    _add_db("dev-sandbox", "db.t3.medium", cpu=3.0, memory_used_pct=18)      # -> t3.micro (72% mem), saves 49.64 - 12.41 = 37.23
    # over-sized, but already the smallest size
    _add_db("auth-service", "db.t3.micro", cpu=6.0, memory_used_pct=22)
    # right-sized
    _add_db("busy-analytics", "db.r5.large", cpu=78.0, memory_used_pct=90)
    _add_db("billing-prod", "db.r5.xlarge", cpu=61.0, memory_used_pct=71)
    _add_db("events-ingest", "db.m5.large", cpu=35.0, memory_used_pct=52)
    _add_db("ml-features", "db.r5.xlarge", cpu=10.0, memory_used_pct=45)     # low CPU but busy memory
    # too new to judge
    _add_db("new-db-1", "db.t3.medium")
    _add_db("new-db-2", "db.r5.large")

    report = tmp_path / "fleet.html"
    assert main(["--output", str(report)]) == 0
    out = capsys.readouterr().out

    assert "Found 12 RDS instance(s)" in out
    # 525.60 + 420.48 + 140.16 + 74.46 + 37.23 = 1197.93
    assert "Total potential savings: $1,197.93/month = $14,375.16/year" in out
    assert "Over-sized but no safe smaller size in its family: auth-service" in out
    assert "2 instance(s) skipped" in out

    # recommendations are listed biggest saving first
    rec_section = out[out.index("Recommendations:"):]
    positions = [rec_section.index(name) for name in
                 ("orders-prod", "catalog-search", "legacy-reports", "staging-api", "dev-sandbox")]
    assert positions == sorted(positions)

    html = report.read_text(encoding="utf-8")
    assert "$1,197.93" in html
    assert "5 over-sized" not in html  # auth-service is over-sized too
    assert "6 over-sized &middot; 4 right-sized &middot; 2 skipped" in html


@mock_aws
def test_no_report_flag_skips_the_html(capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _add_db("idle-orders-db", "db.r5.2xlarge", cpu=15.0, memory_used_pct=15)

    assert main(["--no-report"]) == 0

    assert "HTML report written" not in capsys.readouterr().out
    assert not (tmp_path / "reports").exists()


@mock_aws
def test_default_report_goes_in_reports_folder(capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _add_db("idle-orders-db", "db.r5.2xlarge", cpu=15.0, memory_used_pct=15)

    assert main([]) == 0

    reports = list((tmp_path / "reports").glob("costguard-report-*.html"))
    assert len(reports) == 1


@mock_aws
def test_report_write_failure_returns_error_code(capsys, tmp_path):
    _add_db("idle-orders-db", "db.r5.2xlarge", cpu=15.0, memory_used_pct=15)

    assert main(["--output", str(tmp_path)]) == 1  # a folder, not a file
    assert "Could not write the HTML report" in capsys.readouterr().out


def test_missing_credentials_returns_error_code(capsys, monkeypatch):
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("AWS_SECRET_ACCESS_KEY", raising=False)

    assert main(["--no-report"]) == 1
    assert "Could not connect to AWS" in capsys.readouterr().out
