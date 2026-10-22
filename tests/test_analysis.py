"""
Tests for src.analysis.analyzer — pure logic, no AWS mocking needed.
"""

from src.analysis import analyze_instance, insufficient_data_result, OVERSIZED, RIGHT_SIZED, INSUFFICIENT_DATA


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


def test_low_cpu_and_low_memory_is_oversized():
    """The project brief's example: 15% CPU, 20% memory."""
    result = analyze_instance(_metrics(15.0, 15.0, 20.0, 20.0))
    assert result["status"] == OVERSIZED
    assert result["is_oversized"] is True


def test_busy_instance_is_right_sized():
    result = analyze_instance(_metrics(78.0, 95.0, 85.0, 90.0))
    assert result["status"] == RIGHT_SIZED
    assert result["is_oversized"] is False


def test_low_average_with_frequent_cpu_spikes_is_not_oversized():
    """'Always < 20%' — a 10% average hiding regular 90% peaks must not be shrunk."""
    result = analyze_instance(_metrics(10.0, 90.0, 20.0, 25.0, cpu_p95=85.0))
    assert result["status"] == RIGHT_SIZED


def test_rare_cpu_spike_does_not_block_oversized():
    """A few one-off spikes above the p95 shouldn't hide an idle instance."""
    result = analyze_instance(_metrics(10.0, 90.0, 20.0, 25.0, cpu_p95=15.0))
    assert result["status"] == OVERSIZED
    assert "p95 peak 15.0%" in result["reason"]


def test_low_cpu_but_busy_memory_is_not_oversized():
    """Mixed case: a step down halves RAM too, so busy memory blocks it."""
    result = analyze_instance(_metrics(10.0, 15.0, 70.0, 80.0))
    assert result["status"] == RIGHT_SIZED
    assert "memory is in use" in result["reason"]


def test_low_memory_but_busy_cpu_is_not_oversized():
    result = analyze_instance(_metrics(60.0, 80.0, 10.0, 15.0))
    assert result["status"] == RIGHT_SIZED
    assert "CPU is in use" in result["reason"]


def test_unknown_memory_falls_back_to_cpu_only():
    result = analyze_instance(_metrics(10.0, 15.0, None, None))
    assert result["status"] == OVERSIZED
    assert "CPU only" in result["reason"]


def test_thresholds_are_strictly_below():
    """Exactly 20% CPU / 30% memory is not 'below' the threshold."""
    assert analyze_instance(_metrics(20.0, 20.0, 10.0, 10.0))["status"] == RIGHT_SIZED
    assert analyze_instance(_metrics(10.0, 10.0, 30.0, 30.0))["status"] == RIGHT_SIZED


def test_insufficient_data_is_never_reported_as_oversized():
    result = insufficient_data_result("new-db", "skipped — too little data")
    assert result["status"] == INSUFFICIENT_DATA
    assert result["is_oversized"] is False
