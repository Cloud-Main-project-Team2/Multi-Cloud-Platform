from __future__ import annotations

import datetime as dt

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.providers import VerificationResult

_TIMEOUT_CONFIG = Config(connect_timeout=5, read_timeout=8, retries={"max_attempts": 1})

# 동기화 대상 리전 — 프로비저닝 폼에서 이미 사용자에게 허용한 리전과 동일하게 좁힌다
# (서비스 컨텍스트 9절 리전 제한, `provisioning.js`의 COUNTRY_REGION). 전체 리전을 도는 것은
# 느리고 opt-in 리전에서 오류가 나기 쉬워 이번 세션 범위에서 제외했다.
_SYNC_REGIONS = ["ap-northeast-2", "us-east-1"]


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


def _empty_bucket(s3_client, bucket: str) -> None:
    paginator = s3_client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket):
        objects = [{"Key": obj["Key"]} for obj in page.get("Contents", [])]
        if objects:
            s3_client.delete_objects(Bucket=bucket, Delete={"Objects": objects})


def perform_resource_action(
    service_code: str,
    original_resource_type: str,
    action: str,
    secret_payload: dict,
    region: str | None,
    external_resource_id: str,
    force_empty: bool = False,
) -> None:
    from app.resource_actions import ResourceActionError

    region = region or "us-east-1"

    try:
        if service_code == "ec2" and original_resource_type != "EBS Volume":
            ec2 = _client(secret_payload, "ec2", region)
            if action == "start":
                ec2.start_instances(InstanceIds=[external_resource_id])
            elif action == "stop":
                ec2.stop_instances(InstanceIds=[external_resource_id])
            elif action == "delete":
                ec2.terminate_instances(InstanceIds=[external_resource_id])
            return

        if service_code == "ec2" and original_resource_type == "EBS Volume":
            ec2 = _client(secret_payload, "ec2", region)
            ec2.delete_volume(VolumeId=external_resource_id)
            return

        if service_code == "rds":
            rds = _client(secret_payload, "rds", region)
            if action == "start":
                rds.start_db_instance(DBInstanceIdentifier=external_resource_id)
            elif action == "stop":
                rds.stop_db_instance(DBInstanceIdentifier=external_resource_id)
            elif action == "delete":
                rds.delete_db_instance(DBInstanceIdentifier=external_resource_id, SkipFinalSnapshot=True)
            return

        if service_code == "s3":
            s3 = _client(secret_payload, "s3", region)
            try:
                s3.delete_bucket(Bucket=external_resource_id)
            except ClientError as exc:
                error_code = exc.response.get("Error", {}).get("Code")
                if error_code != "BucketNotEmpty":
                    raise
                if not force_empty:
                    raise ResourceActionError("BucketNotEmpty") from exc
                _empty_bucket(s3, external_resource_id)
                s3.delete_bucket(Bucket=external_resource_id)
            return

        raise ResourceActionError("UNSUPPORTED_OPERATION")
    except ResourceActionError:
        raise
    except (BotoCoreError, ClientError) as exc:
        raise ResourceActionError("PROVIDER_API_ERROR") from exc


def _instance_tags(tag_list) -> tuple[dict, str | None]:
    tags = {t["Key"]: t["Value"] for t in tag_list or []}
    return tags, tags.get("Name")


def discover_resources(secret_payload: dict) -> list:
    from app.resource_sync import DiscoveredResource

    results: list[DiscoveredResource] = []

    for region in _SYNC_REGIONS:
        try:
            ec2 = _client(secret_payload, "ec2", region)
            for page in ec2.get_paginator("describe_instances").paginate():
                for reservation in page.get("Reservations", []):
                    for instance in reservation.get("Instances", []):
                        state = (instance.get("State", {}) or {}).get("Name", "")
                        if state == "terminated":
                            continue
                        tags, name = _instance_tags(instance.get("Tags"))
                        results.append(
                            DiscoveredResource(
                                service_code="ec2",
                                external_resource_id=instance["InstanceId"],
                                original_resource_type="EC2 Instance",
                                name=name,
                                region=region,
                                status=state.upper() or None,
                                tags=tags,
                            )
                        )
            for page in ec2.get_paginator("describe_volumes").paginate():
                for volume in page.get("Volumes", []):
                    tags, name = _instance_tags(volume.get("Tags"))
                    results.append(
                        DiscoveredResource(
                            service_code="ec2",
                            external_resource_id=volume["VolumeId"],
                            original_resource_type="EBS Volume",
                            name=name,
                            region=region,
                            status=(volume.get("State") or "").upper() or None,
                            tags=tags,
                        )
                    )
        except (BotoCoreError, ClientError):
            pass  # 리전별 실패(예: opt-in 리전 미활성화)는 건너뛰고 계속한다

        try:
            rds = _client(secret_payload, "rds", region)
            for page in rds.get_paginator("describe_db_instances").paginate():
                for db_instance in page.get("DBInstances", []):
                    results.append(
                        DiscoveredResource(
                            service_code="rds",
                            external_resource_id=db_instance["DBInstanceIdentifier"],
                            original_resource_type="RDS Instance",
                            name=db_instance["DBInstanceIdentifier"],
                            region=region,
                            status=(db_instance.get("DBInstanceStatus") or "").upper() or None,
                            tags={},
                        )
                    )
        except (BotoCoreError, ClientError):
            pass

    try:
        # S3는 전역 서비스라 리전별 반복이 필요 없다. 버킷별 리전은 조회 비용이 커서
        # 이번 세션에서는 region=None으로 둔다(추후 개선 여지, CLAUDE.md 기록).
        s3 = _client(secret_payload, "s3", "us-east-1")
        for bucket in s3.list_buckets().get("Buckets", []):
            results.append(
                DiscoveredResource(
                    service_code="s3",
                    external_resource_id=bucket["Name"],
                    original_resource_type="S3 Bucket",
                    name=bucket["Name"],
                    region=None,
                    status="AVAILABLE",
                    tags={},
                )
            )
    except (BotoCoreError, ClientError):
        pass

    return results
