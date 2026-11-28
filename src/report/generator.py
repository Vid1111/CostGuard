"""
report.generator
-----------------
Person C — Week 9-10 deliverable.

Renders a professional HTML report from the recommendation data produced
by the recommendations engine.  Saves the file to reports/costguard_report.html
(the directory is created automatically if it does not already exist).

Usage (called from main.py after recommendations are ready):

    from src.report.generator import generate_html_report
    generate_html_report(instances, metrics_map, analyses, recommendations, totals)

The function returns the path to the written file so main.py can print it.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

# ── Paths ─────────────────────────────────────────────────────────────────────
_TEMPLATES_DIR = Path(__file__).parent / "templates"
_REPORTS_DIR   = Path(__file__).parent.parent.parent / "reports"


def _env() -> Environment:
    """Return a Jinja2 Environment pointed at our templates folder."""
    return Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=select_autoescape(["html"]),
    )


# ── Public API ─────────────────────────────────────────────────────────────────

def generate_html_report(
    instances:       list[dict],
    metrics_map:     dict[str, dict | None],
    analyses:        dict[str, dict],
    recommendations: list[dict],
    totals:          dict,
    output_path:     str | None = None,
) -> str:
    """
    Render the CostGuard HTML report and write it to disk.

    Parameters
    ----------
    instances       : list of DBInstance dicts from aws_connector
    metrics_map     : instance_id -> MetricsSummary (or None if skipped)
    analyses        : instance_id -> AnalysisResult
    recommendations : list of Recommendation dicts from recommendations.engine
    totals          : {"monthly": float, "annual": float} from total_savings()
    output_path     : override the default output file path

    Returns
    -------
    str — absolute path of the written HTML file.
    """
    # Build per-row data for the template so it doesn't need to do dict lookups
    rows = _build_rows(instances, metrics_map, analyses, recommendations)

    # Recommendation lookup for the savings section
    rec_by_id = {r["instance_id"]: r for r in recommendations}

    context = {
        "generated_at":   datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "instance_count": len(instances),
        "rec_count":      len(recommendations),
        "skipped_count":  sum(1 for a in analyses.values() if a["status"] == "insufficient_data"),
        "total_monthly":  totals["monthly"],
        "total_annual":   totals["annual"],
        "rows":           rows,
        "recommendations": recommendations,
        "rec_by_id":      rec_by_id,
    }

    html = _env().get_template("report.html").render(**context)

    # Determine output path and write
    if output_path is None:
        _REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        output_path = str(_REPORTS_DIR / "costguard_report.html")

    with open(output_path, "w", encoding="utf-8") as fh:
        fh.write(html)

    return os.path.abspath(output_path)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _build_rows(
    instances:   list[dict],
    metrics_map: dict[str, dict | None],
    analyses:    dict[str, dict],
    recommendations: list[dict],
) -> list[dict]:
    """Flatten all per-instance data into template-friendly dicts."""
    rec_by_id = {r["instance_id"]: r for r in recommendations}
    rows = []

    for db in instances:
        iid     = db["instance_id"]
        m       = metrics_map.get(iid)
        a       = analyses.get(iid, {})
        rec     = rec_by_id.get(iid)
        status  = a.get("status", "unknown")

        rows.append({
            # instance basics
            "instance_id":          iid,
            "instance_type":        db["instance_type"],
            "engine":               db["engine"],
            "region":               db["region"],
            "multi_az":             db["multi_az"],
            "storage_gb":           db["storage_allocated_gb"],
            "monthly_cost":         db["monthly_cost"],
            # metrics (may be None)
            "cpu_avg":              _pct(m, "cpu_avg_pct"),
            "cpu_p95":              _pct(m, "cpu_p95_pct"),
            "cpu_max":              _pct(m, "cpu_max_pct"),
            "memory_avg":           _pct(m, "memory_avg_pct"),
            "memory_max":           _pct(m, "memory_max_pct"),
            "storage_used":         f'{m["storage_used_gb"]} GB' if m and m.get("storage_used_gb") is not None else "n/a",
            "connections":          m["connections_avg"] if m else "n/a",
            # analysis
            "status":               status,
            "analysis_reason":      a.get("reason", "—"),
            # recommendation (may be None)
            "recommendation":       rec,
        })

    return rows


def _pct(metrics: dict | None, key: str) -> str:
    if metrics is None:
        return "n/a"
    val = metrics.get(key)
    return f"{val}%" if val is not None else "n/a"
