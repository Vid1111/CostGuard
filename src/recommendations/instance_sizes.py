"""
recommendations.instance_sizes
--------------------------------
Size ladders for each RDS instance family, smallest first, with vCPU
counts. Covers the same instance classes as aws_connector.pricing and
metrics.instance_memory so all three tables stay in sync.

RAM comes from metrics.instance_memory rather than being repeated here.
"""

from src.metrics.instance_memory import get_total_memory_gb

_FAMILY_LADDERS = {
    "db.t3": ["db.t3.micro", "db.t3.small", "db.t3.medium", "db.t3.large"],
    "db.r5": ["db.r5.large", "db.r5.xlarge", "db.r5.2xlarge", "db.r5.4xlarge"],
    "db.m5": ["db.m5.large", "db.m5.xlarge", "db.m5.2xlarge"],
}

# t3 classes all have 2 vCPUs (burstable) — stepping down a t3 size
# only shrinks RAM, which the projection math handles naturally
_VCPU_TABLE = {
    "db.t3.micro": 2,
    "db.t3.small": 2,
    "db.t3.medium": 2,
    "db.t3.large": 2,
    "db.r5.large": 2,
    "db.r5.xlarge": 4,
    "db.r5.2xlarge": 8,
    "db.r5.4xlarge": 16,
    "db.m5.large": 2,
    "db.m5.xlarge": 4,
    "db.m5.2xlarge": 8,
}


def _family(instance_type: str) -> str:
    # "db.r5.2xlarge" -> "db.r5"
    return instance_type.rsplit(".", 1)[0]


def smaller_sizes(instance_type: str) -> list[str]:
    """
    Every size in the same family that is smaller than instance_type,
    largest first (i.e. one step down, then two steps down, ...).
    Empty if the type is unknown or already the smallest.
    """
    ladder = _FAMILY_LADDERS.get(_family(instance_type), [])
    if instance_type not in ladder:
        return []
    return list(reversed(ladder[:ladder.index(instance_type)]))


def get_vcpus(instance_type: str) -> int | None:
    return _VCPU_TABLE.get(instance_type)


def get_memory_gb(instance_type: str) -> float | None:
    return get_total_memory_gb(instance_type)
