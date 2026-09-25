# CostGuard

Find where you're wasting money on cloud databases.

CostGuard connects to your AWS account, checks how your RDS databases are
actually being used, flags instances that are over-provisioned, and
recommends cheaper instance types — with an estimate of how much you'd
save per month and per year.

## Status

 In development — V1 scope. See [docs/architecture.md](docs/architecture.md)
for the system design.

- [x] Week 1-2: Architecture & project structure
- [x] Week 3-4: AWS Connection — can list all RDS databases
- [ ] Week 5-6: CloudWatch metrics
- [ ] Week 7-8: Analysis & recommendations
- [ ] Week 9-10: HTML report
- [ ] Week 11-12: Polish, testing, demo

## How it works

1. Connect to your AWS account (read-only credentials)
2. List all RDS database instances
3. Pull CPU / memory / storage / connection usage from CloudWatch
4. Flag instances running well under capacity
5. Recommend a smaller instance type and calculate the savings
6. Generate an HTML report

## Example

```
Current:  db.r5.2xlarge   ($2,000/month)
Usage:    15% CPU, 20% memory
Recommend: db.r5.large    ($500/month)
Savings:  $1,500/month = $18,000/year
```

## Setup

See [SETUP.md](SETUP.md) for how to get AWS credentials and run the tool.

## Team

| Area                         | Owner    |
|-------------------------------|----------|
| AWS Integration                | Person A |
| Metrics & Analysis             | Person B |
| Recommendations & Reports      | Person C |

## License

TBD
