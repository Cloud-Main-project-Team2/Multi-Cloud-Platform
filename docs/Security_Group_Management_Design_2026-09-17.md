# 보안그룹 관리 기능 설계 결정 (2026-09-17)

프로비저닝에서 EC2/RDS 등을 생성할 때 자동으로 만들어지는 보안그룹과 별개로, 사용자가
사이드바 "보안그룹" 화면에서 AWS Security Group / Azure NSG / GCP 방화벽 규칙을 직접
생성·조회·삭제하고 규칙을 추가·삭제하는 기능. 확정된 12개 화면 설계에는 없던 새 화면이다 —
화면설계서의 `SEC-01`("보안 점검")과는 다른 기능이다(SEC-01은 위반 규칙을 읽기 전용으로
탐지하는 확장 기능이고 아직 미구현이며, 규칙 저장용 테이블도 없다). S3는 보안그룹 개념이
없어 범위에서 제외했다.

## 1. DB에 저장하지 않는다

`app/routers/credentials.py`의 `GET /credentials/{id}/network-resources`(조회 전용, 프로비저닝
폼의 "기존 리소스 사용" 드롭다운용)와 같은 원칙 — 매 요청마다 CSP를 직접 조회/변경하고 로컬에
캐시하지 않는다. 보안그룹 규칙은 자주 바뀌고 조회 비용도 싸서(단일 API 호출) 캐시할 이유가
없다. `resources` 테이블은 `resource_sync_jobs` 파이프라인(Terraform이 만든 것 또는 discover된
것 전제)에 맞춰져 있어, 이 기능처럼 자주 바뀌는 비-Terraform 엔터티를 억지로 끼워 넣으면 새로운
sync-item 의미를 발명해야 한다.

그래서 `audit_events`도 남기지 않는다 — API 명세서 §14는 기록 대상 action을 고정 목록으로
정의하는데 보안그룹 관련 action이 없고, 이 기능은 DB에 target_id로 쓸 만한 행이 애초에 없다
(resource-sync가 같은 이유로 감사 로그를 안 남기는 전례를 그대로 따른다).

## 2. GCP는 "그룹"이 없다 — API 모양의 비대칭을 숨기지 않는다

AWS Security Group과 Azure NSG는 "그룹 하나 + 그 안의 규칙 여러 개"인데, GCP는 **방화벽 규칙
자체가 최상위 객체**다(그룹이 없다. VPC 네트워크 전역(global)에 적용되고, 인스턴스와의 연결은
target tags/서비스 계정으로 한다 — AWS SG/Azure NSG처럼 리소스에 "붙이는" 개념이 아니다).

이 차이를 API 경로 하나로 억지로 통일하지 않고 명시적으로 드러낸다:

```
GET    /credentials/{id}/security-groups?region=            목록(규칙 포함 상세)
POST   /credentials/{id}/security-groups?region=            그룹 생성(AWS/Azure) / 규칙 생성(GCP)
GET    /credentials/{id}/security-groups/{group_id}?region= 단건 상세
DELETE /credentials/{id}/security-groups/{group_id}?region= 그룹 삭제(AWS/Azure) / 규칙 삭제(GCP)
POST   /credentials/{id}/security-groups/{group_id}/rules            규칙 추가(AWS/Azure 전용)
DELETE /credentials/{id}/security-groups/{group_id}/rules/{rule_id}  규칙 삭제(AWS/Azure 전용)
```

- GCP는 `group_id`가 곧 방화벽 규칙의 이름이다. `POST .../security-groups` 한 번으로 완성된
  규칙을 만들고, `.../rules` 서브 엔드포인트 2개는 GCP 크리덴셜로 호출하면 `422
  VALIDATION_ERROR`로 명시적으로 거부한다(구조적 불일치를 숨기지 않는다).
- 프론트(`security-groups.js`)도 이 차이를 그대로 반영한다 — AWS/Azure는 "그룹 카드 → 펼치면
  규칙 표"(중첩), GCP는 "방화벽 규칙 평면 표"다. 통일하려 하지 않는다.

## 3. Azure는 URL에 ARM 리소스 ID를 넣지 않는다

처음엔 `list_network_resources`가 이미 주는 `network_security_groups[].id`(ARM 리소스 ID
전체, `/subscriptions/.../resourceGroups/.../networkSecurityGroups/nsg1`)를 그대로 `group_id`
경로 파라미터로 쓰려고 했는데, ARM ID엔 `/`가 섞여 있어 `.../rules/{rule_id}` 같은 하위 경로와
라우팅이 충돌한다(구현 중 실제로 겪고 수정함). 그래서 Azure는:

- `group_id` = NSG **이름**만(ARM ID 아님).
- `resource_group` = 별도 쿼리 파라미터.

`list_network_resources`가 이미 `network_security_groups[].resource_group`을 필드로 내려주므로
프론트가 추가로 조회할 필요는 없다.

## 4. 권한

**AWS**: `aws_delegation_setup`(`app/routers/credentials.py`)의 인라인 정책에 3번째 statement로
`ec2:CreateSecurityGroup`/`DeleteSecurityGroup`/`AuthorizeSecurityGroupIngress·Egress`/
`RevokeSecurityGroupIngress·Egress`/`CreateTags`를 `Resource: "*"`로 추가했다. 새로 만들 SG의
ID는 생성 시점에야 정해져 `mcp-ssm-*` 같은 이름 접두사 스코핑이 불가능하다 — 하지만 이 권한들은
`iam:PassRole`(권한 상승 위험, 별도 statement)과 달리 자기 계정 안의 네트워크 규칙만 바꿀 수
있어 `Resource: "*"`로 둬도 `AmazonEC2FullAccess`가 이미 부여하는 것과 실질적으로 같은 위험
수준이다. 조회용 `ec2:DescribeSecurityGroupRules`도 첫 번째 statement에 추가했다(규칙을
`SecurityGroupRuleId` 기준으로 정확히 지정해 삭제하려면 필요).

실제 AWS 계정(`credential_id=9`, `MultiCloudOps01` 역할)으로 생성→규칙 추가→규칙 삭제→그룹
삭제까지 라이브로 전부 확인했다 — `AmazonEC2FullAccess`가 이미 붙어 있어 새 statement 없이도
바로 성공했다(관리형 정책이 정상 부착된 경우의 기본 동작).

**Azure/GCP**: 코드 변경 없음. 고객이 자기 콘솔에서 설정하는 RBAC 역할(Azure: Network
Contributor 또는 `Microsoft.Network/networkSecurityGroups/*`+`securityRules/*` 커스텀 롤)/
서비스 계정 IAM 롤(GCP: `roles/compute.securityAdmin`)이 필요하다 — AWS처럼 서버가 정책 JSON을
만들어주는 구조가 아니다.

**사전 권한 게이트 없음**: `resources.py`(`_process_action_item`, 코드 440번째 줄 주석)의 기존
결정을 그대로 따른다 — `permission_scope.resource_control`을 사전 차단에 쓰지 않는다(AWS
`iam:SimulatePrincipalPolicy` 프로빙이 EC2 전용 키에서 false negative를 낸다). 진짜 게이트는
CSP SDK 호출 실패 시 `502 PROVIDER_API_ERROR`로 두는 것으로 통일했다.

## 5. 남은 한계

- GCP 방화벽 목록은 프로젝트 전체를 한 번에 조회한다(네트워크별 필터 없음) — 방화벽 규칙 수가
  많은 실 계정에서는 느릴 수 있다.
- 페이지네이션 없음 — 계정당 SG/규칙 수가 적다는 전제(API 명세서 §1.2도 목록 페이지네이션
  정책을 플랫폼 전체에서 아직 확정하지 않았다).
- Azure만 실 계정 테스트를 하지 못했다(테스트 계정 없음) — 단위 테스트(monkeypatch)로만
  검증했고, `NetworkManagementClient`/`ClientSecretCredential` 패턴은 기존
  `list_network_resources`/`perform_resource_action`이 이미 실 계정으로 검증한 것과 동일하다.
