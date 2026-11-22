"""
recommendations.engine
------------------------
Week 7-8 deliverable (Person C): for an instance the Analysis Engine
flagged as over-sized, pick a cheaper instance type in the same family
and work out the savings. Returns a Recommendation dict (see
docs/architecture.md section 4).

How a target size is chosen:
  Usage is projected onto each smaller size by scaling with the
  capacity ratio, e.g. 15% CPU on 8 vCPUs is ~60% on 2 vCPUs. The
  smallest size whose projected peaks stay under the TARGET_* ceilings
  wins.

  Safety nets:
    * The single busiest CPU minute we saw must still fit on the new
      size (projected <= 100%), so a rare spike may slow the
      downgraded instance down but never asks for more CPU than it has.
    * A step that only shrinks RAM (vCPUs stay the same, e.g. every
      t3 size has 2 vCPUs) is only taken when memory usage is known.
      Without memory data there is no evidence that step is safe.

Risk level is how close the projected peak gets to 100%:
  low    - projected peak <= 50% (and memory usage is known)
  medium - projected peak <= 65%
  high   - anything above, up to the ceilings, or the busiest minute
           would land above 90%

Every function here is pure (no AWS calls), so it is fully unit-tested
with plain dicts.
"""

from src.aws_connector.pricing import estimate_monthly_cost
from src.recommendations.instance_sizes import smaller_sizes, get_vcpus, get_memory_gb

# sizing ceilings on the recommended instance
TARGET_CPU_PEAK = 75.0          # projected CPU p95
TARGET_MEMORY_PEAK = 80.0       # projected memory peak
CPU_SATURATION_LIMIT = 100.0    # projected busiest CPU minute

# risk bands
LOW_RISK_MAX_PEAK = 50.0
MEDIUM_RISK_MAX_PEAK = 65.0
HIGH_RISK_CPU_MAX = 90.0

RISK_LEVELS = ("low", "medium", "high")
MONTHS_PER_YEAR = 12


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _project(usage_pct: float | None, current_capacity, target_capacity) -> float | None:
    """Scale a usage % from the current instance's capacity onto the target's."""
    if usage_pct is None or not current_capacity or not target_capacity:
        return None
    return round(usage_pct * current_capacity / target_capacity, 2)


def _first_known(*values):
    """First value that isn't None, or None if all are."""
    return next((v for v in values if v is not None), None)


def _risk(projected_cpu_peak: float, projected_memory_peak: float | None,
          projected_cpu_max: float | None) -> tuple[str, str]:
    """
    Return (risk_level, plain-English reason) for a projected workload
    on the recommended size.
    """
    if projected_cpu_max is not None and projected_cpu_max > HIGH_RISK_CPU_MAX:
        return "high", (f"busiest CPU minute would reach {projected_cpu_max:g}% "
                        f"on the new size (above {HIGH_RISK_CPU_MAX:g}%)")

    peak = max(projected_cpu_peak, projected_memory_peak or 0)
    if projected_memory_peak is None:
        resource = "CPU"
    else:
        resource = "memory" if projected_memory_peak >= projected_cpu_peak else "CPU"

    if peak <= LOW_RISK_MAX_PEAK and projected_memory_peak is not None:
        return "low", f"projected {resource} peak {peak:g}% leaves plenty of headroom"
    if peak <= MEDIUM_RISK_MAX_PEAK:
        reason = f"projected {resource} peak {peak:g}%"
        if projected_memory_peak is None:
            reason += "; memory usage unknown, so never rated low"
        return "medium", reason
    return "high", f"projected {resource} peak {peak:g}% leaves little headroom"


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------

def recommend(db_instance: dict, metrics: dict, analysis: dict) -> dict | None:
    """
    Build a Recommendation for one instance, or return None when there's
    nothing to recommend:
      - the analysis didn't flag it as over-sized,
      - its type isn't in our size/pricing tables,
      - or no smaller size keeps projected peaks under the ceilings
        (e.g. it's already the smallest in its family).
    """
    if not analysis.get("is_oversized") or not metrics:
        return None

    current_type = db_instance["instance_type"]
    current_cost = db_instance["monthly_cost"]
    current_vcpus = get_vcpus(current_type)
    current_memory = get_memory_gb(current_type)
    if not current_cost or not current_vcpus:
        return None

    # peaks drive the sizing; fall back to the next-best figure if one is missing
    cpu_max = _first_known(metrics.get("cpu_max_pct"), metrics.get("cpu_p95_pct"), metrics.get("cpu_avg_pct"))
    cpu_peak = _first_known(metrics.get("cpu_p95_pct"), cpu_max)
    mem_peak = _first_known(metrics.get("memory_max_pct"), metrics.get("memory_avg_pct"))

    best = None
    for candidate in smaller_sizes(current_type):  # one step down first
        cand_vcpus = get_vcpus(candidate)
        projected_cpu = _project(cpu_peak, current_vcpus, cand_vcpus)
        projected_cpu_max = _project(cpu_max, current_vcpus, cand_vcpus)
        projected_mem = _project(mem_peak, current_memory, get_memory_gb(candidate))

        if projected_cpu is None or projected_cpu > TARGET_CPU_PEAK:
            break
        if projected_cpu_max is not None and projected_cpu_max > CPU_SATURATION_LIMIT:
            break
        if projected_mem is None and cand_vcpus == current_vcpus:
            break  # RAM-only step with no memory data: nothing proves it's safe
        if projected_mem is not None and projected_mem > TARGET_MEMORY_PEAK:
            break
        best = (candidate, projected_cpu, projected_cpu_max, projected_mem)  # fits, try one smaller

    if best is None:
        return None

    recommended_type, projected_cpu, projected_cpu_max, projected_mem = best
    recommended_cost = estimate_monthly_cost(recommended_type, db_instance["region"], db_instance["multi_az"])
    if not recommended_cost:
        return None  # target not priced; never report a fake 100% saving

    monthly_savings = round(current_cost - recommended_cost, 2)
    if monthly_savings <= 0:
        return None

    risk_level, risk_reason = _risk(projected_cpu, projected_mem, projected_cpu_max)

    return {
        "instance_id": db_instance["instance_id"],
        "current_type": current_type,
        "current_cost": current_cost,
        "current_vcpus": current_vcpus,
        "current_memory_gb": current_memory,
        "recommended_type": recommended_type,
        "recommended_cost": recommended_cost,
        "recommended_vcpus": get_vcpus(recommended_type),
        "recommended_memory_gb": get_memory_gb(recommended_type),
        "monthly_savings": monthly_savings,
        "annual_savings": round(monthly_savings * MONTHS_PER_YEAR, 2),
        "savings_pct": round(monthly_savings / current_cost * 100, 1),
        "risk_level": risk_level,
        "risk_reason": risk_reason,
        "projected_cpu_peak_pct": projected_cpu,
        "projected_cpu_max_pct": projected_cpu_max,
        "projected_memory_peak_pct": projected_mem,
        "reason": analysis["reason"],
    }


def build_recommendations(results: list[tuple[dict, dict | None, dict]]) -> tuple[list[dict], list[str]]:
    """
    Run recommend() over the (db_instance, metrics, analysis) triples
    produced by the pipeline.

    Returns (recommendations, no_safe_downgrade):
      recommendations    - sorted by monthly savings, biggest first
      no_safe_downgrade  - instance IDs flagged over-sized for which no
                           smaller size passed the safety checks
    """
    recommendations, no_safe_downgrade = [], []
    for db, metrics, analysis in results:
        if not analysis.get("is_oversized"):
            continue
        rec = recommend(db, metrics, analysis)
        if rec:
            recommendations.append(rec)
        else:
            no_safe_downgrade.append(db["instance_id"])

    recommendations.sort(key=lambda r: (-r["monthly_savings"], r["instance_id"]))
    return recommendations, no_safe_downgrade


def total_savings(recommendations: list[dict]) -> dict:
    """
    Sum monthly and annual savings across all recommendations.

    Annual is computed from the rounded monthly total (x12) so the two
    figures shown in the report always agree with each other.
    """
    monthly = round(sum(r["monthly_savings"] for r in recommendations), 2)
    return {"monthly": monthly, "annual": round(monthly * MONTHS_PER_YEAR, 2)}


def savings_by_risk(recommendations: list[dict]) -> dict:
    """
    Monthly savings and recommendation count per risk level, so the
    report can show "how much is safe to take right away".
    Every level is always present (0 when unused).
    """
    summary = {level: {"count": 0, "monthly": 0.0} for level in RISK_LEVELS}
    for r in recommendations:
        bucket = summary[r["risk_level"]]
        bucket["count"] += 1
        bucket["monthly"] = round(bucket["monthly"] + r["monthly_savings"], 2)
    return summary


def spend_summary(instances: list[dict], recommendations: list[dict]) -> dict:
    """
    Current vs projected monthly spend across every listed instance.

    Instances with an unknown price (monthly_cost 0.0) can't be counted;
    they are reported in `unpriced` so the report can say the total is
    a lower bound rather than silently under-stating spend.
    """
    current = round(sum(db["monthly_cost"] for db in instances), 2)
    savings = total_savings(recommendations)["monthly"]
    projected = round(current - savings, 2)
    return {
        "current_monthly": current,
        "current_annual": round(current * MONTHS_PER_YEAR, 2),
        "projected_monthly": projected,
        "projected_annual": round(projected * MONTHS_PER_YEAR, 2),
        "savings_pct": round(savings / current * 100, 1) if current else 0.0,
        "unpriced": [db["instance_id"] for db in instances if not db["monthly_cost"]],
    }
