"""
Tests for src.report - the HTML report generator.

The context builder is plain Python, so its numbers are checked
exactly. The rendered HTML is checked for the figures the brief asks
for (current spend, recommended changes, total savings, risk level)
and for escaping, so a strange instance name can't break the page.
"""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.analysis import analyze_instance, analyzer
from src.recommendations import build_recommendations, engine
from src.report import (
    build_report_context,
    render_report,
    write_report,
    generate_report,
    default_report_path,
    ReportGenerationError,
)
from src.report import demo
from src.report.generator import money, pct, gb, plain, bar_width


def _pipeline():
    instances, results = demo.sample_pipeline()
    recommendations, no_safe = build_recommendations(results)
    return instances, results, recommendations, no_safe


def _context(**kwargs):
    instances, results, recs, no_safe = _pipeline()
    return build_report_context(
        instances, results, recs, no_safe,
        generated_at=datetime(2026, 11, 28, 9, 30, tzinfo=timezone.utc), **kwargs,
    )


class TestFilters:
    def test_money(self):
        assert money(1234.5) == "$1,234.50"
        assert money(1234.5, 0) == "$1,234"
        assert money(-20) == "-$20.00"
        assert money(None) == "n/a"

    def test_pct_and_gb(self):
        assert pct(15.0) == "15%"
        assert pct(15.25) == "15.25%"
        assert pct(None) == "n/a"
        assert gb(64) == "64 GB"
        assert gb(None) == "n/a"

    def test_plain_removes_dashes(self):
        assert plain("skipped — too new") == "skipped: too new"
        assert plain(None) == ""

    def test_bar_width_is_clamped(self):
        assert bar_width(50, 200) == 25.0
        assert bar_width(500, 200) == 100.0
        assert bar_width(None, 200) == 0.0
        assert bar_width(10, 0) == 0.0


class TestContext:
    def test_counts_cover_every_database(self):
        ctx = _context()
        assert ctx["counts"]["total"] == len(demo.SAMPLE_FLEET)
        assert ctx["counts"]["oversized"] + ctx["counts"]["right_sized"] + ctx["counts"]["skipped"] == ctx["counts"]["total"]
        assert [r["instance_id"] for r in ctx["skipped"]] == ["brand-new-db"]

    def test_spend_and_savings_add_up(self):
        ctx = _context()
        spend, totals = ctx["spend"], ctx["totals"]

        assert totals["monthly"] == round(sum(r["monthly_savings"] for r in ctx["recommendations"]), 2)
        assert totals["annual"] == round(totals["monthly"] * 12, 2)
        assert spend["projected_monthly"] == round(spend["current_monthly"] - totals["monthly"], 2)

        by_risk = sum(ctx["risk"][level]["monthly"] for level in ctx["risk_levels"])
        assert round(by_risk, 2) == totals["monthly"]

    def test_thresholds_come_from_the_engine(self):
        """The 'How decisions were made' section can never disagree with the code."""
        t = _context()["thresholds"]
        assert t["cpu_avg"] == analyzer.CPU_AVG_THRESHOLD
        assert t["mem_peak"] == analyzer.MEMORY_PEAK_LIMIT
        assert t["target_cpu"] == engine.TARGET_CPU_PEAK
        assert t["high_risk_cpu"] == engine.HIGH_RISK_CPU_MAX

    def test_rows_sorted_by_cost_and_linked_to_recommendations(self):
        rows = _context()["rows"]
        costs = [r["monthly_cost"] for r in rows]
        assert costs == sorted(costs, reverse=True)
        assert {r["instance_id"] for r in rows if r["has_recommendation"]} == \
               {r["instance_id"] for r in _context()["recommendations"]}

    def test_listed_but_unanalysed_instance_is_not_dropped(self):
        instances, results, recs, no_safe = _pipeline()
        extra = dict(instances[0], instance_id="ghost-db")
        ctx = build_report_context(instances + [extra], results, recs, no_safe)
        assert "ghost-db" in [r["instance_id"] for r in ctx["skipped"]]

    def test_region_defaults_to_the_instances_regions(self):
        assert _context()["region"] == "us-east-1"
        assert _context(region="eu-west-1")["region"] == "eu-west-1"

    def test_unpriced_instance_is_reported(self):
        db = {"instance_id": "odd-db", "instance_type": "db.z9.massive", "engine": "postgres",
              "region": "us-east-1", "monthly_cost": 0.0, "storage_allocated_gb": 10, "multi_az": False}
        metrics = {"instance_id": "odd-db", "cpu_avg_pct": 50.0, "cpu_p95_pct": 60.0, "cpu_max_pct": 70.0,
                   "memory_avg_pct": 50.0, "memory_max_pct": 60.0}
        ctx = build_report_context([db], [(db, metrics, analyze_instance(metrics))], [])
        assert ctx["spend"]["unpriced"] == ["odd-db"]


class TestRender:
    def test_report_shows_the_brief_requirements(self):
        ctx = _context()
        html = render_report(ctx)

        assert "Current spend" in html
        assert money(ctx["spend"]["current_monthly"], 0) in html
        assert "Recommended changes" in html
        assert "Potential savings" in html
        assert money(ctx["totals"]["monthly"]) in html
        assert money(ctx["totals"]["annual"]) in html
        for r in ctx["recommendations"]:
            assert r["instance_id"] in html
            assert f'class="tag {r["risk_level"]}"' in html
        assert "28 Nov 2026, 09:30 UTC" in html

    def test_report_is_self_contained(self):
        html = render_report(_context())
        assert "<script" not in html
        assert "<link" not in html
        assert "http://" not in html and "https://" not in html

    def test_instance_names_are_html_escaped(self):
        db = {"instance_id": "<script>alert(1)</script>", "instance_type": "db.t3.micro", "engine": "postgres",
              "region": "us-east-1", "monthly_cost": 12.41, "storage_allocated_gb": 20, "multi_az": False}
        ctx = build_report_context([db], [], [])
        html = render_report(ctx)
        assert "<script>alert(1)</script>" not in html
        assert "&lt;script&gt;" in html

    def test_no_recommendations_shows_empty_state(self):
        html = render_report(build_report_context([], [], []))
        assert "No downgrade recommendations" in html

    def test_missing_context_value_raises_clear_error(self):
        ctx = _context()
        del ctx["totals"]
        with pytest.raises(ReportGenerationError, match="Could not render"):
            render_report(ctx)


class TestWrite:
    def test_write_creates_folders_and_returns_absolute_path(self, tmp_path):
        path = write_report("<html>ok</html>", tmp_path / "a" / "b" / "report.html")
        assert path.is_absolute()
        assert path.read_text(encoding="utf-8") == "<html>ok</html>"
        assert [p.name for p in path.parent.iterdir()] == ["report.html"]  # no temp file left behind

    def test_write_overwrites_existing_report(self, tmp_path):
        target = tmp_path / "report.html"
        target.write_text("old", encoding="utf-8")
        write_report("new", target)
        assert target.read_text(encoding="utf-8") == "new"

    def test_write_to_a_folder_raises_clear_error(self, tmp_path):
        with pytest.raises(ReportGenerationError, match="Could not write"):
            write_report("<html></html>", tmp_path)

    def test_default_path_is_timestamped(self):
        path = default_report_path(datetime(2026, 11, 28, 9, 30, 5))
        assert path == Path("reports") / "costguard-report-20261128-093005.html"

    def test_generate_report_end_to_end(self, tmp_path):
        instances, results, recs, no_safe = _pipeline()
        path = generate_report(instances, results, recs, no_safe, tmp_path / "out.html")
        html = path.read_text(encoding="utf-8")
        assert html.startswith("<!DOCTYPE html>")
        assert "orders-prod" in html


def test_demo_writes_a_report(tmp_path, capsys):
    out = tmp_path / "demo.html"
    assert demo.main(["--output", str(out)]) == 0
    assert out.exists()
    assert "Sample report written to" in capsys.readouterr().out
