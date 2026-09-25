# Setup Guide

## 1. Requirements

- Python 3.11+
- An AWS account with at least one RDS instance (or dummy/sample data
  for early testing — see `docs/architecture.md`)

## 2. Get AWS Credentials

1. Log in to the [AWS Console](https://console.aws.amazon.com/).
2. Go to **IAM > Users** and create a new user (or use an existing one)
   for CostGuard.
3. Attach a **read-only** policy covering:
   - `rds:Describe*`
   - `cloudwatch:GetMetricStatistics` / `cloudwatch:GetMetricData`
   - `pricing:GetProducts` (if using live pricing lookups)
4. Under **Security credentials**, generate an **Access Key**.
5. Save the Access Key ID and Secret Access Key somewhere safe — do
   **not** commit them anywhere.

## 3. Configure the Project

```bash
git clone <repo-url>
cd costguard
python -m venv venv
source venv/bin/activate    # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` and fill in:

```
AWS_ACCESS_KEY_ID=your-access-key
AWS_SECRET_ACCESS_KEY=your-secret-key
AWS_DEFAULT_REGION=us-east-1
```

## 4. Run

```bash
python -m src.main
```

As of Week 3-4, this connects to AWS, lists every RDS instance in the
configured region, and prints a table with type, engine, region,
storage, and an estimated monthly cost for each. Metrics analysis and
the HTML report are not wired in yet — those land in Weeks 5-10.

## 5. Run the Tests

```bash
python -m pytest tests/ -v
```

Tests use [moto](https://github.com/getmoto/moto) to mock AWS, so
they run without real credentials or an AWS account. Before a demo
or a real milestone check, also run `python -m src.main` against an
actual sandbox AWS account (see section 2) — mocked tests only prove
the code path works, not that our assumptions about the real API
response shape are correct.

## 6. Output (Weeks 9-10)

The finished tool will generate an HTML report in the `reports/`
folder showing current spend, recommendations, and total potential
savings.

---

*This guide will be filled in further as the metrics collector,
analysis engine, and report generator are built (Weeks 5-10).*
