# 기능별 필요 권한 참조 (AWS / Azure / GCP)

## 이 문서의 목적

플랫폼은 **각자 자기 클라우드 계정을 위임(AWS AssumeRole) 또는 직접 등록(Azure/GCP 서비스
계정)해서 연결**하는 구조라, IAM 역할·RBAC 권한·서비스 계정 롤을 만들고 유지하는 건 **그
계정 소유자(회사)의 몫**이고 플랫폼이 대신해줄 수 없다(계정 경계를 넘어 쓰기 권한을 미리
받아두는 건 이 위임 구조를 만든 이유 자체와 배치된다).

이 문서는 그 판단을 위한 **참조표**다 — "이 기능을 쓰려면 이 권한이 필요하다"만 정리하고,
역할을 하나로 합칠지 기능별로 쪼갤지는 다루지 않는다(회사가 결정할 문제).

- **AWS**는 마이페이지 "역할 위임" 화면이 항상 최신 필요 권한을 JSON으로 보여준다 — 이 문서의
  AWS 절은 그 화면 뒤에 있는 논리를 설명하는 배경 자료다. 실제로 복사해 붙여넣을 값은 항상
  마이페이지를 기준으로 한다(코드가 바뀌면 화면도 같이 바뀌지만, 이 문서는 그때그때 안 바뀔 수
  있다).
- **Azure/GCP**는 마이페이지가 정책 JSON을 만들어주지 않는다(RBAC/서비스 계정 모델이 달라
  AWS처럼 서버가 정책 문서를 조립해줄 수 없다) — 이 문서가 유일한 안내다.

## AWS

권장 구성(마이페이지 "역할 위임" 화면과 동일): 관리형 정책 4개 + 인라인 정책(현재 4개
statement, 화면에서 항상 최신 값 확인) 조합. 아래는 그 뒤에 있는 "왜"를 기능별로 정리한 것.

### 관리형 정책 4개로 대부분 커버됨

`AmazonEC2FullAccess`, `AmazonRDSFullAccess`, `AmazonS3FullAccess`, `CloudFrontFullAccess`를
붙이면 아래가 전부 됨(서비스 전체에 대한 광범위한 권한이라 최소 권한 원칙과는 거리가 있다 —
`docs/AWS_Delegation_Role_Setup_Guide.md` §3 "권한 범위에 대해" 참고):

| 기능 | 대표 액션 |
|---|---|
| 인벤토리 조회·동기화 | `ec2:DescribeInstances/Volumes`, `rds:DescribeDBInstances`, `s3:ListAllMyBuckets` |
| 리소스 제어(시작/중지/삭제) | `ec2:StartInstances/StopInstances/TerminateInstances/DeleteVolume`, `rds:StartDBInstance/StopDBInstance/DeleteDBInstance`, `s3:DeleteBucket/DeleteObject/ListBucket` |
| EC2 프로비저닝 | `ec2:RunInstances`, `ec2:DescribeImages/Vpcs/Subnets/AvailabilityZones/SecurityGroups`, `ec2:CreateSecurityGroup`, `ec2:AuthorizeSecurityGroupIngress/Egress`, `ec2:CreateSubnet`, `ec2:CreateTags`, `ec2:TerminateInstances`(삭제) |
| RDS 프로비저닝 | `rds:CreateDBInstance`, `rds:CreateDBSubnetGroup`, `rds:AddTagsToResource`, `rds:DeleteDBInstance`(삭제) |
| S3 프로비저닝 | `s3:CreateBucket`, `s3:PutBucketPublicAccessBlock/Versioning`, `s3:GetBucket*`, `s3:DeleteBucket`(삭제) |
| CloudFront 프로비저닝 | `cloudfront:CreateDistribution/GetDistribution/UpdateDistribution/DeleteDistribution/TagResource` |
| 보안그룹 조회·규칙 조회 | `ec2:DescribeSecurityGroups/SecurityGroupRules` |

### 관리형 정책에 없어서 인라인으로 따로 추가해야 하는 것

관리형 정책 4개는 EC2/RDS/S3/CloudFront **서비스 자체**의 권한만 준다 — 아래는 다른 서비스
(IAM, Cost Explorer, SSM)라서 반드시 인라인 정책으로 추가해야 한다(마이페이지가 항상 최신
값을 보여준다):

| statement | 필요한 이유 | 액션 | Resource 스코핑 |
|---|---|---|---|
| ① 조회 보완 | 비용 표시(`ce:`), 권한 자동 판별(`iam:Simulate*`), "기존 리소스 사용" 조회, EC2 생성이 항상 거치는 VPC 속성/AZ 조회 | `ce:GetCostAndUsage`, `iam:SimulatePrincipalPolicy`, `ec2:DescribeVpcs/VpcAttribute/Subnets/SecurityGroups/SecurityGroupRules/AvailabilityZones` | `*` (전부 읽기 전용) |
| ② SSM 콘솔용 IAM 역할 관리 | EC2가 SSH 대신 SSM으로 접속하도록 인스턴스마다 `mcp-ssm-*` 역할/인스턴스 프로파일을 만듦 | `iam:CreateRole/GetRole/DeleteRole/ListRolePolicies/ListAttachedRolePolicies/ListRoleTags/TagRole/UntagRole/ListInstanceProfilesForRole/AttachRolePolicy/DetachRolePolicy/CreateInstanceProfile/GetInstanceProfile/DeleteInstanceProfile/AddRoleToInstanceProfile/RemoveRoleFromInstanceProfile/PassRole` | **`mcp-ssm-*`로 좁힘**(`iam:PassRole`은 임의 역할에 주면 권한 상승 위험) |
| ③ 보안그룹 관리 화면 | 프로비저닝과 별개로 SG를 직접 생성/삭제, 규칙 추가/삭제 | `ec2:CreateSecurityGroup/DeleteSecurityGroup/AuthorizeSecurityGroupIngress·Egress/RevokeSecurityGroupIngress·Egress/CreateTags` | `*`(EC2FullAccess가 이미 주는 것과 실질적으로 같은 위험 수준) |
| ④ AWS CLI 접속(SSM) | 인벤토리 "AWS CLI로 접속"이 실제로 `aws ssm start-session`을 열 수 있게 함 | `ssm:StartSession/TerminateSession/ResumeSession/DescribeSessions/DescribeInstanceInformation/GetConnectionStatus` | `*`(이미 있는 EC2 전체 제어 권한 이상으로 위험을 넓히지 않음 — 단, "이 플랫폼이 만든 인스턴스만"으로 더 좁히고 싶다면 `Condition: {"ssm:resourceTag/managed-by": "multi-cloud-platform"}` 추가 가능, 현재는 미적용) |

## Azure

Azure RBAC 내장 역할은 AWS IAM 정책보다 단위가 훨씬 크다 — 서비스별로 세밀하게 쪼개기
어렵다. 기능별로 필요한 것을 대략 매핑하면:

| 기능 | 필요 권한 (Azure 리소스 프로바이더 액션) | 대응하는 내장 역할(대략) |
|---|---|---|
| 계정 검증 | 구독 읽기(`Microsoft.Resources/subscriptions/read`), 리소스 목록 조회 | **Reader** |
| VM 조회·시작·중지·삭제 | `Microsoft.Compute/virtualMachines/read`, `.../start/action`, `.../deallocate/action`, `.../delete` | **Virtual Machine Contributor** |
| 기존 리소스 조회(VNet/NSG/서브넷) | `Microsoft.Network/virtualNetworks/read`, `.../subnets/read`, `Microsoft.Network/networkSecurityGroups/read` | **Reader**로 충분 |
| 보안그룹(NSG) 관리 | 위 조회 + `networkSecurityGroups/write·delete`, `.../securityRules/read·write·delete` | **Network Contributor** |
| VM 프로비저닝 | 리소스그룹/VNet/서브넷/NSG/PublicIP/NIC/VM 전체 `read·write·delete` | **Network Contributor + Virtual Machine Contributor** |
| Storage 프로비저닝 | `Microsoft.Storage/storageAccounts/*` | **Storage Account Contributor** |
| CDN(Front Door) 프로비저닝 | `Microsoft.Cdn/profiles/*`, `afdEndpoints/*`, `originGroups/*`, `origins/*`, `routes/*` | CDN Profile Contributor급 커스텀 역할 |
| DB 프로비저닝(MySQL/PostgreSQL/SQL Server) | VNet/Subnet/PrivateDnsZone 권한 + 각 DB 네임스페이스의 서버·DB `read·write·delete` | Contributor급(서비스 전용 내장 역할 없음) |

**결론**: 이 플랫폼의 프로비저닝 기능을 전부 쓰려면 사실상 구독 또는 리소스그룹 범위의
**Contributor** 역할이 필요하다. AWS처럼 "서비스별 FullAccess 4개 + 인라인 보완"으로 잘게
쪼개는 게 Azure RBAC 구조상 어렵다 — 회사가 더 세밀하게 나누고 싶다면 위 표 기준으로 커스텀
역할을 직접 만들어야 한다.

## GCP

| 기능 | 필요 권한(대표) | 대응하는 사전 정의 역할 |
|---|---|---|
| 계정 검증 | `resourcemanager.projects.get`, `compute.instances.list` | **Viewer** |
| Compute 조회·제어·동기화 | `compute.instances.start/stop/delete/get/list` | **Compute Instance Admin** |
| 기존 네트워크 조회 / 방화벽 관리 | `compute.networks.list`, `compute.firewalls.list/get/insert/delete` | **Compute Security Admin** |
| Compute 프로비저닝 | `compute.instances.insert/delete`, `compute.disks.*`, `compute.firewalls.insert`, `compute.networks.get`, `compute.subnetworks.use` | **Compute Admin** |
| Cloud SQL 프로비저닝 | `cloudsql.instances.create/get/delete`, `cloudsql.users.create`, `servicenetworking.services.addPeering`, `compute.globalAddresses.create`, `serviceusage.services.enable`(API 자동 활성화) | **Cloud SQL Admin** + **Service Networking Admin** |
| Cloud Storage 프로비저닝 | `storage.buckets.create/get/delete` | **Storage Admin** |
| Cloud CDN 프로비저닝 | `storage.buckets.*` + `compute.backendBuckets.*`, `compute.urlMaps.*`, `compute.targetHttpProxies.*`, `compute.globalForwardingRules.*` | **Compute Admin** 범위 안에 포함 |

**결론**: 전체 기능을 쓰려면 대략 `roles/compute.admin` + `roles/cloudsql.admin` +
`roles/storage.admin` + `roles/servicenetworking.networksAdmin` 조합이 필요하다.

## 남는 한계 (참고)

- 이 표는 코드 조사 시점(2026-09-17) 기준이다 — 기능이 추가되면 실제 필요 권한도 늘어날 수
  있고, 이 문서가 그때마다 자동으로 갱신되지는 않는다(AWS만 마이페이지가 항상 최신 값을 보여줌).
- 권한이 부족한 상태로 기능을 실행하면 서버가 CSP의 원문 오류를 그대로 사용자에게 보여주고
  "권한이 부족합니다 — 역할/정책에 권한을 추가하세요" 안내를 자동으로 붙인다
  (`app/error_patterns.py`, 2026-09-17 추가) — 이 문서를 못 봤거나 최신이 아니어도, 실행 시점의
  에러 메시지 자체가 무엇이 빠졌는지 알려주도록 설계했다.
- Azure/GCP는 AWS만큼 세밀한 프로빙(`verify()`의 `permission_scope`)이 구현돼 있지 않다 —
  `inventory_read`만 확인하고 나머지는 항상 `false`로 고정돼 있다(CLAUDE.md 결정 기록,
  2026-09-10). 즉 Azure/GCP 자격 증명은 등록 시점에 "이 권한이 있는지"를 미리 확인해주지
  않고, 실제로 그 기능을 실행해봐야 알 수 있다.
