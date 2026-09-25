import os
import boto3
from dotenv import load_dotenv

load_dotenv()  # no-op if .env doesn't exist


def get_session() -> boto3.Session:
    """
    Build a boto3 Session from environment variables.

    Raises RuntimeError with a clear message if required credentials
    are missing, rather than letting boto3 fail later with a cryptic
    NoCredentialsError deep inside a client call.
    """
    access_key = os.getenv("AWS_ACCESS_KEY_ID")
    secret_key = os.getenv("AWS_SECRET_ACCESS_KEY")
    region = os.getenv("AWS_DEFAULT_REGION", "us-east-1")

    if not access_key or not secret_key:
        raise RuntimeError(
            "Missing AWS credentials. Set AWS_ACCESS_KEY_ID and "
            "AWS_SECRET_ACCESS_KEY in your .env file."
        )

    return boto3.Session(
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name=region,
    )


def get_rds_client(session: boto3.Session = None):
    """Return a boto3 RDS client, building a session if one isn't passed in."""
    session = session or get_session()
    return session.client("rds")
