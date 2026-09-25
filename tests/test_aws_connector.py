"""
Tests for src.aws_connector.

Uses moto to mock AWS RDS so these run in CI with no real AWS account
or credentials needed. Pair this with an occasional manual run against
a real sandbox account (see SETUP.md) — mocks prove the code path
works, not that our assumptions about the real API response shape
are correct.
"""

import os
import pytest
import boto3
from moto import mock_aws

from src.aws_connector.connector import list_db_instances, AWSConnectionError
from src.aws_connector.pricing import estimate_monthly_cost, is_known_instance_type


@pytest.fixture(autouse=True)
def aws_credentials():
    """Dummy credentials so boto3 doesn't complain, moto intercepts every call."""
    os.environ["AWS_ACCESS_KEY_ID"] = "testing"
    os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
    os.environ["AWS_DEFAULT_REGION"] = "us-east-1"


@mock_aws
def test_list_db_instances_empty_account():
    """An account with no RDS instances should return an empty list, not error."""
    assert list_db_instances() == []


@mock_aws
def test_list_db_instances_returns_expected_shape():
    """Created instances should come back matching the DBInstance contract."""
    client = boto3.client("rds", region_name="us-east-1")
    client.create_db_instance(
        DBInstanceIdentifier="test-oversized-db",
        DBInstanceClass="db.r5.2xlarge",
        Engine="postgres",
        MasterUsername="admin",
        MasterUserPassword="dummy-password-123",
        AllocatedStorage=500,
        MultiAZ=False,
    )

    instances = list_db_instances()

    assert len(instances) == 1
    db = instances[0]
    assert db["instance_id"] == "test-oversized-db"
    assert db["instance_type"] == "db.r5.2xlarge"
    assert db["engine"] == "postgres"
    assert db["region"] == "us-east-1"
    assert db["storage_allocated_gb"] == 500
    assert db["multi_az"] is False
    assert db["monthly_cost"] > 0  # known instance type, should have a real price


@mock_aws
def test_list_db_instances_handles_multiple_and_pagination_shape():
    client = boto3.client("rds", region_name="us-east-1")
    for i in range(3):
        client.create_db_instance(
            DBInstanceIdentifier=f"db-{i}",
            DBInstanceClass="db.t3.medium",
            Engine="mysql",
            MasterUsername="admin",
            MasterUserPassword="dummy-password-123",
            AllocatedStorage=20,
        )

    instances = list_db_instances()
    assert len(instances) == 3
    assert {db["instance_id"] for db in instances} == {"db-0", "db-1", "db-2"}


@mock_aws
def test_list_db_instances_unknown_type_defaults_cost_to_zero(caplog):
    """
    An instance type not in our pricing table shouldn't crash the
    whole run — it should come back with monthly_cost=0.0 so it's
    visibly wrong rather than silently mis-costed.
    """
    client = boto3.client("rds", region_name="us-east-1")
    client.create_db_instance(
        DBInstanceIdentifier="future-instance-type",
        DBInstanceClass="db.z9.massive",  # not in pricing.py
        Engine="postgres",
        MasterUsername="admin",
        MasterUserPassword="dummy-password-123",
        AllocatedStorage=100,
    )

    instances = list_db_instances()
    assert instances[0]["monthly_cost"] == 0.0


def test_missing_credentials_raise_clear_error(monkeypatch):
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("AWS_SECRET_ACCESS_KEY", raising=False)

    with pytest.raises(AWSConnectionError, match="Missing AWS credentials"):
        list_db_instances()


class TestPricing:
    def test_known_instance_type_in_default_region(self):
        assert estimate_monthly_cost("db.t3.micro", "us-east-1") == 12.41

    def test_known_type_unlisted_region_applies_multiplier(self):
        cost = estimate_monthly_cost("db.t3.micro", "eu-west-1")
        assert cost == round(12.41 * 1.10, 2)

    def test_unknown_instance_type_returns_zero(self):
        assert estimate_monthly_cost("db.made.up", "us-east-1") == 0.0

    def test_is_known_instance_type(self):
        assert is_known_instance_type("db.r5.2xlarge") is True
        assert is_known_instance_type("db.made.up") is False
