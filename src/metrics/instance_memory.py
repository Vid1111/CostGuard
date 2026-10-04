"""
metrics.instance_memory
-------------------------
CloudWatch reports FreeableMemory in raw bytes, not a percentage —
to turn that into "this instance is using X% of its memory" we need
to know how much total RAM each instance class actually has.

Like aws_connector.pricing, this is a static table for V1 rather than
a live lookup, covering the same instance classes used there so the
two tables stay in sync. AWS doesn't expose total instance RAM through
any describe_* API call, so hardcoding it is the standard approach
(the AWS CLI/console doesn't do anything smarter than this either).

TODO (V2): consider the boto3 `ec2.describe_instance_types`-style
approach if AWS ever exposes an equivalent for RDS instance classes.
"""

# total RAM in GB, keyed by instance type (same across all regions —
# unlike price, hardware spec doesn't change by region)
_MEMORY_TABLE_GB = {
    "db.t3.micro": 1,
    "db.t3.small": 2,
    "db.t3.medium": 4,
    "db.t3.large": 8,
    "db.r5.large": 16,
    "db.r5.xlarge": 32,
    "db.r5.2xlarge": 64,
    "db.r5.4xlarge": 128,
    "db.m5.large": 8,
    "db.m5.xlarge": 16,
    "db.m5.2xlarge": 32,
}


def get_total_memory_gb(instance_type: str) -> float | None:
    """
    Return total RAM (GB) for a known instance type, or None if the
    instance type isn't in our table — callers should treat None as
    "can't compute a memory percentage for this instance" rather than
    guessing or defaulting to 0, which would misleadingly look like
    100% memory usage.
    """
    return _MEMORY_TABLE_GB.get(instance_type)


def is_known_instance_type(instance_type: str) -> bool:
    return instance_type in _MEMORY_TABLE_GB
