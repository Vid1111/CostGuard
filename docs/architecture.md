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
    "is_oversized": True,
    "reason": "cpu_avg_pct < 20 and memory_avg_pct < 30"
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
    "risk_level": "medium"
}
```

## 5. Over-Sizing Thresholds (V1)

- CPU average < 20% over the observation window -> candidate for downgrade
- Memory average < 30% over the observation window -> candidate for downgrade
- **Minimum observation window: 14 days**, enforced in code
  (`metrics/collector.py`, `MIN_OBSERVATION_DAYS`) before any CloudWatch
  call is even made.
- **Data coverage check:** if CloudWatch returns fewer than 50% of the
  expected hourly datapoints for the window (e.g. a new, stopped, or
  misidentified instance), the collector raises
  `InsufficientMetricsError` instead of returning a misleadingly
  confident average. The Analysis Engine should treat these instances
  as "skipped — insufficient data," not "healthy."
- Risk level is set by how close usage is to the recommended instance's
  ceiling: comfortable margin = low risk, tight margin = medium/high.

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
- **Pricing table (known limitation):** still static, still
  compute-only — doesn't yet account for Multi-AZ (roughly doubles
  compute + storage cost) or storage/IOPS pricing. Tracked as a V2
  TODO in `pricing.py`.

## 9. Open Questions for Week 7-8

- Exact thresholds for "low/medium/high" risk level in recommendations
  — needs a concrete rule, not just a feel.
- How to handle an instance where CPU is low but memory is high (or
  vice versa) — is it still "oversized," and what's the recommended
  downgrade in that mixed case?
