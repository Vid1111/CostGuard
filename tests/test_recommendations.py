"""
Tests for src.recommendations — target-size choice, savings math, and
risk levels. Costs are worked out by hand from aws_connector.pricing
so a wrong number in the engine shows up as a failed assertion.
"""

from src.analysis import analyze_instance
from src.aws_connector.pricing import estimate_monthly_cost
from src.recommendations import recommend, total_savings
from src.recommendations.instance_sizes import smaller_sizes


def _db(instance_type, multi_az=False, region="us-east-1"):
    return {
        "instance_id": "db-1",
        "instance_type": instance_type,
        "engine": "postgres",
        "region": region,
        "monthly_cost": estimate_monthly_cost(instance_type, region, multi_az),
        "storage_allocated_gb": 100,
        "multi_az": multi_az,
    }


def _metrics(cpu_avg, cpu_max, mem_avg, mem_max, cpu_p95=None):
    return {
        "instance_id": "db-1",
        "cpu_avg_pct": cpu_avg,
        "cpu_max_pct": cpu_max,
        "cpu_min_pct": 0.0,
        "cpu_p95_pct": cpu_p95 if cpu_p95 is not None else cpu_max,
        "memory_avg_pct": mem_avg,
        "memory_max_pct": mem_max,
        "storage_used_gb": 50.0,
        "connections_avg": 5.0,
        "observation_window_days": 14,
    }


def _recommend(db, metrics):
    return recommend(db, metrics, analyze_instance(metrics))


def test_brief_example_r5_2xlarge_downgrades_to_r5_large():
    # 15% of 8 vCPUs -> 60% of 2 vCPUs; 15% of 64GB -> 60% of 16GB
    rec = _recommend(_db("db.r5.2xlarge"), _metrics(15.0, 15.0, 15.0, 15.0))

    assert rec["current_type"] == "db.r5.2xlarge"
    assert rec["recommended_type"] == "db.r5.large"
    assert rec["current_cost"] == 700.80
    assert rec["recommended_cost"] == 175.20
    assert rec["monthly_savings"] == 525.60
    assert rec["annual_savings"] == 6307.20
    assert rec["projected_cpu_peak_pct"] == 60.0
    assert rec["projected_memory_peak_pct"] == 60.0
    assert rec["risk_level"] == "medium"


def test_stops_before_projected_peak_exceeds_ceiling():
    # r5.4xlarge -> xlarge projects 48% memory; large would be 96% (over 80%)
    rec = _recommend(_db("db.r5.4xlarge"), _metrics(5.0, 8.0, 10.0, 12.0))

    assert rec["recommended_type"] == "db.r5.xlarge"
    assert rec["monthly_savings"] == round(1401.60 - 350.40, 2)
    assert rec["risk_level"] == "low"


def test_sizing_uses_p95_but_busiest_minute_must_still_fit():
    # p95 15% would allow r5.large (60%), but the 48% busiest minute
    # would need 192% of an r5.large — so stop at r5.xlarge (96%)
    rec = _recommend(_db("db.r5.2xlarge"), _metrics(10.0, 48.0, 15.0, 15.0, cpu_p95=15.0))

    assert rec["recommended_type"] == "db.r5.xlarge"
    assert rec["projected_cpu_peak_pct"] == 30.0
    assert rec["projected_cpu_max_pct"] == 96.0
    assert rec["risk_level"] == "high"  # busiest minute lands above 90%


def test_rare_spike_still_allows_full_downgrade_when_it_fits():
    # busiest minute 20% on 8 vCPUs -> 80% on 2 vCPUs, still fits
    rec = _recommend(_db("db.r5.2xlarge"), _metrics(8.0, 20.0, 15.0, 15.0, cpu_p95=12.0))

    assert rec["recommended_type"] == "db.r5.large"
    assert rec["projected_cpu_peak_pct"] == 48.0
    assert rec["projected_cpu_max_pct"] == 80.0
    assert rec["risk_level"] == "medium"  # memory 60% decides


def test_t3_downgrade_is_limited_by_memory():
    # t3 sizes share 2 vCPUs; 25% of 4GB -> 50% of 2GB, but 100% of 1GB
    rec = _recommend(_db("db.t3.medium"), _metrics(10.0, 15.0, 20.0, 25.0))

    assert rec["recommended_type"] == "db.t3.small"
    assert rec["monthly_savings"] == 24.82
    assert rec["risk_level"] == "low"


def test_multi_az_savings_are_doubled():
    rec = _recommend(_db("db.r5.2xlarge", multi_az=True), _metrics(15.0, 15.0, 15.0, 15.0))

    assert rec["current_cost"] == 1401.60
    assert rec["recommended_cost"] == 350.40
    assert rec["monthly_savings"] == 1051.20


def test_region_pricing_is_used_for_target():
    rec = _recommend(_db("db.r5.2xlarge", region="eu-west-1"), _metrics(15.0, 15.0, 15.0, 15.0))
    assert rec["recommended_cost"] == round(175.20 * 1.10, 2)
    assert rec["monthly_savings"] == round(700.80 * 1.10 - 175.20 * 1.10, 2)


def test_unknown_memory_is_never_low_risk():
    rec = _recommend(_db("db.r5.2xlarge"), _metrics(4.0, 5.0, None, None))
    assert rec["recommended_type"] == "db.r5.large"  # 5% of 8 vCPUs -> 20% of 2
    assert rec["risk_level"] == "medium"


def test_smallest_size_has_no_recommendation():
    assert _recommend(_db("db.t3.micro"), _metrics(5.0, 8.0, 10.0, 12.0)) is None


def test_right_sized_instance_has_no_recommendation():
    assert _recommend(_db("db.r5.2xlarge"), _metrics(70.0, 90.0, 80.0, 90.0)) is None


def test_unknown_instance_type_has_no_recommendation():
    db = _db("db.z9.massive")
    assert db["monthly_cost"] == 0.0
    assert _recommend(db, _metrics(5.0, 8.0, None, None)) is None


def test_total_savings():
    recs = [{"monthly_savings": 525.60}, {"monthly_savings": 24.82}]
    assert total_savings(recs) == {"monthly": 550.42, "annual": 6605.04}


def test_smaller_sizes_order():
    assert smaller_sizes("db.r5.2xlarge") == ["db.r5.xlarge", "db.r5.large"]
    assert smaller_sizes("db.r5.large") == []
    assert smaller_sizes("db.made.up") == []
