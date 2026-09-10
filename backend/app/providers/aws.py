from __future__ import annotations

import datetime as dt

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.providers import VerificationResult

_TIMEOUT_CONFIG = Config(connect_timeout=5, read_timeout=8, retries={"max_attempts": 1})


def _client(secret_payload: dict, service: str, region: str):
    return boto3.client(
        service,
        aws_access_key_id=secret_payload.get("access_key_id"),
        aws_secret_access_key=secret_payload.get("secret_access_key"),
        aws_session_token=secret_payload.get("session_token") or None,
        region_name=region,
        config=_TIMEOUT_CONFIG,
    )


def verify(external_account_id: str, secret_payload: dict) -> VerificationResult:
    try:
        sts = _client(secret_payload, "sts", "us-east-1")
        identity = sts.get_caller_identity()
    except (BotoCoreError, ClientError, KeyError):
        return VerificationResult(verified=False, error_code="PROVIDER_AUTHENTICATION_FAILED")

    scope = {"inventory_read": False, "resource_control": False, "provision": False, "cost_read": False}

    try:
        ec2 = _client(secret_payload, "ec2", "ap-northeast-2")
        ec2.describe_instances(MaxResults=5)
        scope["inventory_read"] = True
    except (BotoCoreError, ClientError):
        pass

    try:
        ce = _client(secret_payload, "ce", "us-east-1")
        today = dt.date.today()
        ce.get_cost_and_usage(
            TimePeriod={
                "Start": today.replace(day=1).isoformat(),
                "End": today.isoformat(),
            },
            Granularity="MONTHLY",
            Metrics=["UnblendedCost"],
        )
        scope["cost_read"] = True
    except (BotoCoreError, ClientError):
        pass

    try:
        iam = _client(secret_payload, "iam", "us-east-1")
        actions = ["ec2:StartInstances", "ec2:StopInstances", "ec2:RunInstances"]
        result = iam.simulate_principal_policy(
            PolicySourceArn=identity["Arn"],
            ActionNames=actions,
        )
        allowed = {
            row["EvalActionName"]: row["EvalDecision"] == "allowed"
            for row in result.get("EvaluationResults", [])
        }
        scope["resource_control"] = allowed.get("ec2:StartInstances", False) and allowed.get(
            "ec2:StopInstances", False
        )
        scope["provision"] = allowed.get("ec2:RunInstances", False)
    except (BotoCoreError, ClientError):
        pass

    return VerificationResult(verified=True, permission_scope=scope)
