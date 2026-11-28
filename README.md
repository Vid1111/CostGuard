# CostGuard

Find where you're wasting money on cloud databases.

CostGuard connects to your AWS account, checks how your RDS databases are
actually being used, flags instances that are over-provisioned, and
recommends cheaper instance types, with an estimate of how much you'd
save per month and per year. The results are printed in the terminal
and written to a self-contained HTML report.

## Status

V1 scope. See [docs/architecture.md](docs/architecture.md) for the system design.

- [x] Week 1-2: Architecture & project structure
- [x] Week 3-4: AWS Connection: can list all RDS databases
- [x] Week 5-6: CloudWatch metrics: can show usage metrics for each database
- [x] Week 7-8: Analysis & recommendations: flags over-sized instances, recommends a smaller size, calculates savings
- [x] Week 9-10: HTML report: current spend, recommended changes, total savings, risk levels
- [x] Week 11: Polish & test: 79 automated tests, including a 12-database end-to-end run; Docker support
- [ ] Week 11-12: Run against a real AWS account with 5+ databases, record the demo video

## How it works

1. **Connect** to your AWS account with read-only credentials (`src/aws_connector/`)
2. **List** every RDS instance with its type, engine, region, storage, Multi-AZ and estimated monthly cost
3. **Measure** 14 days of CloudWatch usage per instance: CPU (avg / p95 / max / min), memory, storage, connections (`src/metrics/`)
4. **Analyse** which instances are over-sized (`src/analysis/`)
5. **Recommend** a smaller instance type in the same family and calculate the savings (`src/recommendations/`)
6. **Report** everything as an HTML page (`src/report/`)

## Quick start

```bash
pip install -r requirements.txt
cp .env.example .env        # then fill in your AWS keys
python -m src.main
```

Full instructions, including the IAM permissions to grant, are in [SETUP.md](SETUP.md).

| Command | What it does |
|---|---|
| `python -m src.main` | Full run: terminal summary + `reports/costguard-report-<timestamp>.html` |
| `python -m src.main --output my-report.html` | Choose where the report is written |
| `python -m src.main --no-report` | Terminal summary only |
| `python -m src.report.demo` | Sample report from 12 dummy databases, **no AWS account needed** |
| `python -m pytest tests/ -v` | Run all the tests (no AWS account needed) |

## Example

Three databases: one idle, one busy, one created yesterday.

```
Usage metrics (last 14 days):

Instance ID              CPU avg   CPU p95   CPU max   CPU min   Mem avg   Mem max   Verdict
--------------------------------------------------------------------------------------------
idle-orders-db           15.0%     15.0%     15.0%     15.0%     15.0%     15.0%     OVER-SIZED
busy-analytics-db        78.0%     78.0%     78.0%     78.0%     90.0%     90.0%     ok
brand-new-db               (skipped - Only 0/336 expected CPU datapoints found ...)

Recommendations:

Instance ID              Current         $/mo       Recommended     $/mo       Save $/mo   Save $/yr   Risk
-----------------------------------------------------------------------------------------------------------
idle-orders-db           db.r5.2xlarge   $700.80    db.r5.large     $175.20    $525.60     $6,307.20   medium

Total potential savings: $525.60/month = $6,307.20/year

1 instance(s) skipped - not enough CloudWatch data to judge.

HTML report written to C:\CostGuard\CostGuard\reports\costguard-report-20261128-093005.html
```

`idle-orders-db` uses 15% of 8 vCPUs and 15% of 64 GB RAM. On a
`db.r5.large` (2 vCPUs, 16 GB) that becomes about 60% CPU and 60% memory,
which is comfortable, so it's recommended with **medium** risk.
`busy-analytics-db` is left alone, and `brand-new-db` is skipped instead
of being reported as idle.

### The HTML report

One file with no external CSS, scripts or fonts, so it can be emailed,
opened offline or printed. It contains:

- **Summary:** current spend, potential savings, spend after the changes, and how many databases were over-sized, right-sized or skipped
- **Savings by risk level:** how much of the saving is low, medium or high risk
- **Recommended changes:** current → recommended type, both costs, monthly and yearly savings, projected usage on the new size, risk level and why
- **Needs attention:** skipped databases, over-sized ones with no safe smaller size, and unpriced instance types
- **All databases:** every database's usage and verdict
- **How decisions were made:** the thresholds, read straight from the code so the report can't disagree with it

Run `python -m src.report.demo` to see one built from 12 sample
databases (7 recommendations, $2,071.74/month).

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
- a step that only removes RAM (all t3 sizes have 2 vCPUs) is only taken when memory usage is known

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

## Docker

```bash
docker build -t costguard .
docker run --rm --env-file .env -v "$(pwd)/reports:/app/reports" costguard
```

The AWS keys are passed in from `.env` at run time and are never copied
into the image. The report lands in `./reports` on your machine. To run
the tests in the container:

```bash
docker run --rm --entrypoint python costguard -m pytest tests/ -q
```

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

79 tests, using [moto](https://github.com/getmoto/moto) to mock RDS and
CloudWatch, so no AWS account is needed:

| File | Covers |
|---|---|
| `test_aws_connector.py` | listing databases, pricing (region, Multi-AZ), error messages |
| `test_metrics_collector.py` | averages, memory %, storage, p95 vs max, too-little-data rules |
| `test_analysis.py` | over-sizing rules, including spikes, mixed CPU/memory and exact thresholds |
| `test_recommendations.py` | chosen size, savings to the cent, risk levels, sorting, spend totals |
| `test_report.py` | report figures, escaping, self-contained HTML, file writing and errors |
| `test_main.py` | the whole tool end to end, including a 12-database fleet ($1,197.93/month saved, worked out by hand) |

## Reproducing the report's measurements

The numbers in the project report come from two scripts in `experiments/`.
Neither needs an AWS account; both write a JSON file and print a summary.

```bash
python experiments/benchmark.py results/benchmark.json
python experiments/policy_comparison.py results/policy.json
```

- `benchmark.py`: AWS API calls per run (1 + 4N, read-only), end-to-end
  time against mocked AWS, and local analysis + report time for 10, 100
  and 1,000 databases (5 runs each after a warm-up).
- `policy_comparison.py`: sizing on averages vs on maximums vs
  CostGuard's rule, on five seeded fleets of 1,000 synthetic databases.

Timings depend on the machine; the API counts and the policy results
are deterministic.

## Team

| Area                         | Owner    |
|-------------------------------|----------|
| AWS Integration                | Person A |
| Metrics & Analysis             | Person B |
| Recommendations & Reports      | Person C |

## License

TBD
