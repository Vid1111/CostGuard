# monthly cost in USD, keyed by (instance_type, region)
# falls back to the "us-east-1" price if the exact region isn't listed
_PRICING_TABLE = {
    ("db.t3.micro", "us-east-1"): 12.41,
    ("db.t3.small", "us-east-1"): 24.82,
    ("db.t3.medium", "us-east-1"): 49.64,
    ("db.t3.large", "us-east-1"): 99.28,
    ("db.r5.large", "us-east-1"): 175.20,
    ("db.r5.xlarge", "us-east-1"): 350.40,
    ("db.r5.2xlarge", "us-east-1"): 700.80,
    ("db.r5.4xlarge", "us-east-1"): 1401.60,
    ("db.m5.large", "us-east-1"): 140.16,
    ("db.m5.xlarge", "us-east-1"): 280.32,
    ("db.m5.2xlarge", "us-east-1"): 560.64,
}

# simple regional multiplier applied when a region isn't in the table
# directly (rough approximation, not billing-accurate)
_REGION_MULTIPLIER = {
    "us-east-1": 1.00,
    "us-west-2": 1.00,
    "eu-west-1": 1.10,
    "eu-central-1": 1.12,
    "ap-southeast-1": 1.15,
}


def estimate_monthly_cost(instance_type: str, region: str) -> float:
    """
    Look up (or estimate) the on-demand monthly cost for an RDS
    instance type in a given region.

    Falls back to the us-east-1 price adjusted by a rough regional
    multiplier if the exact (type, region) pair isn't in the table,
    and to 0.0 (with the caller expected to flag it) if the instance
    type itself is unknown.
    """
    key = (instance_type, region)
    if key in _PRICING_TABLE:
        return _PRICING_TABLE[key]

    base = _PRICING_TABLE.get((instance_type, "us-east-1"))
    if base is None:
        return 0.0  # unknown instance type — caller should flag this instance

    multiplier = _REGION_MULTIPLIER.get(region, 1.10)
    return round(base * multiplier, 2)


def is_known_instance_type(instance_type: str) -> bool:
    return any(t == instance_type for t, _ in _PRICING_TABLE)
