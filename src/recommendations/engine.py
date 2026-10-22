"""
recommendations.engine
------------------------
Week 7-8 deliverable: for an instance the Analysis Engine flagged as
over-sized, pick a cheaper instance type in the same family and work
out the savings. Returns a Recommendation dict (see
docs/architecture.md section 4).

How a target size is chosen:
  Usage is projected onto each smaller size by scaling with the
  capacity ratio — e.g. 15% CPU on 8 vCPUs is ~60% on 2 vCPUs. The
  smallest size whose projected peaks stay under the TARGET_* ceilings
  wins. CPU is sized on its 95th-percentile peak (rare spikes ignored)
  and memory on its true peak.

  Safety net: the single busiest CPU minute we saw must still fit on
  the new size (projected <= 100%), so a rare spike may slow the
  downgraded instance down but never asks for more CPU than it has.

Risk level is how close the projected peak gets to 100%:
  low    — projected peak <= 50%
  medium — projected peak <= 65%
  high   — anything above, up to the ceilings, or the busiest minute
           would land above 90%
Unknown memory usage is never "low" risk, since we couldn't check it.
"""

from src.aws_connector.pricing import estimate_monthly_cost
from src.recommendations.instance_sizes import smaller_sizes, get_vcpus, get_memory_gb

TARGET_CPU_PEAK = 75.0
TARGET_MEMORY_PEAK = 80.0
CPU_SATURATION_LIMIT = 100.0

LOW_RISK_MAX_PEAK = 50.0
MEDIUM_RISK_MAX_PEAK = 65.0
HIGH_RISK_CPU_MAX = 90.0


def _project(usage_pct: float | None, current_capacity, target_capacity) -> float | None:
    """Scale a usage % from the current instance's capacity onto the target's."""
    if usage_pct is None or not current_capacity or not target_capacity:
        return None
    return round(usage_pct * current_capacity / target_capacity, 2)


def _first_known(*values):
    return next((v for v in values if v is not None), None)


def _risk_level(projected_cpu_peak: float, projected_memory_peak: float | None, projected_cpu_max: float) -> str:
    if (projected_cpu_max or 0) > HIGH_RISK_CPU_MAX:
        return "high"
    peak = max(projected_cpu_peak, projected_memory_peak or 0)
    if peak <= LOW_RISK_MAX_PEAK and projected_memory_peak is not None:
        return "low"
    if peak <= MEDIUM_RISK_MAX_PEAK:
        return "medium"
    return "high"


def recommend(db_instance: dict, metrics: dict, analysis: dict) -> dict | None:
    """
    Build a Recommendation for one instance, or return None when there's
    nothing to recommend:
      - the analysis didn't flag it as over-sized,
      - its type isn't in our size/pricing tables,
      - or no smaller size keeps projected peaks under the ceilings
        (e.g. it's already the smallest in its family).
    """
    if not analysis["is_oversized"]:
        return None

    current_type = db_instance["instance_type"]
    current_cost = db_instance["monthly_cost"]
    current_vcpus = get_vcpus(current_type)
    current_memory = get_memory_gb(current_type)
    if not current_cost or not current_vcpus:
        return None

    # peaks drive the sizing; fall back to the next-best figure if one is missing
    cpu_max = _first_known(metrics.get("cpu_max_pct"), metrics.get("cpu_avg_pct"))
    cpu_peak = _first_known(metrics.get("cpu_p95_pct"), cpu_max)
    mem_peak = _first_known(metrics.get("memory_max_pct"), metrics.get("memory_avg_pct"))

    best = None
    for candidate in smaller_sizes(current_type):  # one step down first
        projected_cpu = _project(cpu_peak, current_vcpus, get_vcpus(candidate))
        projected_cpu_max = _project(cpu_max, current_vcpus, get_vcpus(candidate))
        projected_mem = _project(mem_peak, current_memory, get_memory_gb(candidate))

        if projected_cpu is None or projected_cpu > TARGET_CPU_PEAK:
            break
        if projected_cpu_max is not None and projected_cpu_max > CPU_SATURATION_LIMIT:
            break
        if projected_mem is not None and projected_mem > TARGET_MEMORY_PEAK:
            break
        best = (candidate, projected_cpu, projected_cpu_max, projected_mem)  # fits — try one size smaller

    if best is None:
        return None

    recommended_type, projected_cpu, projected_cpu_max, projected_mem = best
    recommended_cost = estimate_monthly_cost(recommended_type, db_instance["region"], db_instance["multi_az"])
    monthly_savings = round(current_cost - recommended_cost, 2)
    if monthly_savings <= 0:
        return None

    return {
        "instance_id": db_instance["instance_id"],
        "current_type": current_type,
        "current_cost": current_cost,
        "recommended_type": recommended_type,
        "recommended_cost": recommended_cost,
        "monthly_savings": monthly_savings,
        "annual_savings": round(monthly_savings * 12, 2),
        "risk_level": _risk_level(projected_cpu, projected_mem, projected_cpu_max),
        "projected_cpu_peak_pct": projected_cpu,
        "projected_cpu_max_pct": projected_cpu_max,
        "projected_memory_peak_pct": projected_mem,
        "reason": analysis["reason"],
    }


def total_savings(recommendations: list[dict]) -> dict:
    """Sum monthly and annual savings across all recommendations."""
    monthly = round(sum(r["monthly_savings"] for r in recommendations), 2)
    return {"monthly": monthly, "annual": round(monthly * 12, 2)}
