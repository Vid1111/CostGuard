import logging
from botocore.exceptions import ClientError, NoCredentialsError, EndpointConnectionError

from src.aws_connector.session import get_rds_client
from src.aws_connector.pricing import estimate_monthly_cost, is_known_instance_type

logger = logging.getLogger(__name__)


class AWSConnectionError(Exception):
    """Raised when we can't reach AWS or the credentials are invalid."""


def _to_db_instance(raw: dict, region: str) -> dict:
    """
    Map a single raw describe_db_instances() entry to our DBInstance
    contract (see docs/architecture.md, section 4).
    """
    instance_type = raw["DBInstanceClass"]
    monthly_cost = estimate_monthly_cost(instance_type, region)

    db_instance = {
        "instance_id": raw["DBInstanceIdentifier"],
        "instance_type": instance_type,
        "engine": raw.get("Engine", "unknown"),
        "region": region,
        "monthly_cost": monthly_cost,
        "storage_allocated_gb": raw.get("AllocatedStorage", 0),
        "multi_az": raw.get("MultiAZ", False),
    }

    if not is_known_instance_type(instance_type):
        logger.warning(
            "Unknown instance type '%s' for %s — pricing not in our table, "
            "monthly_cost defaulted to 0.0. Add it to pricing.py.",
            instance_type,
            db_instance["instance_id"],
        )

    return db_instance


def list_db_instances(region: str = None) -> list[dict]:
    """
    Connect to AWS RDS and return every DB instance in the account
    (current region) as a list of DBInstance dicts.

    Handles pagination automatically — accounts with more than 100
    instances (the per-page limit) are still returned in full.

    Raises AWSConnectionError with a human-readable message on auth
    failure, network failure, or any other AWS-side error, so the
    caller (and eventually the report) can surface something useful
    instead of a raw boto3 traceback.
    """
    try:
        client = get_rds_client()
        resolved_region = region or client.meta.region_name

        instances = []
        paginator = client.get_paginator("describe_db_instances")
        for page in paginator.paginate():
            for raw in page["DBInstances"]:
                instances.append(_to_db_instance(raw, resolved_region))

        logger.info("Found %d RDS instance(s) in %s", len(instances), resolved_region)
        return instances

    except RuntimeError as e:
        # raised by session.get_session() when credentials are missing
        raise AWSConnectionError(str(e)) from e

    except NoCredentialsError as e:
        raise AWSConnectionError(
            "No AWS credentials found. Check your .env file (see SETUP.md)."
        ) from e

    except EndpointConnectionError as e:
        raise AWSConnectionError(
            "Could not reach AWS — check your network connection and region."
        ) from e

    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "Unknown")
        if code in ("AuthFailure", "UnrecognizedClientException", "InvalidClientTokenId"):
            raise AWSConnectionError(
                "AWS rejected the credentials — they may be invalid, expired, "
                "or revoked. Re-check your access key in .env."
            ) from e
        if code == "AccessDenied":
            raise AWSConnectionError(
                "AWS credentials are valid but lack permission to call "
                "rds:DescribeDBInstances. Check the IAM policy (see SETUP.md)."
            ) from e
        raise AWSConnectionError(f"AWS returned an error ({code}): {e}") from e


if __name__ == "__main__":
    # Quick manual check: `python -m src.aws_connector.connector`
    logging.basicConfig(level=logging.INFO)
    try:
        for db in list_db_instances():
            print(db)
    except AWSConnectionError as err:
        print(f"Error: {err}")
