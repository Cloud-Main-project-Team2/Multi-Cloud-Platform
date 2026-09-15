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
         "Action": ["ce:GetCostAndUsage", "iam:SimulatePrincipalPolicy"],
         "Resource": "*"
       }
     ]
   }
   ```
   관리형 정책에 없어서 따로 붙인다. 비용 표시(`ce:`)와 권한 자동 판별(`iam:Simulate*`)에
   쓰이며, 없어도 연결 자체는 되고 해당 항목만 "권한 없음"으로 기록된다.
7. 만들어진 **역할 ARN**을 마이페이지에 붙여넣고 저장한다. 계정 ID는 ARN에서 자동으로 읽는다.

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
>    `CloudFrontFullAccess` + 인라인으로 `ce:GetCostAndUsage`, `iam:SimulatePrincipalPolicy`
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
| "역할을 빌릴 수 없습니다" (`CLOUD_PERMISSION_DENIED`) | STS는 원인을 구분해주지 않는다. ①역할 이름이 `MultiCloudOps`로 시작하는지 ②신뢰 정책의 계정 ID가 플랫폼 계정인지 ③ExternalId가 등록값과 같은지 — 셋을 순서대로 확인 |
| "역할이 속한 AWS 계정이 다릅니다" (`CREDENTIAL_ACCOUNT_MISMATCH`) | Role ARN의 계정과 실제 빌린 역할의 계정이 다르다. ARN을 다시 확인 |
| "서비스의 AWS 설정이 없습니다" (`PLATFORM_AWS_NOT_CONFIGURED`) | `.env`의 `PLATFORM_AWS_ACCOUNT_ID`가 비었거나 컨테이너에 전달되지 않았다. `docker compose exec api printenv \| grep PLATFORM`으로 확인 |
| "서비스의 AWS 자격 증명 설정에 문제" (`PLATFORM_AWS_AUTHENTICATION_FAILED` 계열) | 사용자 잘못이 아니다. 플랫폼 액세스 키가 만료·삭제됐거나 오타 |
| 같은 계정인데 `AccessDenied` | §4 — 사용자 쪽 `sts:AssumeRole` 권한이 빠졌을 가능성이 높다 |
| 코드를 고쳤는데 화면이 그대로 | 브라우저 캐시. 한 번 Ctrl+Shift+R. (2026-09-15에 `nginx/default.conf`에 `no-cache`를 넣어 이후로는 재발하지 않는다) |
