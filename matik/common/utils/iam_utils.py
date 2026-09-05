"""AWS IAM utilities for role assumption."""

import boto3

from common.utils import log_utils

logger = log_utils.get_logger(__name__)


def assume_role(
    role_arn: str,
    region: str | None = None,
    session_name: str = "matik-session",
    session: boto3.Session | None = None,
) -> boto3.Session:
    """
    Assume an IAM role and return a boto3 session with the assumed credentials.

    For chained role assumption, pass the session from a previous assume_role call:
        session1 = assume_role("arn:aws:iam::111:role/First")
        session2 = assume_role("arn:aws:iam::222:role/Second", session=session1)

    Args:
        role_arn: ARN of the IAM role to assume
        region: AWS region for the session
        session_name: Name for the assumed role session
        session: Existing session to use for assumption (for chained roles).
                 If None, uses default credentials.

    Returns:
        boto3.Session with assumed role credentials

    Raises:
        botocore.exceptions.ClientError: If role assumption fails
    """
    logger.info("assuming IAM role", role_arn=role_arn, region=region)

    # Use provided session or default credentials
    if session:
        sts_client = session.client("sts", region_name=region)
    else:
        sts_client = boto3.client("sts", region_name=region)

    # Assume the specified IAM role
    logger.info("calling sts:AssumeRole")
    assumed_role = sts_client.assume_role(
        RoleArn=role_arn,
        RoleSessionName=session_name,
    )
    logger.info("role assumed successfully")

    # Create session with assumed role credentials
    credentials = assumed_role["Credentials"]
    return boto3.Session(
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"],
        region_name=region,
    )


def get_session(
    role_arn: str | None = None,
    region: str | None = None,
    session_name: str = "matik-session",
) -> boto3.Session:
    """
    Get a boto3 session.

    - If role_arn is provided: assumes the role and returns session with assumed credentials
    - If role_arn is None: returns default session (uses env vars, instance profile, etc.)

    Args:
        role_arn: ARN of IAM role to assume, or None for default credentials
        region: AWS region for the session
        session_name: Name for the assumed role session (only used when assuming)

    Returns:
        boto3.Session with appropriate credentials
    """
    if role_arn:
        return assume_role(role_arn, region, session_name)
    logger.info("using default boto3 session (no role assumption)", region=region)
    return boto3.Session(region_name=region)
