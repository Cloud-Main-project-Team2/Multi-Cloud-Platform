# AWS 역할 위임 연결 — IAM 설정 가이드

> 개념 설명은 `docs/AWS_AssumeRole_Delegation_Explainer.md`, 구현 설계는
> `docs/IAM_Delegation_and_Team_Budget_Design_2026-09-15.md`.
> 이 문서는 **콘솔에서 실제로 뭘 만들어야 하는지**만 다룬다.

## 0. 가장 많이 막히는 지점부터

**신뢰 정책 JSON을 "IAM → 정책 → 정책 생성"에 넣으면 만들어지지 않는다.** 정상이다.

IAM에는 성격이 다른 두 가지 문서가 있고, 둘은 만드는 곳이 다르다.

| | 권한 정책 (Permission Policy) | 신뢰 정책 (Trust Policy) |
|---|---|---|
| 붙는 곳 | 사용자·역할·그룹 | **역할에만**, 역할의 일부로 |
| 의미 | "무엇을 할 수 있나" | "누가 이 역할을 빌릴 수 있나" |
| `Principal` 필드 | **금지** | **필수** |
| 만드는 곳 | IAM → 정책 → 정책 생성 | IAM → **역할 생성** 중 "사용자 지정 신뢰 정책" |

마이페이지가 보여주는 JSON에는 `Principal`이 있으므로 **신뢰 정책**이다. 정책 생성 화면에
넣으면 `Has prohibited field Principal` 류의 에러가 난다. 별도로 만드는 것이 아니라 **역할을
만들 때 그 안에 넣는 값**이다.

## 1. 만들어야 하는 것 — 두 개

```
[플랫폼 계정: 우리 서비스]              [연결할 계정: 팀원/고객]
 ① IAM 사용자 mcp-platform-caller  ──▶  ② IAM 역할 MultiCloudOpsAccess
    권한: sts:AssumeRole 하나만            신뢰 정책: ①의 계정을 신뢰 + ExternalId 조건
    → 액세스 키를 .env에                   권한 정책: EC2/RDS/S3/CloudFront + 인라인 2개
```

①은 **한 번만** 만든다(서비스 전체에 하나). ②는 **연결할 계정마다** 하나씩 만든다.

## 2. ① 플랫폼 IAM 사용자 (서비스 운영자가 1회)

IAM → 사용자 → 사용자 생성, 이름 `mcp-platform-caller`. 콘솔 로그인 권한은 **끈다**
(프로그래밍 방식 액세스만).

권한은 아래 인라인 정책 **하나만** 준다. 이 JSON에는 `Principal`이 없으므로 "정책 생성"으로
만들어도 되고, 사용자에게 인라인으로 붙여도 된다.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "sts:AssumeRole",
      "Resource": "arn:aws:iam::*:role/MultiCloudOps*"
    }
  ]
}
```

- 다른 권한은 **하나도 주지 않는다.** 이 키가 유출돼도 "우리를 이미 신뢰하도록 설정해 둔
  역할"만 빌릴 수 있게 하는 것이 설계의 핵심이다.
- `Resource`를 역할 이름 접두사로 제한한다. 이 값은 `.env`의
  `PLATFORM_AWS_ASSUMABLE_ROLE_PATTERN`과 같아야 한다.
- ⚠️ **한 번 만든 뒤 지우지 않는다.** 같은 이름으로 다시 만들어도 내부 고유 ID가 달라진다.
  (그래서 역할의 신뢰 정책은 사용자 ARN이 아니라 계정 root를 Principal로 쓴다 — §3.)

발급한 액세스 키는 저장소 루트 `.env`에 넣는다(git 추적 대상이 아니다):

```
PLATFORM_AWS_ACCOUNT_ID=<12자리 계정 ID>
PLATFORM_AWS_ACCESS_KEY_ID=AKIA...
PLATFORM_AWS_SECRET_ACCESS_KEY=...
```

아래 둘은 기본값이 있어 생략 가능하다.

```
PLATFORM_AWS_ASSUMABLE_ROLE_PATTERN=arn:aws:iam::*:role/MultiCloudOps*
PLATFORM_AWS_SESSION_DURATION_SECONDS=3600
```

`docker-compose.yml`이 환경변수를 하나씩 명시 전달하므로, 변수를 추가할 때는 compose 파일에도
같이 넣어야 컨테이너에 전달된다.

## 3. ② 연결할 계정의 IAM 역할 (계정 소유자가 각자)

마이페이지 → AWS → 인증 방식 "역할 위임"을 열면 신뢰 정책 JSON이 표시된다. 그 화면의 값을
쓴다(아래는 형태 예시).

1. IAM → **역할(Roles)** → **역할 생성**
2. 신뢰할 수 있는 엔터티 유형: **사용자 지정 신뢰 정책** 선택
3. 마이페이지에 표시된 JSON을 **여기에** 붙여넣는다
   ```json
   {
     "Version": "2012-10-17",
     "Statement": [
       {
         "Effect": "Allow",
         "Principal": { "AWS": "arn:aws:iam::<플랫폼 계정 ID>:root" },
         "Action": "sts:AssumeRole",
         "Condition": { "StringEquals": { "sts:ExternalId": "<발급된 UUID>" } }
       }
     ]
   }
   ```
4. 권한 추가에서 체크: `AmazonEC2FullAccess`, `AmazonRDSFullAccess`, `AmazonS3FullAccess`,
   `CloudFrontFullAccess`
5. 역할 이름: **`MultiCloudOpsAccess`** (반드시 `MultiCloudOps`로 시작해야 한다 — §2의
   `Resource` 제한 때문)
6. 생성 후 그 역할의 **권한 탭 → 인라인 정책 추가**로 하나 더:
   ```json
   {
     "Version": "2012-10-17",
     "Statement": [
       {
         "Effect": "Allow",
         "Action": [
           "ce:GetCostAndUsage",
           "iam:SimulatePrincipalPolicy",
           "ec2:DescribeVpcs",
           "ec2:DescribeVpcAttribute",
           "ec2:DescribeSubnets",
           "ec2:DescribeSecurityGroups",
           "ec2:DescribeSecurityGroupRules",
           "ec2:DescribeAvailabilityZones"
         ],
         "Resource": "*"
       }
     ]
   }
   ```
   관리형 정책에 없어서 따로 붙인다. 비용 표시(`ce:`)와 권한 자동 판별(`iam:Simulate*`)에
   쓰이며, `ec2:DescribeVpcs`/`DescribeSubnets`/`DescribeSecurityGroups`는 프로비저닝 폼
   "기존 리소스 사용"이 실제 목록을 조회하는 데 쓴다(2026-09-17 추가). `ec2:DescribeVpcAttribute`
   /`DescribeAvailabilityZones`는 그것과 무관하게 **EC2 생성 자체**가 항상 거친다 —
   `terraform/aws/ec2/main.tf`의 `data "aws_vpc"`가 (VPC를 직접 지정하든 기본 VPC를 쓰든)
   매번 `enableDnsSupport`/`enableDnsHostnames` 속성을 읽고, `data "aws_availability_zones"`도
   서브넷을 새로 만들 필요가 없을 때조차 매 apply마다 평가되기 때문이다(2026-09-17 실제 AWS
   계정으로 EC2 생성을 테스트하다가 `ec2:DescribeVpcAttribute AccessDenied`로 막히는 걸
   발견했다). `ec2:DescribeSecurityGroupRules`는 보안그룹 관리 화면이 규칙을
   `SecurityGroupRuleId` 기준으로 조회하는 데 쓴다(§8). 전부 `AmazonEC2FullAccess`에 포함되지만,
   그 관리형 정책 없이(또는 콘솔에서 체크가 빠진 채) 좁은 인라인 정책만으로 역할을 만들면 이
   중 하나가 막혀 조회 실패 또는 **EC2 생성 자체 실패**로 이어진다.
7. **여기서 끝내지 말고 같은 인라인 정책 화면에서 statement를 하나 더 추가한다** — 없으면 EC2
   생성 자체가 실패한다(⚠️ 아래 참고):
   ```json
   {
     "Effect": "Allow",
     "Action": [
       "iam:CreateRole", "iam:GetRole", "iam:DeleteRole",
       "iam:ListRolePolicies", "iam:ListAttachedRolePolicies", "iam:ListRoleTags",
       "iam:TagRole", "iam:UntagRole", "iam:ListInstanceProfilesForRole",
       "iam:AttachRolePolicy", "iam:DetachRolePolicy",
       "iam:CreateInstanceProfile", "iam:GetInstanceProfile", "iam:DeleteInstanceProfile",
       "iam:AddRoleToInstanceProfile", "iam:RemoveRoleFromInstanceProfile",
       "iam:PassRole"
     ],
     "Resource": [
       "arn:aws:iam::*:role/mcp-ssm-*",
       "arn:aws:iam::*:instance-profile/mcp-ssm-*"
     ]
   }
   ```
   EC2 프로비저닝(`app/aws_provisioning.py`)은 SSH 키 대신 SSM Session Manager로 접속하게
   하려고 인스턴스마다 `mcp-ssm-*` IAM 역할/인스턴스 프로파일을 만든다
   (`terraform/aws/ec2/main.tf`). 이 역할이 그 IAM 리소스를 만들고 인스턴스에 넘길
   (`iam:PassRole`) 권한이 없으면 `iam:ListRolePolicies AccessDenied`로 EC2 생성이 막힌다
   (2026-09-17 실사용 중 발견 — terraform이 역할을 만든 직후 state 갱신을 위해 인라인/첨부
   정책 목록까지 읽는다). **`iam:PassRole`을 `Resource: "*"`로 주지 않는다** — 임의 역할에
   주면 위임 세션이 그 역할로 권한을 상승시킬 수 있어(예: 관리자 역할을 다른 리소스에 붙이는
   식), 우리가 만드는 `mcp-ssm-*` 역할/프로파일로만 좁힌다(§2의 `MultiCloudOps*` 이름 제한과
   같은 원칙).
8. **보안그룹 관리 화면**(프로비저닝과 별개로 SG를 직접 만들고 지우는 기능)을 쓰려면 statement를
   하나 더 추가한다:
   ```json
   {
     "Effect": "Allow",
     "Action": [
       "ec2:CreateSecurityGroup", "ec2:DeleteSecurityGroup",
       "ec2:AuthorizeSecurityGroupIngress", "ec2:AuthorizeSecurityGroupEgress",
       "ec2:RevokeSecurityGroupIngress", "ec2:RevokeSecurityGroupEgress",
       "ec2:CreateTags"
     ],
     "Resource": "*"
   }
   ```
   새로 만들 보안그룹의 ID는 생성 시점에야 정해져 `mcp-ssm-*` 같은 이름 접두사로 미리 좁힐 수
   없다 — 하지만 `iam:PassRole`(§7, 권한 상승 위험)과 달리 이 권한들은 자기 계정 안의 네트워크
   규칙만 바꿀 수 있어 `Resource: "*"`로 둬도 `AmazonEC2FullAccess`가 이미 부여하는 것과 실질적
   위험 수준이 같다. 없어도 EC2/RDS 생성 자체는 되고, 보안그룹 관리 화면에서 생성·규칙 추가만
   막힌다(§6의 조회는 이미 됨).
9. **인벤토리 "AWS CLI로 접속" 기능**(SSM Session Manager로 인스턴스에 셸 접속)을 쓰려면
   statement를 하나 더 추가한다:
   ```json
   {
     "Effect": "Allow",
     "Action": [
       "ssm:StartSession", "ssm:TerminateSession", "ssm:ResumeSession",
       "ssm:DescribeSessions", "ssm:DescribeInstanceInformation", "ssm:GetConnectionStatus"
     ],
     "Resource": "*"
   }
   ```
   `mcp-ssm-*` 역할에 붙는 `AmazonSSMManagedInstanceCore`(§7)는 "인스턴스가 SSM에 등록되는"
   권한이고, 이건 그것과 별개로 "**사용자**가 그 세션을 여는" 권한이라 위임 역할 쪽에 따로 있어야
   한다 — 없으면 `aws ssm start-session`이 `ssm:StartSession AccessDenied`로 막힌다(2026-09-17
   실사용 중 발견). `iam:PassRole`과 달리 이미 갖고 있는 EC2 전체 제어 권한(인스턴스 시작·중지·
   삭제) 이상으로 위험 범위를 넓히지 않아 `Resource: "*"`로 둔다.
10. 만들어진 **역할 ARN**을 마이페이지에 붙여넣고 저장한다. 계정 ID는 ARN에서 자동으로 읽는다.

### ⚠️ ExternalId — 가장 흔한 실패 원인

ExternalId는 **그 페이지를 열었을 때 발급된 값**이고 서버에 보관하지 않는다. 즉 **새로고침하면
다른 값이 나온다.**

역할을 이미 만들었다면, 등록할 때 External ID 칸에 **신뢰 정책에 넣은 그 값**을 넣어야 한다.
입력칸에 자동으로 채워진 새 값을 그대로 두면 `AccessDenied`가 난다.

### 권한 범위에 대해

최소 권한으로 조이는 것은 후속 과제로 둔다. 지금 조이면 Terraform이 VPC/보안그룹 생성 단계에서
막혀 디버깅에 시간을 쓰게 된다. 실제 과금이 발생하는 리소스를 만들 수 있는 권한이므로, 남의
계정을 연결할 때는 그 사실을 반드시 알린다(§5 안내문).

## 4. 같은 계정으로 테스트할 때

플랫폼 계정과 연결 대상 계정이 같아도 동작한다. 단 **양쪽이 모두 필요**하다.

- 역할의 신뢰 정책이 그 계정을 Principal로 지정 (§3)
- 그 계정의 `mcp-platform-caller` 사용자에게 `sts:AssumeRole` 권한 (§2)

다른 계정이면 신뢰 정책만으로 충분하지만, **같은 계정 안에서는 사용자 정책도 함께 있어야**
통과한다. 하나만 있으면 `AccessDenied`다.

## 5. 팀원에게 보낼 안내문 (복사용)

> **멀티클라우드 플랫폼 AWS 계정 연결 요청**
>
> 자기 AWS 계정에 IAM 역할 하나만 만들어주세요. **Access Key는 주지 않으셔도 됩니다.**
>
> 1. IAM → 역할 → 역할 만들기 → **사용자 지정 신뢰 정책** 선택
> 2. 아래 JSON 붙여넣기 (ExternalId는 개인별로 따로 전달드립니다)
>    ```json
>    { "Version": "2012-10-17",
>      "Statement": [{ "Effect": "Allow",
>        "Principal": { "AWS": "arn:aws:iam::<플랫폼 계정 ID>:root" },
>        "Action": "sts:AssumeRole",
>        "Condition": { "StringEquals": { "sts:ExternalId": "<ExternalId>" } } }] }
>    ```
> 3. 권한: `AmazonEC2FullAccess`, `AmazonRDSFullAccess`, `AmazonS3FullAccess`,
>    `CloudFrontFullAccess` + 마이페이지 → AWS → "역할 위임" 화면에 표시되는 인라인 정책
>    4개(비용/조회용 + `mcp-ssm-*` IAM 관리용 + 보안그룹 관리용 + SSM 세션 접속용, ⚠️ 두 번째가
>    없으면 EC2 생성이 실패합니다)를 그대로 복사해 붙여주세요
> 4. 역할 이름은 **`MultiCloudOpsAccess`** 로 만들어주세요
> 5. 만들어진 **역할 ARN**만 알려주시면 됩니다
>
> ⚠️ 이 역할로 EC2/RDS 등 **실제 과금되는 리소스를 생성·삭제**하는 테스트를 합니다. 테스트 후
> 정리는 제가 하지만, 비용 알림은 켜두시길 권장합니다.
>
> 연결을 끊고 싶으시면 **그 역할만 삭제**하시면 즉시 차단됩니다.

## 6. 문제 해결

| 증상 | 원인 / 조치 |
|---|---|
| 정책 생성 화면에서 신뢰 정책 JSON이 거부됨 | 정상. §0 — 역할 생성의 "사용자 지정 신뢰 정책"에 넣어야 한다 |
| 역할 만들기 3단계에서 이름을 넣었는데 "이름은 필수 항목"이라며 생성이 안 됨 | IAM 콘솔이 React라 **브라우저 자동완성/비밀번호 관리자가 채운 값은 입력 이벤트가 발생하지 않아** 폼 내부 상태가 빈 칸으로 남는다. 화면에는 글자가 보이는데 검증은 실패한다. 필드를 전부 지우고 **직접 타이핑**하면 해결된다(2026-09-15 실제로 겪음). 이름을 바꿔가며 시도해도 똑같이 실패하는 것이 이 증상의 단서다 — 이름이 원인이면 다른 이름으로는 됐어야 한다 |
| 콘솔 폼에서 계속 막힘 | CloudShell에서 `aws iam create-role --role-name MultiCloudOpsAccess --assume-role-policy-document file://trust.json` → `attach-role-policy` → `put-role-policy`로 우회한다. 콘솔과 달리 거부 사유(`MalformedPolicyDocument`/`EntityAlreadyExists`/`AccessDenied`)를 그대로 알려줘 원인 파악이 빠르다 |
| "역할을 빌릴 수 없습니다" (`CLOUD_PERMISSION_DENIED`) | STS는 원인을 구분해주지 않는다. ①역할 이름이 `MultiCloudOps`로 시작하는지 ②신뢰 정책의 계정 ID가 플랫폼 계정인지 ③ExternalId가 등록값과 같은지 — 셋을 순서대로 확인 |
| "역할이 속한 AWS 계정이 다릅니다" (`CREDENTIAL_ACCOUNT_MISMATCH`) | Role ARN의 계정과 실제 빌린 역할의 계정이 다르다. ARN을 다시 확인 |
| "서비스의 AWS 설정이 없습니다" (`PLATFORM_AWS_NOT_CONFIGURED`) | `.env`의 `PLATFORM_AWS_ACCOUNT_ID`가 비었거나 컨테이너에 전달되지 않았다. `docker compose exec api printenv \| grep PLATFORM`으로 확인 |
| "서비스의 AWS 자격 증명 설정에 문제" (`PLATFORM_AWS_AUTHENTICATION_FAILED` 계열) | 사용자 잘못이 아니다. 플랫폼 액세스 키가 만료·삭제됐거나 오타 |
| 같은 계정인데 `AccessDenied` | §4 — 사용자 쪽 `sts:AssumeRole` 권한이 빠졌을 가능성이 높다 |
| EC2 생성 job이 `CLOUD_PERMISSION_DENIED`로 실패, 원문에 `reading inline policies for IAM role mcp-ssm-...`/`ListRolePolicies`/`AssumeRole` 대상이 `MultiCloudOps*`가 자기 계정의 `mcp-ssm-*` 역할인 경우 | AssumeRole 자체는 성공했고, 그 뒤 terraform이 SSM용 IAM 역할을 만들다 막힌 것이다(§3-7의 두 번째 인라인 statement 누락, 2026-09-17 실사용 중 발견). 역할을 §3 이전 버전으로 만들어 뒀다면 인라인 정책에 `mcp-ssm-*` 관리 statement를 추가한다 |
| EC2 생성 job이 `CLOUD_PERMISSION_DENIED`로 실패, 원문에 `reading EC2 VPC ... Attribute (enableDnsHostnames)`/`DescribeVpcAttribute` | VPC/서브넷/보안 그룹을 **자동 생성**(기존 리소스 미지정)하는 경로에서도 항상 거치는 조회다 — `AmazonEC2FullAccess`를 안 붙였거나 콘솔에서 체크가 빠졌을 가능성이 높다. §3-6의 첫 번째 인라인 statement에 `ec2:DescribeVpcAttribute`/`DescribeAvailabilityZones`가 있는지 확인(2026-09-17 실사용 중 발견 — "기존 리소스 사용" 여부와 무관하게 모든 EC2 생성이 이 두 조회를 한다) |
| 코드를 고쳤는데 화면이 그대로 | 브라우저 캐시. 한 번 Ctrl+Shift+R. (2026-09-15에 `nginx/default.conf`에 `no-cache`를 넣어 이후로는 재발하지 않는다) |
| `aws ssm start-session`이 `AccessDeniedException: ... is not authorized to perform: ssm:StartSession` | 인벤토리 "AWS CLI로 접속"이 발급한 임시 자격증명은 위임 역할의 권한을 그대로 물려받는다 — §3-9의 SSM 세션 접속용 statement가 역할에 없다는 뜻. 추가하면 바로 해결된다(⚠️ 터미널에 붙여넣은 Access Key/Secret/Session Token은 짧게 만료되는 임시 값이어도 채팅에 남기지 않는다) |
