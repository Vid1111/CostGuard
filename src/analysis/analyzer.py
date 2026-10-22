"""
analysis.analyzer
-------------------
Week 7-8 deliverable: decide, from a MetricsSummary, whether an RDS
instance is over-sized. Returns an AnalysisResult dict (see
docs/architecture.md section 4) for the Recommendation Engine.

Pure logic — no AWS calls — so every rule here is unit-testable with
plain dicts.
"""

# thresholds from the project brief (docs/architecture.md section 5)
CPU_AVG_THRESHOLD = 20.0
MEMORY_AVG_THRESHOLD = 30.0

# the brief says CPU/memory must be *always* low, not just low on
# average — an instance averaging 10% CPU with nightly 90% spikes is
# not safe to shrink. These cap the peak seen in the window.
# CPU uses the 95th percentile of hourly peaks, so a handful of one-off
# spikes don't block a downgrade; memory uses the true peak, because
# running out of RAM crashes or swaps while busy CPU only slows down.
CPU_PEAK_LIMIT = 50.0
MEMORY_PEAK_LIMIT = 60.0

OVERSIZED = "oversized"
RIGHT_SIZED = "right_sized"
INSUFFICIENT_DATA = "insufficient_data"


def _is_low(avg, peak, avg_threshold, peak_limit) -> bool:
    return avg is not None and avg < avg_threshold and (peak is None or peak < peak_limit)


def analyze_instance(metrics: dict) -> dict:
    """
    Apply the over-sizing thresholds to one MetricsSummary.

    An instance is OVER-SIZED only when CPU *and* memory are both low.
    Mixed cases (low CPU but busy memory, or the reverse) are reported
    as right-sized: within an RDS family every step down halves vCPUs
    and RAM together, so shrinking would starve whichever resource is
    actually in use.

    If memory % is unknown (no FreeableMemory data), the decision falls
    back to CPU alone and the reason says so.
    """
    cpu_avg = metrics.get("cpu_avg_pct")
    cpu_p95 = metrics.get("cpu_p95_pct")
    if cpu_p95 is None:
        cpu_p95 = metrics.get("cpu_max_pct")
    mem_avg = metrics.get("memory_avg_pct")
    mem_max = metrics.get("memory_max_pct")

    if cpu_avg is None:
        return insufficient_data_result(metrics["instance_id"], "no CPU data")

    cpu_low = _is_low(cpu_avg, cpu_p95, CPU_AVG_THRESHOLD, CPU_PEAK_LIMIT)
    mem_known = mem_avg is not None
    mem_low = _is_low(mem_avg, mem_max, MEMORY_AVG_THRESHOLD, MEMORY_PEAK_LIMIT)

    cpu_text = f"CPU avg {cpu_avg}% / p95 peak {cpu_p95}%"
    mem_text = f"memory avg {mem_avg}% / peak {mem_max}%" if mem_known else "memory unknown"

    if cpu_low and (mem_low or not mem_known):
        status = OVERSIZED
        reason = f"{cpu_text} and {mem_text} are below thresholds"
        if not mem_known:
            reason += " (decided on CPU only)"
    elif cpu_low:
        status = RIGHT_SIZED
        reason = f"CPU is low ({cpu_text}) but memory is in use ({mem_text})"
    elif mem_low:
        status = RIGHT_SIZED
        reason = f"memory is low ({mem_text}) but CPU is in use ({cpu_text})"
    else:
        status = RIGHT_SIZED
        reason = f"{cpu_text}, {mem_text} — capacity is in use"

    return {
        "instance_id": metrics["instance_id"],
        "status": status,
        "is_oversized": status == OVERSIZED,
        "reason": reason,
    }


def insufficient_data_result(instance_id: str, reason: str) -> dict:
    """
    AnalysisResult for an instance whose metrics couldn't be trusted
    (InsufficientMetricsError) — reported as skipped, never as healthy.
    """
    return {
        "instance_id": instance_id,
        "status": INSUFFICIENT_DATA,
        "is_oversized": False,
        "reason": reason,
    }
