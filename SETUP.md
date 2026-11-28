# Setup Guide

## 1. Requirements

- Python 3.11+
- An AWS account with at least one RDS instance. To try the tool
  without AWS, see section 6 (sample report) and section 5 (tests).
- Optional: Docker (section 7)

## 2. Get AWS Credentials

1. Log in to the [AWS Console](https://console.aws.amazon.com/).
2. Go to **IAM > Users** and create a new user (or use an existing one)
   for CostGuard.
3. Attach a **read-only** policy. CostGuard only calls these two actions:

   ```json
   {
     "Version": "2012-10-17",
     "Statement": [{
       "Effect": "Allow",
       "Action": ["rds:DescribeDBInstances", "cloudwatch:GetMetricStatistics"],
       "Resource": "*"
     }]
   }
   ```

   It never needs write access: nothing in your account is changed.
4. Under **Security credentials**, generate an **Access Key**.
5. Save the Access Key ID and Secret Access Key somewhere safe. Do
   **not** commit them anywhere.

## 3. Configure the Project

```bash
git clone https://github.com/Vid1111/CostGuard.git
cd CostGuard
python -m venv venv
source venv/bin/activate    # Windows PowerShell: venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp .env.example .env        # Windows: copy .env.example .env
```

Edit `.env` and fill in:

```
AWS_ACCESS_KEY_ID=your-access-key
AWS_SECRET_ACCESS_KEY=your-secret-key
AWS_DEFAULT_REGION=us-east-1
```

Set `AWS_DEFAULT_REGION` to the region your databases are in. `.env`
is listed in `.gitignore`, so it is never committed.

## 4. Run

Always run from the project folder, with `python -m`:

```bash
python -m src.main
```

This connects to AWS, lists every RDS instance in the region, pulls 14
days of CloudWatch usage (CPU, memory, storage, connections) for each
one, flags over-sized instances, prints the recommended downgrades
with monthly/yearly savings and a risk level, and writes an HTML report
to `reports/costguard-report-<timestamp>.html`.

Options:

```bash
python -m src.main --output my-report.html   # choose the report path
python -m src.main --no-report               # terminal summary only
```

A database younger than about 7 days (under half of the 14-day window)
or one CloudWatch has no data for is shown as "skipped" with a reason,
rather than reported as idle.

If something is wrong you get a plain message instead of a crash, for
example "Missing AWS credentials", "AWS rejected the credentials" or
"lack permission to call rds:DescribeDBInstances". The program exits
with code 1 in those cases.

## 5. Run the Tests

```bash
python -m pytest tests/ -v
```

79 tests. They use [moto](https://github.com/getmoto/moto) to mock AWS
(both RDS and CloudWatch), so they run without real credentials or an
AWS account. Before a demo, also run `python -m src.main` against a
real sandbox account: mocked tests prove the code works, not that real
AWS answers the way we expect.

## 6. Sample Report (no AWS needed)

```bash
python -m src.report.demo
```

Builds `reports/costguard-demo.html` from 12 dummy databases. The
numbers go through the real analysis and recommendation code, so it
shows exactly what the tool produces. Use `--output` to choose the path.

## 7. Docker

```bash
docker build -t costguard .
docker run --rm --env-file .env -v "$(pwd)/reports:/app/reports" costguard
```

On Windows PowerShell use `-v "${PWD}/reports:/app/reports"`. The keys
come from `.env` at run time and are never baked into the image.

## 8. Testing on a Real Account

For the "works on a real account" and "5+ databases" checks:

1. Create 5 small databases (for example `db.t3.micro`) in one region.
2. Leave them running about 7 days so CloudWatch has enough history.
3. Run `python -m src.main` and keep the report.
4. **Delete the databases afterwards**, since they cost money while running.
