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
    """credential이 실제로 쓸 수 있는지 확인하고 permission_scope를 프로빙한다.

    위임(assume_role) 방식에서는 **AssumeRole이 성공하는 것 자체가 1차 검증**이고, 그 위에
    "빌린 역할이 정말 사용자가 등록한 그 계정의 것인지"까지 확인한다(confused deputy 방지 —
    공격자가 남의 Role ARN을 자기 계정인 척 등록하는 것을 막는다).

    이 계정 일치 검사는 **위임 방식에만** 적용한다. 레거시 access key 경로에 새 실패 사유를
    추가하면 이미 등록돼 동작 중인 credential이 재검증에서 갑자기 실패할 수 있기 때문이다.
    """
    from app.providers import AUTH_TYPE_ASSUME_ROLE, auth_type_of
    from app.providers.session import CredentialResolutionError, resolve_secret_payload

    is_delegated = auth_type_of(secret_payload) == AUTH_TYPE_ASSUME_ROLE
    try:
        secret_payload = resolve_secret_payload("aws", secret_payload)
    except CredentialResolutionError as exc:
        return VerificationResult(verified=False, error_code=exc.error_code)

    try:
        sts = _client(secret_payload, "sts", "us-east-1")
        identity = sts.get_caller_identity()
    except (BotoCoreError, ClientError, KeyError):
        return VerificationResult(verified=False, error_code="PROVIDER_AUTHENTICATION_FAILED")

    if is_delegated and identity.get("Account") != external_account_id:
        return VerificationResult(verified=False, error_code="CREDENTIAL_ACCOUNT_MISMATCH")

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


def get_cpu_utilization(secret_payload: dict, region: str, instance_ids: list[str]) -> dict[str, float | None]:
    """최근 1시간 평균 CPU 사용률(%)을 인스턴스별로 일괄 조회한다 — 보고서 "리소스 사용률 상위"
    섹션(2026-09-17)의 실데이터 소스. `GetMetricStatistics`(단건, 레거시)가 아니라
    `GetMetricData`(일괄, CSP가 권장하는 현재 방식)를 쓴다 — 인스턴스 수만큼 API를 왕복하지
    않고 한 번에 최대 500개 쿼리를 묶어 보낼 수 있다.

    메모리는 여기서 다루지 않는다 — `CWAgent mem_used_percent`는 인스턴스에 CloudWatch Agent가
    설치돼 있어야만 나오는데, 이 앱의 Terraform 모듈은 그 에이전트를 설치하지 않는다."""
    if not instance_ids:
        return {}

    now = dt.datetime.now(dt.timezone.utc)
    queries = [
        {
            "Id": f"m{i}",
            "MetricStat": {
                "Metric": {
                    "Namespace": "AWS/EC2",
                    "MetricName": "CPUUtilization",
                    "Dimensions": [{"Name": "InstanceId", "Value": instance_id}],
                },
                "Period": 300,
                "Stat": "Average",
            },
        }
        for i, instance_id in enumerate(instance_ids)
    ]

    try:
        cw = _client(secret_payload, "cloudwatch", region)
        resp = cw.get_metric_data(
            MetricDataQueries=queries,
            StartTime=now - dt.timedelta(hours=1),
            EndTime=now,
        )
    except (BotoCoreError, ClientError):
        return {instance_id: None for instance_id in instance_ids}

    # GetMetricData는 기본적으로 최신 시각 순(내림차순)으로 Values를 돌려준다 — [0]이 가장 최근값.
    result_by_id = {r["Id"]: r for r in resp.get("MetricDataResults", [])}
    out: dict[str, float | None] = {}
    for i, instance_id in enumerate(instance_ids):
        values = result_by_id.get(f"m{i}", {}).get("Values") or []
        out[instance_id] = round(values[0], 1) if values else None
    return out


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


def issue_cli_session(secret_payload: dict, *, duration_seconds: int = 900) -> dict:
    """SSM Session Manager 접속용 단기 AWS CLI 자격증명을 발급한다.

    계정에 저장된 access_key_id/secret_access_key를 그대로 사용자에게 내려주지 않고, STS
    GetSessionToken으로 짧게 만료되는(기본 15분) 임시 자격증명만 반환한다 — SSH 키 페어처럼
    오래 남는 비밀을 새로 만들지 않는다는 원칙(마이페이지 크리덴셜을 IAM Role/MFA로 옮기려는
    방향과 같은 선상, 2026-09-15).

    **위임(assume_role) credential은 GetSessionToken을 쓸 수 없다.** AWS가 세션 자격증명으로의
    호출을 거부한다(`AccessDenied: Cannot call GetSessionToken with session credentials`,
    2026-09-15 실제 계정으로 확인). 그럴 필요도 없다 — AssumeRole 결과 자체가 이미 단기
    자격증명이라 그대로 내려주면 된다. 다만 사용자 손에 직접 들어가는 값이므로 기본 세션
    수명(1시간)이 아니라 `duration_seconds`(기본 15분)로 더 짧게 끊는다.
    """
    from app.providers import AUTH_TYPE_ASSUME_ROLE, auth_type_of
    from app.providers.session import CredentialResolutionError, assume_role
    from app.resource_actions import ResourceActionError

    if auth_type_of(secret_payload) == AUTH_TYPE_ASSUME_ROLE:
        role_arn = secret_payload.get("role_arn")
        external_id = secret_payload.get("external_id")
        if not role_arn or not external_id:
            raise ResourceActionError("CREDENTIAL_VERIFICATION_FAILED")
        try:
            issued = assume_role(role_arn, external_id, duration_seconds=duration_seconds)
        except CredentialResolutionError as exc:
            # 실패 사유(신뢰 정책/ExternalId 문제 vs 플랫폼 설정 문제)를 그대로 올려 보낸다.
            raise ResourceActionError(exc.error_code) from exc
        expiration = issued.get("expiration")
        return {
            "access_key_id": issued["access_key_id"],
            "secret_access_key": issued["secret_access_key"],
            "session_token": issued["session_token"],
            "expires_at": (
                dt.datetime.fromisoformat(expiration)
                if expiration
                else dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=duration_seconds)
            ),
        }

    try:
        sts = _client(secret_payload, "sts", "us-east-1")
        result = sts.get_session_token(DurationSeconds=duration_seconds)
    except (BotoCoreError, ClientError) as exc:
        raise ResourceActionError("PROVIDER_API_ERROR") from exc

    creds = result["Credentials"]
    return {
        "access_key_id": creds["AccessKeyId"],
        "secret_access_key": creds["SecretAccessKey"],
        "session_token": creds["SessionToken"],
        "expires_at": creds["Expiration"],
    }


def list_network_resources(secret_payload: dict, region: str) -> dict:
    """프로비저닝 폼의 "기존 리소스 사용"에서 실제 VPC/서브넷/보안 그룹 목록을 보여주기 위한
    조회 전용 API(2026-09-17). `secret_payload`는 이미 해석된(위임이면 AssumeRole 결과) 자격증명을
    받는다고 가정한다 — `perform_resource_action()`/`discover_resources()`와 동일 관례로, 위임
    처리는 호출부(라우터)가 `resolve_secret_payload()`로 미리 해 둔다.

    실패 시(권한 부족 등) 빈 목록이 아니라 예외를 올린다 — 사용자가 "왜 하나도 안 보이지"를
    "권한이 없다"와 "진짜 하나도 없다"로 구분할 수 있어야 한다(리소스 동기화의 "0건은 대부분
    실패가 아니다" 문제와 반대 방향 결정).
    """
    from app.resource_actions import ResourceActionError

    ec2 = _client(secret_payload, "ec2", region)
    try:
        vpcs_resp = ec2.describe_vpcs()
        subnets_resp = ec2.describe_subnets()
        sgs_resp = ec2.describe_security_groups()
    except (BotoCoreError, ClientError) as exc:
        raise ResourceActionError("PROVIDER_API_ERROR") from exc

    def _name_tag(tags) -> str | None:
        for t in tags or []:
            if t.get("Key") == "Name":
                return t.get("Value")
        return None

    vpcs = [
        {
            "id": v["VpcId"],
            "cidr_block": v.get("CidrBlock"),
            "name": _name_tag(v.get("Tags")),
            "is_default": v.get("IsDefault", False),
        }
        for v in vpcs_resp.get("Vpcs", [])
    ]
    subnets = [
        {
            "id": s["SubnetId"],
            "vpc_id": s["VpcId"],
            "availability_zone": s.get("AvailabilityZone"),
            "cidr_block": s.get("CidrBlock"),
            "name": _name_tag(s.get("Tags")),
        }
        for s in subnets_resp.get("Subnets", [])
    ]
    security_groups = [
        {"id": g["GroupId"], "vpc_id": g.get("VpcId"), "name": g.get("GroupName")}
        for g in sgs_resp.get("SecurityGroups", [])
    ]
    return {"vpcs": vpcs, "subnets": subnets, "security_groups": security_groups}


def list_security_groups(secret_payload: dict, region: str) -> list[dict]:
    """보안그룹 관리 화면(2026-09-17)의 목록 조회 — 규칙까지 포함한 상세를 돌려준다.
    `list_network_resources()`의 `security_groups`(id/name/vpc_id 요약, 프로비저닝 폼의 "기존
    리소스 사용" 드롭다운용)와 달리 규칙 CRUD에 쓸 수 있는 전체 정보를 담는다.

    `describe_security_group_rules()`가 주는 `SecurityGroupRuleId`를 그대로 `rule_id`로 쓴다 —
    구버전 API처럼 `IpPermissions` 전체를 매칭해 삭제하는 방식보다 안전하다(2018년 이후 리전
    에선 전부 지원).
    """
    from app.resource_actions import ResourceActionError

    ec2 = _client(secret_payload, "ec2", region)
    try:
        groups_resp = ec2.describe_security_groups()
        rules_resp = ec2.describe_security_group_rules()
    except (BotoCoreError, ClientError) as exc:
        raise ResourceActionError("PROVIDER_API_ERROR") from exc

    rules_by_group: dict[str, list[dict]] = {}
    for r in rules_resp.get("SecurityGroupRules", []):
        rules_by_group.setdefault(r["GroupId"], []).append(
            {
                "rule_id": r["SecurityGroupRuleId"],
                "direction": "egress" if r.get("IsEgress") else "ingress",
                "protocol": r.get("IpProtocol"),
                "from_port": r.get("FromPort"),
                "to_port": r.get("ToPort"),
                "cidr": r.get("CidrIpv4") or r.get("CidrIpv6"),
                "description": r.get("Description"),
            }
        )

    groups = []
    for g in groups_resp.get("SecurityGroups", []):
        group_rules = rules_by_group.get(g["GroupId"], [])
        groups.append(
            {
                "id": g["GroupId"],
                "name": g.get("GroupName"),
                "description": g.get("Description"),
                "vpc_id": g.get("VpcId"),
                "ingress_rules": [r for r in group_rules if r["direction"] == "ingress"],
                "egress_rules": [r for r in group_rules if r["direction"] == "egress"],
            }
        )
    return groups


def create_security_group(secret_payload: dict, region: str, name: str, description: str, vpc_id: str) -> dict:
    from app.resource_actions import ResourceActionError

    ec2 = _client(secret_payload, "ec2", region)
    try:
        resp = ec2.create_security_group(GroupName=name, Description=description, VpcId=vpc_id)
    except (BotoCoreError, ClientError) as exc:
        raise ResourceActionError("PROVIDER_API_ERROR") from exc
    return {
        "id": resp["GroupId"],
        "name": name,
        "description": description,
        "vpc_id": vpc_id,
        "ingress_rules": [],
        "egress_rules": [],
    }


def delete_security_group(secret_payload: dict, region: str, group_id: str) -> None:
    from app.resource_actions import ResourceActionError

    ec2 = _client(secret_payload, "ec2", region)
    try:
        ec2.delete_security_group(GroupId=group_id)
    except (BotoCoreError, ClientError) as exc:
        raise ResourceActionError("PROVIDER_API_ERROR") from exc


def add_security_group_rule(
    secret_payload: dict,
    region: str,
    group_id: str,
    direction: str,
    protocol: str,
    from_port: int | None,
    to_port: int | None,
    cidr: str,
    description: str | None,
) -> dict:
    from app.resource_actions import ResourceActionError

    ec2 = _client(secret_payload, "ec2", region)
    ip_range: dict = {"CidrIp": cidr}
    if description:
        ip_range["Description"] = description
    ip_permission: dict = {"IpProtocol": protocol, "IpRanges": [ip_range]}
    if from_port is not None:
        ip_permission["FromPort"] = from_port
    if to_port is not None:
        ip_permission["ToPort"] = to_port

    try:
        if direction == "ingress":
            resp = ec2.authorize_security_group_ingress(GroupId=group_id, IpPermissions=[ip_permission])
        else:
            resp = ec2.authorize_security_group_egress(GroupId=group_id, IpPermissions=[ip_permission])
    except (BotoCoreError, ClientError) as exc:
        raise ResourceActionError("PROVIDER_API_ERROR") from exc

    added = (resp.get("SecurityGroupRules") or [{}])[0]
    return {
        "rule_id": added.get("SecurityGroupRuleId"),
        "direction": direction,
        "protocol": protocol,
        "from_port": from_port,
        "to_port": to_port,
        "cidr": cidr,
        "description": description,
    }


def remove_security_group_rule(secret_payload: dict, region: str, group_id: str, direction: str, rule_id: str) -> None:
    from app.resource_actions import ResourceActionError

    ec2 = _client(secret_payload, "ec2", region)
    try:
        if direction == "ingress":
            ec2.revoke_security_group_ingress(GroupId=group_id, SecurityGroupRuleIds=[rule_id])
        else:
            ec2.revoke_security_group_egress(GroupId=group_id, SecurityGroupRuleIds=[rule_id])
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
