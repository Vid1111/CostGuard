# CostGuard

Find where you're wasting money on cloud databases.

CostGuard connects to your AWS account, checks how your RDS databases are
actually being used, flags instances that are over-provisioned, and
recommends cheaper instance types — with an estimate of how much you'd
save per month and per year.

## Status

🚧 In development — V1 scope. See [docs/architecture.md](docs/architecture.md)
for the system design.

- [x] Week 1-2: Architecture & project structure
- [x] Week 3-4: AWS Connection — can list all RDS databases
- [x] Week 5-6: CloudWatch metrics — can show usage metrics for each database
- [x] Week 7-8: Analysis & recommendations — flags over-sized instances, recommends a smaller size, calculates savings
- [ ] Week 9-10: HTML report
- [ ] Week 11-12: Polish, testing, demo

## How it works

1. **Connect** to your AWS account with read-only credentials (`src/aws_connector/`)
2. **List** every RDS instance with its type, engine, region, storage, Multi-AZ and estimated monthly cost
3. **Measure** 14 days of CloudWatch usage per instance: CPU (avg / p95 / max / min), memory, storage, connections (`src/metrics/`)
4. **Analyse** which instances are over-sized (`src/analysis/`)
5. **Recommend** a smaller instance type in the same family and calculate the savings (`src/recommendations/`)
6. **Report** as HTML *(Week 9-10)*

## Quick start

```bash
pip install -r requirements.txt
cp .env.example .env        # then fill in your AWS keys
python -m src.main
```

Full instructions, including the IAM permissions to grant, are in [SETUP.md](SETUP.md).

## Example

Three databases: one idle, one busy, one created yesterday.

```
Usage metrics (last 14 days):

Instance ID              CPU avg   CPU p95   CPU max   CPU min   Mem avg   Mem max   Verdict
--------------------------------------------------------------------------------------------
idle-orders-db           15.0%     15.0%     15.0%     15.0%     15.0%     15.0%     OVER-SIZED
busy-analytics-db        78.0%     78.0%     78.0%     78.0%     90.0%     90.0%     ok
brand-new-db               (skipped — Only 0/336 expected CPU datapoints found ...)

Recommendations:

Instance ID              Current         $/mo       Recommended     $/mo       Save $/mo   Save $/yr   Risk
-----------------------------------------------------------------------------------------------------------
idle-orders-db           db.r5.2xlarge   $700.80    db.r5.large     $175.20    $525.60     $6,307.20   medium

Total potential savings: $525.60/month = $6,307.20/year

1 instance(s) skipped — not enough CloudWatch data to judge.
```

`idle-orders-db` uses 15% of 8 vCPUs and 15% of 64 GB RAM. On a
`db.r5.large` (2 vCPUs, 16 GB) that becomes about 60% CPU and 60% memory,
which is comfortable, so it's recommended with **medium** risk.
`busy-analytics-db` is left alone, and `brand-new-db` is skipped instead
of being reported as idle.

## How decisions are made

### Is an instance over-sized?

Both CPU **and** memory must be "always low" over the 14-day window:

| Resource | Average below | Peak below | Peak measured as |
|---|---|---|---|
| CPU | 20% | 50% | **95th percentile** of hourly peaks |
| Memory | 30% | 60% | true peak (lowest free memory seen) |

- **Why the 95th percentile for CPU?** In 95% of hours, the busiest
  minute stayed at or below this value. A handful of one-off spikes
  (a backup, a one-time migration) don't block a downgrade, but a
  regular busy period (a nightly batch job in 10% of hours) still does.
- **Why the true peak for memory?** Running out of CPU only slows a
  database down; running out of RAM makes it swap or crash.
- **Low CPU but busy memory (or the reverse):** not over-sized. Every
  step down in an RDS family halves vCPUs and RAM together.
- **Too little data** (under 50% of expected hourly datapoints):
  skipped, never reported as healthy.

### Which size to recommend?

Peak usage is scaled onto each smaller size in the same family
(15% of 8 vCPUs ≈ 60% of 2 vCPUs). The smallest size wins where:

- projected CPU p95 ≤ 75%
- projected memory peak ≤ 80%
- the single busiest CPU minute still fits (≤ 100%)

### Risk level

| Risk | Projected peak on the new size |
|---|---|
| low | ≤ 50% (and memory usage is known) |
| medium | ≤ 65% |
| high | above 65%, or the busiest CPU minute would exceed 90% |

### Cost

Monthly cost = on-demand hourly price × 730 hours, from a static table
in `src/aws_connector/pricing.py`, adjusted by region and doubled for
Multi-AZ. Savings = current cost − recommended cost, × 12 for yearly.

## Assumptions and limitations

- **Prices are estimates**, not your bill: on-demand, compute only.
  Storage, IOPS, backups and Reserved Instance discounts aren't included.
  Savings are still correct, because a downsize keeps the same storage.
- **CPU scales linearly** with vCPU count in the projection. Real
  workloads can be less linear (smaller buffer cache means more disk
  I/O, and therefore more CPU), which is why the limits leave headroom.
- **Burstable t3 instances** all have 2 vCPUs, so they are sized on
  memory only. CPU credit balance isn't checked.
- **14-day window:** monthly or quarterly peaks outside the window
  aren't seen.
- **Same-family only:** no cross-family moves (e.g. r5 → m5) in V1.
- **Supported instance types:** db.t3, db.r5 and db.m5 sizes. Other
  types are listed but not priced or recommended.

## Tests

```bash
python -m pytest tests/ -v
```

45 tests, using [moto](https://github.com/getmoto/moto) to mock RDS and
CloudWatch, so no AWS account is needed. They cover the connector,
metrics maths (including p95), analysis rules, recommendation and
savings maths, and a full end-to-end run.

## Team

| Area                         | Owner    |
|-------------------------------|----------|
| AWS Integration                | Person A |
| Metrics & Analysis             | Person B |
| Recommendations & Reports      | Person C |

## License

TBD
