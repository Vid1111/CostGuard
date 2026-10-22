# CostGuard — Architecture Document

## 1. Overview

CostGuard connects to a user's AWS account, inspects their RDS database
instances, checks real usage against CloudWatch metrics, and recommends
cheaper instance sizes where the database is over-provisioned. Output is
an HTML report showing current spend, recommendations, and savings.

## 2. Data Flow

```
[AWS Credentials Input]
        |
        v
[AWS Connector] ---------> list of RDS instances
        |                  (type, cost, region, storage)
        v
[Metrics Collector] -----> CloudWatch usage per instance
        |                  (CPU / memory / storage / connections)
        v
[Analysis Engine] -------> flags over-sized instances
        |                  (applies thresholds)
        v
[Recommendation Engine] -> cheaper instance type + savings calc
        |
        v
[Report Generator] ------> HTML report
```

Each stage is a separate module with a single responsibility. Modules
communicate through plain data objects, not shared state, so any stage
can be tested or swapped independently.

## 3. Module Ownership

| Module              | Owner    | Responsibility                                   |
|----------------------|----------|---------------------------------------------------|
| `aws_connector`      | Person A | Auth, list RDS instances, pull instance details   |
| `metrics`            | Person B | Query CloudWatch, compute avg/max/min usage       |
| `analysis`           | Shared   | Apply over-sizing thresholds                       |
| `recommendations`    | Person C | Map to smaller instance type, calculate savings   |
| `report`             | Person C | Render HTML report from recommendation data       |

## 4. Data Contracts

### `DBInstance` (output of `aws_connector`)
```python
{
    "instance_id": "prod-orders-db",
    "instance_type": "db.r5.2xlarge",
    "engine": "postgres",
    "region": "us-east-1",
    "monthly_cost": 2000.00,       # from pricing lookup
    "storage_allocated_gb": 500,
    "multi_az": True
}
```

### `MetricsSummary` (output of `metrics`, keyed by instance_id)
```python
{
    "instance_id": "prod-orders-db",
    "cpu_avg_pct": 15.2,
    "cpu_max_pct": 34.0,
    "cpu_min_pct": 2.1,
    "cpu_p95_pct": 22.0,            # 95th percentile of hourly peaks
    "memory_avg_pct": 20.1,
    "memory_max_pct": 41.0,
    "storage_used_gb": 210,
    "connections_avg": 12,
    "observation_window_days": 14
}
```
Note: `memory_avg_pct` / `memory_max_pct` come back as `None` when the
instance type isn't in `metrics/instance_memory.py`'s RAM table — the
collector never guesses a memory percentage it can't actually compute.

### `AnalysisResult` (output of `analysis`)
```python
{
    "instance_id": "prod-orders-db",
    "status": "oversized",          # or "right_sized" / "insufficient_data"
    "is_oversized": True,
    "reason": "CPU avg 15.2% / p95 peak 22.0% and memory avg 20.1% / peak 41.0% are below thresholds"
}
```

### `Recommendation` (output of `recommendations`)
```python
{
    "instance_id": "prod-orders-db",
    "current_type": "db.r5.2xlarge",
    "current_cost": 2000.00,
    "recommended_type": "db.r5.large",
    "recommended_cost": 500.00,
    "monthly_savings": 1500.00,
    "annual_savings": 18000.00,
    "risk_level": "medium",
    "projected_cpu_peak_pct": 60.0,    # usage scaled onto the smaller size
    "projected_cpu_max_pct": 88.0,      # busiest minute on the smaller size
    "projected_memory_peak_pct": 60.0,
    "reason": "..."                   # copied from the AnalysisResult
}
```

## 5. Over-Sizing Thresholds (V1)

- CPU average < 20% **and** CPU p95 peak < 50% -> CPU is "always low"
  (p95 = 95th percentile of the hourly maximums: rare one-off spikes are
  ignored, regular busy hours are not)
- Memory average < 30% **and** memory peak < 60% -> memory is "always low"
- An instance is over-sized only when **both** are low (the brief says
  "always" low, so the peak caps stop a low average that hides big
  spikes from being shrunk). If memory % is unknown, CPU alone decides.
- **Minimum observation window: 14 days**, enforced in code
  (`metrics/collector.py`, `MIN_OBSERVATION_DAYS`) before any CloudWatch
  call is even made.
- **Data coverage check:** if CloudWatch returns fewer than 50% of the
  expected hourly datapoints for the window (e.g. a new, stopped, or
  misidentified instance), the collector raises
  `InsufficientMetricsError` instead of returning a misleadingly
  confident average. The Analysis Engine should treat these instances
  as "skipped — insufficient data," not "healthy."
- **Target size** (`recommendations/engine.py`): peak usage is projected
  onto each smaller size in the same family by the vCPU / RAM ratio
  (15% CPU on 8 vCPUs = 60% on 2 vCPUs). The smallest size whose
  projected CPU p95 stays under 75% and memory peak under 80% is
  recommended, as long as the single busiest CPU minute still fits
  (projected <= 100%).
- **Risk level** is high if the busiest CPU minute would exceed 90%
  on the new size; otherwise it's the higher projected peak:
  <= 50% low, <= 65% medium, above that high. Unknown memory is never
  low risk.

## 6. Tech Stack

- **Language:** Python 3.11+
- **AWS SDK:** boto3 (RDS client + CloudWatch client)
- **Report rendering:** Jinja2 templates -> static HTML
- **Config/secrets:** environment variables / AWS shared credentials
  file, loaded via `python-dotenv` for local dev. Nothing hardcoded,
  nothing committed.
- **Pricing data:** static lookup table (instance type + region ->
  monthly cost), `aws_connector/pricing.py`.
- **Memory specs:** static lookup table (instance type -> total RAM),
  `metrics/instance_memory.py` — needed to convert CloudWatch's
  `FreeableMemory` (bytes) into a usage percentage, since AWS has no
  API that returns total RAM for an RDS instance class.
- **Testing:** pytest + moto (mocks both RDS and CloudWatch, so the
  full suite runs with no real AWS account).

## 7. Security Notes

- Credentials are never hardcoded or logged.
- `.env` is git-ignored; `.env.example` documents required variables
  without real values.
- Recommend read-only IAM permissions (`rds:Describe*`,
  `cloudwatch:GetMetricStatistics`) — the tool never needs write access
  in V1.

## 8. Decisions Made in Weeks 3-6

- **CloudWatch API choice (resolved):** we used `get_metric_statistics`
  (one metric at a time) rather than `get_metric_data` (batched). For
  V1's scale — a handful of instances, 4 metrics each — the simpler,
  more readable API was worth the extra call count. Worth revisiting
  `get_metric_data` in V2 if an account has many dozens of instances,
  since it batches requests and reduces API call volume.
- **Pricing table (known limitation):** still static and compute-only.
  Multi-AZ is now priced (instance cost x2). Storage/IOPS aren't priced,
  which doesn't affect savings since a downgrade keeps the same storage.

## 9. Decisions Made in Weeks 7-8

- **Risk levels:** concrete rule based on projected peak usage on the
  recommended size (see section 5).
- **Mixed CPU/memory case:** not over-sized. Every step down within an
  RDS family halves vCPUs and RAM together, so a busy resource blocks
  the downgrade. Switching family (e.g. r5 -> m5 for a CPU-light,
  memory-heavy load) is left for V2.
- **CPU p95 instead of max:** sizing on the single busiest minute let
  one backup or migration block an otherwise-safe downgrade. Memory
  keeps the true peak, since running out of RAM is worse than running
  hot on CPU.
- **Burstable t3:** all t3 sizes have 2 vCPUs, so a t3 downgrade is
  limited by memory only; CPU credit balance isn't checked in V1.
