# CostGuard - run the RDS right-sizing analysis in a container.
#
# Build:
#   docker build -t costguard .
#
# Run (keys come from your .env file, never baked into the image;
# the report is written to ./reports on your machine):
#   docker run --rm --env-file .env -v "$(pwd)/reports:/app/reports" costguard
#
# Run the tests inside the container (no AWS account needed):
#   docker run --rm --entrypoint python costguard -m pytest tests/ -q

FROM python:3.13-slim

# no .pyc files, and print output straight away instead of buffering it
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# install dependencies first so Docker can cache this layer between code changes
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ src/
COPY tests/ tests/

# don't run as root
RUN useradd --create-home costguard && mkdir -p reports && chown costguard reports
USER costguard

ENTRYPOINT ["python", "-m", "src.main"]
