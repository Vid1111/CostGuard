"""
report.generator
------------------
Week 9-10 deliverable (Person C): turn the pipeline's output into a
single self-contained HTML report (no external CSS, JS, fonts or
images, so it can be emailed or opened offline).

The report shows:
  * current monthly / annual spend across every listed instance
  * every recommended change, with projected usage on the new size
  * total potential savings, and how much of it is low / medium / high risk
  * every instance's usage and verdict, plus skipped / unpriced ones
  * the thresholds used, read from the engine's own constants so the
    report can never disagree with the code that made the decisions

Pipeline:
    build_report_context(...)  -> plain dict (pure, unit-tested)
    render_report(context)     -> HTML string (Jinja2, autoescaped)
    write_report(html, path)   -> Path on disk
    generate_report(...)       -> all three in one call
"""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from src.analysis import analyzer, OVERSIZED, RIGHT_SIZED, INSUFFICIENT_DATA
from src.metrics.collector import MIN_OBSERVATION_DAYS
from src.recommendations import engine, total_savings, savings_by_risk, spend_summary, RISK_LEVELS

TEMPLATE_DIR = Path(__file__).parent / "templates"
TEMPLATE_NAME = "report.html.j2"
DEFAULT_REPORT_DIR = Path("reports")

_VERDICT_LABELS = {
    OVERSIZED: "Over-sized",
    RIGHT_SIZED: "Right-sized",
    INSUFFICIENT_DATA: "Skipped",
}


class ReportGenerationError(Exception):
    """Raised when the report can't be rendered or written to disk."""


# ---------------------------------------------------------------------------
# formatting filters (registered on the Jinja environment)
# ---------------------------------------------------------------------------

def money(value: float | None, decimals: int = 2) -> str:
    """1234.5 -> '$1,234.50'; None -> 'n/a'."""
    if value is None:
        return "n/a"
    return f"-${abs(value):,.{decimals}f}" if value < 0 else f"${value:,.{decimals}f}"


def pct(value: float | None) -> str:
    """15.0 -> '15%', 15.25 -> '15.25%'; None -> 'n/a'."""
    return "n/a" if value is None else f"{value:g}%"


def gb(value: float | None) -> str:
    return "n/a" if value is None else f"{value:g} GB"


def plain(text: str | None) -> str:
    """Normalise em/en dashes in upstream reason strings for clean output."""
    return (text or "").replace(" \u2014 ", ": ").replace("\u2014", "-").replace("\u2013", "-")


def bar_width(value: float | None, maximum: float | None) -> float:
    """Width (0-100) of a CSS bar for value relative to maximum."""
    if not value or not maximum:
        return 0.0
    return round(max(0.0, min(100.0, value / maximum * 100)), 2)


def _environment() -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(["html", "j2"]),
        undefined=StrictUndefined,  # a typo in the template fails loudly, not silently blank
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters.update(money=money, pct=pct, gb=gb, plain=plain, bar_width=bar_width)
    return env


# ---------------------------------------------------------------------------
# context
# ---------------------------------------------------------------------------

def _instance_row(db: dict, metrics: dict | None, analysis: dict, rec: dict | None) -> dict:
    """One row of the 'all instances' table."""
    m = metrics or {}
    return {
        "instance_id": db["instance_id"],
        "instance_type": db["instance_type"],
        "engine": db.get("engine", "unknown"),
        "region": db.get("region", ""),
        "multi_az": bool(db.get("multi_az")),
        "monthly_cost": db["monthly_cost"],
        "priced": bool(db["monthly_cost"]),
        "storage_allocated_gb": db.get("storage_allocated_gb"),
        "storage_used_gb": m.get("storage_used_gb"),
        "cpu_avg_pct": m.get("cpu_avg_pct"),
        "cpu_p95_pct": m.get("cpu_p95_pct"),
        "cpu_max_pct": m.get("cpu_max_pct"),
        "memory_avg_pct": m.get("memory_avg_pct"),
        "memory_max_pct": m.get("memory_max_pct"),
        "connections_avg": m.get("connections_avg"),
        "status": analysis["status"],
        "verdict": _VERDICT_LABELS.get(analysis["status"], analysis["status"]),
        "reason": analysis.get("reason", ""),
        "has_recommendation": rec is not None,
    }


def _thresholds() -> dict:
    """Decision thresholds, read live from the analysis/recommendation modules."""
    return {
        "observation_days": MIN_OBSERVATION_DAYS,
        "cpu_avg": analyzer.CPU_AVG_THRESHOLD,
        "cpu_peak": analyzer.CPU_PEAK_LIMIT,
        "mem_avg": analyzer.MEMORY_AVG_THRESHOLD,
        "mem_peak": analyzer.MEMORY_PEAK_LIMIT,
        "target_cpu": engine.TARGET_CPU_PEAK,
        "target_mem": engine.TARGET_MEMORY_PEAK,
        "cpu_saturation": engine.CPU_SATURATION_LIMIT,
        "low_risk": engine.LOW_RISK_MAX_PEAK,
        "medium_risk": engine.MEDIUM_RISK_MAX_PEAK,
        "high_risk_cpu": engine.HIGH_RISK_CPU_MAX,
    }


def build_report_context(
    instances: list[dict],
    results: list[tuple[dict, dict | None, dict]],
    recommendations: list[dict],
    no_safe_downgrade: list[str] | None = None,
    *,
    region: str | None = None,
    generated_at: datetime | None = None,
) -> dict:
    """
    Gather everything the template needs into one plain dict.

    Args:
        instances        - DBInstance dicts from the AWS Connector
        results          - (db, metrics_or_None, analysis) per instance
        recommendations  - Recommendation dicts (build_recommendations output)
        no_safe_downgrade- over-sized instance IDs with no safe smaller size
        region           - shown in the header; inferred from instances if omitted
        generated_at     - timestamp for the header (defaults to now, UTC)
    """
    generated_at = generated_at or datetime.now(timezone.utc)
    recs_by_id = {r["instance_id"]: r for r in recommendations}

    rows = [_instance_row(db, m, a, recs_by_id.get(db["instance_id"])) for db, m, a in results]
    # instances listed but never analysed (shouldn't happen, but never drop one silently)
    seen = {row["instance_id"] for row in rows}
    for db in instances:
        if db["instance_id"] not in seen:
            rows.append(_instance_row(db, None, {"status": INSUFFICIENT_DATA, "reason": "not analysed"}, None))

    counts = {status: sum(1 for r in rows if r["status"] == status) for status in _VERDICT_LABELS}
    regions = sorted({db["region"] for db in instances if db.get("region")})
    risk = savings_by_risk(recommendations)
    totals = total_savings(recommendations)

    return {
        "generated_at": generated_at.strftime("%d %b %Y, %H:%M UTC"),
        "region": region or (", ".join(regions) if regions else "n/a"),
        "spend": spend_summary(instances, recommendations),
        "totals": totals,
        "risk": risk,
        "risk_levels": RISK_LEVELS,
        "recommendations": recommendations,
        "max_cost": max((r["current_cost"] for r in recommendations), default=0),
        "rows": sorted(rows, key=lambda r: (-r["monthly_cost"], r["instance_id"])),
        "counts": {
            "total": len(rows),
            "oversized": counts[OVERSIZED],
            "right_sized": counts[RIGHT_SIZED],
            "skipped": counts[INSUFFICIENT_DATA],
        },
        "skipped": [r for r in rows if r["status"] == INSUFFICIENT_DATA],
        "no_safe_downgrade": list(no_safe_downgrade or []),
        "thresholds": _thresholds(),
    }


# ---------------------------------------------------------------------------
# render / write
# ---------------------------------------------------------------------------

def render_report(context: dict) -> str:
    """Render the context to an HTML string."""
    try:
        return _environment().get_template(TEMPLATE_NAME).render(**context)
    except Exception as e:  # jinja2 raises several exception types
        raise ReportGenerationError(f"Could not render the HTML report: {e}") from e


def default_report_path(now: datetime | None = None) -> Path:
    """reports/costguard-report-YYYYmmdd-HHMMSS.html"""
    now = now or datetime.now()
    return DEFAULT_REPORT_DIR / f"costguard-report-{now:%Y%m%d-%H%M%S}.html"


def write_report(html: str, output_path: str | Path) -> Path:
    """
    Write the HTML atomically (temp file + rename) so a crash halfway
    never leaves a half-written report behind. Returns the absolute path.
    """
    path = Path(output_path).expanduser().resolve()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".costguard-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(html)
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
    except OSError as e:
        raise ReportGenerationError(f"Could not write the report to {path}: {e}") from e
    return path


def generate_report(
    instances: list[dict],
    results: list[tuple[dict, dict | None, dict]],
    recommendations: list[dict],
    no_safe_downgrade: list[str] | None = None,
    output_path: str | Path | None = None,
    *,
    region: str | None = None,
) -> Path:
    """Build, render and write the report in one call. Returns the file path."""
    context = build_report_context(instances, results, recommendations, no_safe_downgrade, region=region)
    return write_report(render_report(context), output_path or default_report_path())
