"""
Measurements behind the report's non-functional requirements (NFR1-NFR4):

  * AWS API calls per run for 1, 5 and 12 databases (mocked AWS)
  * end-to-end time of `main()` against a mocked 12-database account
  * local analysis + report time for 10, 100 and 1,000 synthetic databases

Each timing runs once as a warm-up, then 5 times; results are mean and
standard deviation. No AWS account needed.

Run from the project root:
    python experiments/benchmark.py results/benchmark.json
"""
import json
import os
import random
import statistics
import sys
import tempfile
import time
from collections import Counter
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

sys.path.insert(0, os.getcwd())

import botocore.client
from moto import mock_aws

from src.analysis import analyze_instance, insufficient_data_result
from src.aws_connector.pricing import estimate_monthly_cost
from src.recommendations import build_recommendations
from src.report import build_report_context, render_report, write_report
import src.main as cg_main
from tests import test_main as tm

RUNS = 5
out_path = Path(sys.argv[1])
out_path.parent.mkdir(parents=True, exist_ok=True)
results = {}

# ---------------------------------------------------------------- API calls
calls = Counter()
_orig = botocore.client.BaseClient._make_api_call


def _counting(self, operation_name, api_params):
    calls[operation_name] += 1
    return _orig(self, operation_name, api_params)


os.environ.update(AWS_ACCESS_KEY_ID="testing", AWS_SECRET_ACCESS_KEY="testing", AWS_DEFAULT_REGION="us-east-1")
types = ["db.r5.2xlarge", "db.m5.xlarge", "db.t3.medium", "db.r5.large", "db.m5.2xlarge", "db.t3.large"]
api = {}
e2e = {}
for n in (1, 5, 12):
    with mock_aws():
        for i in range(n):
            tm._add_db(f"db-{i}", types[i % len(types)], cpu=10.0 + i, memory_used_pct=15)
        calls.clear()
        botocore.client.BaseClient._make_api_call = _counting
        try:
            with redirect_stdout(StringIO()):
                cg_main.main(["--no-report"])
        finally:
            botocore.client.BaseClient._make_api_call = _orig
        api[n] = dict(calls)
results["api_calls"] = api

# ------------------------------------------------- end-to-end with mocked AWS
with mock_aws():
    for i in range(12):
        tm._add_db(f"db-{i}", types[i % len(types)], cpu=10.0 + i, memory_used_pct=15)
    times = []
    with tempfile.TemporaryDirectory() as d:
        for r in range(RUNS + 1):
            t0 = time.perf_counter()
            with redirect_stdout(StringIO()):
                cg_main.main(["--output", os.path.join(d, "r.html")])
            if r:  # first run is warm-up
                times.append(time.perf_counter() - t0)
    results["e2e_mocked_12"] = {"mean": statistics.mean(times), "sd": statistics.stdev(times), "runs": times}

# ------------------------------------------------------- local processing
LADDER = ["db.t3.micro", "db.t3.small", "db.t3.medium", "db.t3.large",
          "db.r5.large", "db.r5.xlarge", "db.r5.2xlarge", "db.r5.4xlarge",
          "db.m5.large", "db.m5.xlarge", "db.m5.2xlarge"]


def fleet(n, seed=42):
    rnd = random.Random(seed)
    instances, res = [], []
    for i in range(n):
        itype = rnd.choice(LADDER)
        multi = rnd.random() < 0.3
        db = {"instance_id": f"db-{i:04d}", "instance_type": itype, "engine": rnd.choice(["postgres", "mysql"]),
              "region": "us-east-1", "monthly_cost": estimate_monthly_cost(itype, "us-east-1", multi),
              "storage_allocated_gb": rnd.choice([20, 100, 500]), "multi_az": multi}
        instances.append(db)
        if rnd.random() < 0.1:
            res.append((db, None, insufficient_data_result(db["instance_id"], "too new")))
            continue
        cpu_avg = rnd.uniform(2, 80)
        cpu_p95 = min(100.0, cpu_avg * rnd.uniform(1.1, 2.0))
        cpu_max = min(100.0, cpu_p95 * rnd.uniform(1.0, 1.6))
        mem_avg = rnd.uniform(5, 90)
        mem_max = min(100.0, mem_avg * rnd.uniform(1.0, 1.4))
        m = {"instance_id": db["instance_id"], "cpu_avg_pct": round(cpu_avg, 2), "cpu_p95_pct": round(cpu_p95, 2),
             "cpu_max_pct": round(cpu_max, 2), "cpu_min_pct": 0.5, "memory_avg_pct": round(mem_avg, 2),
             "memory_max_pct": round(mem_max, 2), "storage_used_gb": 10.0, "connections_avg": 5.0,
             "observation_window_days": 14}
        res.append((db, m, None))
    return instances, res


local = {}
with tempfile.TemporaryDirectory() as d:
    for n in (10, 100, 1000):
        instances, raw = fleet(n)
        totals = []
        stage = {"analyse": [], "recommend": [], "render": [], "write": []}
        size = 0
        n_recs = 0
        for r in range(RUNS + 1):
            t0 = time.perf_counter()
            results_ = [(db, m, analyze_instance(m)) if m else (db, m, a) for db, m, a in raw]
            t1 = time.perf_counter()
            recs, no_safe = build_recommendations(results_)
            t2 = time.perf_counter()
            html = render_report(build_report_context(instances, results_, recs, no_safe))
            t3 = time.perf_counter()
            path = write_report(html, os.path.join(d, f"r{n}.html"))
            t4 = time.perf_counter()
            if r:
                totals.append(t4 - t0)
                stage["analyse"].append(t1 - t0)
                stage["recommend"].append(t2 - t1)
                stage["render"].append(t3 - t2)
                stage["write"].append(t4 - t3)
            size = path.stat().st_size
            n_recs = len(recs)
        local[n] = {"mean": statistics.mean(totals), "sd": statistics.stdev(totals),
                    "stages": {k: statistics.mean(v) for k, v in stage.items()},
                    "report_bytes": size, "recommendations": n_recs}
results["local"] = local

out_path.write_text(json.dumps(results, indent=2))
print(json.dumps(results, indent=2))
