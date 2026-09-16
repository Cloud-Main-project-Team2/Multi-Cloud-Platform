# 자격증명 저장·IAM 위임 구조 보안 점검 결과

작성일: 2026-09-16
배경: [티빙 해킹 사건 보도](https://www.boannews.com/news/articleView.html?idxno=145629)(개발환경 키 탈취 → 소스코드에
하드코딩된 운영 접속키[사실상 마스터키] 발견 → 3,954만 계정 유출)를 계기로, 우리 서비스의
자격증명 저장·사용 구조가 같은 유형의 위험을 안고 있는지 재점검한 결과.
방법: Claude Code(저장소 직접 접근 세션)가 실제 파일을 열어 근거를 인용하며 판정. 코드로
확인하지 못한 부분은 "확인 불가"로 명시하고 추측하지 않음.
점검 시점 커밋: `c1fee31`(#67) 기준. IAM 위임 관련 코드는 #58/#59/#64에서 도입됨.

> 관련 설계·가이드 문서: `docs/IAM_Delegation_and_Team_Budget_Design_2026-09-15.md`,
> `docs/AWS_Delegation_Role_Setup_Guide.md`, `docs/AWS_AssumeRole_Delegation_Explainer.md`,
> `docs/change_iam.md`(설계안 적용 가능성 검증).

---

## 0. 한 줄 결론

AWS는 최근 도입된 **역할 위임(AssumeRole + ExternalId + 역할 이름 접두사 제한)** 으로 티빙 사건의
핵심 원인(과대권한 마스터키)과 **정반대 방향**으로 설계돼 있다. 단, 그 범위 제한이 **코드가 아닌
AWS 콘솔 IAM 정책에만** 존재하고, **커밋된 자격증명 암호화 키 폴백**·**실시간 탐지 부재**·**빌리는
역할의 과대권한(FullAccess)**·**Azure/GCP 장기 키 병존**이 남은 리스크다.

---

## 1. 하드코딩 / 커밋 이력 — 대체로 양호, 예외 1건

**양호:**
- 작업트리·git 전체 히스토리에 실제 AWS 키(`AKIA…`)·OpenAI 키(`sk-…`)·private key 블록 **0건**.
  검색된 것은 테스트 픽스처(`AKIAFAKE`)와 AWS 공식 문서의 유명 예시 키
  (`wJalrXUtnFEMI/K7MDENG/EXAMPLEKEY`, `backend/app/seed_mock_data.py:121`), 플레이스홀더
  private key(`-----BEGIN PRIVATE KEY-----\nEXAMPLE\n-----`)뿐.
- `.env`는 `.gitignore`에 포함(`.env`, `.env.*`, `!.env.example`), git 히스토리에 실제 `.env`가
  커밋된 적 없음(`backend/.env.example`만 추적).
- `backend/Dockerfile`은 `.env`를 이미지에 `COPY`하지 않고, non-root(`appuser`, uid 1000)로 실행.
  Terraform `tfstate`/`tfvars`도 gitignore(상태 파일에 시크릿이 남는 유출 경로 차단).
- CI 워크플로 없음(`.github`에 PR/이슈 템플릿만) → CI 시크릿 노출 경로 없음.
- 신규 시크릿(`PLATFORM_AWS_ACCESS_KEY_ID`/`SECRET_ACCESS_KEY`, `OPENAI_API_KEY`)은
  `docker-compose.yml`에서 전부 빈 기본값(`${...:-}`)이고, `backend/.env.example:44,53`이
  *"실제 키는 이 예시 파일에 절대 커밋하지 말 것"* 을 명시.

**예외(실질 위험, 미해결):** `docker-compose.yml:27`에 **실동작 폴백 시크릿이 커밋**돼 있다.

```yaml
CREDENTIAL_ENCRYPTION_KEY: ${CREDENTIAL_ENCRYPTION_KEY:-ItGuIvwBa1vpQkpSuONI0GHpbxwtSxL8UPh4dp340ak=}  # 유효한 32바이트 base64 키
JWT_SECRET_KEY: ${JWT_SECRET_KEY:-dev-only-jwt-secret-change-me}
```

- 이 키는 `backend/app/security/credential_crypto.py`가 저장된 모든 자격증명(AES-256-GCM)을
  복호화하는 키다. 이번 AWS 위임 도입으로 **신규 AWS 위임 credential은 `role_arn`/`external_id`만
  저장(비밀 아님)** 하지만, 레거시 AWS 장기 키·**전체 Azure `client_secret`·GCP 서비스계정 키 JSON**
  은 여전히 이 키로만 복호화된다.
- env를 오버라이드하지 않고 이 compose로 배포하면 **저장소 + DB 덤프만으로** 그 값들이 복호화된다.
  `#16`(DB 구축) 커밋부터 이 폴백이 존재한다.
- 완화 요소: `backend/README.md:169`가 *"운영에서는 compose/.env에 두지 말고 Secrets Manager/
  Vault로 런타임 주입"* 하라고 명시. 그러나 코드상 폴백이 살아 있어 실수로 그대로 뜰 수 있다.

## 2. 권한 범위(최소 권한) — 티빙과 정반대 설계, 단 조건부

**설계 자체는 안전한 방향이다:**
- 플랫폼 IAM 사용자(`mcp-platform-caller`)에 **`sts:AssumeRole` 하나만**, 그것도
  `Resource: "arn:aws:iam::*:role/MultiCloudOps*"` 로 **역할 이름 접두사까지 제한**
  (`docs/AWS_Delegation_Role_Setup_Guide.md` §2). `*`가 아니다. → 이 키가 유출돼도 "우리를 이미
  신뢰하도록 설정된 `MultiCloudOps*` 역할"만 빌릴 수 있어 마스터키가 되지 않는다.
- **ExternalId 강제됨**: 모든 `assume_role()` 호출이 `ExternalId=external_id`를 넘기고
  (`backend/app/providers/session.py:93`), 고객에게 발급하는 신뢰 정책에도
  `Condition: {"StringEquals": {"sts:ExternalId": external_id}}` 가 들어간다
  (`backend/app/routers/credentials.py:307`). confused-deputy 방어가 걸려 있다. `external_id`는
  요청마다 새 UUID로 발급하고 서버에 보관하지 않는다(`credentials.py:302`).

**반드시 짚어야 할 단서:**
- **(a) 범위 제한이 코드가 아닌 콘솔 IAM 정책에만 존재한다.** `assume_role()`은 저장된 `role_arn`을
  **검증 없이 그대로 STS에 전달**한다(`session.py:90`). `PLATFORM_AWS_ASSUMABLE_ROLE_PATTERN`은
  에러 메시지·UI 접두사 표시용으로만 쓰인다(`session.py:103`, `credentials.py:297`). 즉 범위
  제한의 실효성은 **플랫폼 IAM 사용자의 인라인 정책이 가이드대로 설정돼 있느냐에 전적으로 의존**한다.
  → **코드로 확인 불가. AWS IAM 콘솔에서 해당 사용자의 정책이 실제로 `sts:AssumeRole` +
  `Resource: .../MultiCloudOps*` 뿐이고 다른 권한이 없는지 직접 확인해야 한다.**
- **(b) 빌리는 역할 자체는 과대권한이다.** 온보딩 안내가 고객에게 `AmazonEC2FullAccess`/
  `AmazonRDSFullAccess`/`AmazonS3FullAccess`/`CloudFrontFullAccess`(AWS 관리형 **FullAccess**)를
  붙이라고 한다(`credentials.py:317-322`, 가이드 §2). 임시 자격증명(기본 1h, CLI 접속용 15분)이라
  시간·계정 단위로 격리되지만, 발급된 토큰의 권한 폭은 최소권한이 아니다.
- **(c) Azure/GCP는 위임 미구현.** `backend/app/providers/__init__.py:60-64`의 `SUPPORTED_AUTH_TYPES`
  에서 azure/gcp는 `access_key`만 지원 → 여전히 장기 `client_secret` / SA 키 JSON 전체를 암호화
  저장. "장기 키 제거"는 **AWS 신규 위임 등록에 한해서만** 성립한다.

## 3. 임시 자격증명(STS) 처리 방식 — 양호

- `assume_role()` 결과(임시 키 3종)는 **메모리에만** 존재한다. DB·로그·감사 이벤트·에러 메시지에
  남기지 않는다(`session.py` 모듈 docstring이 원칙을 명시). 사용자에게 직접 건네는 CLI 자격증명은
  기본 세션(1h)이 아니라 `duration_seconds`(기본 15분)로 더 짧게 끊는다(`backend/app/providers/aws.py:180`).
- 로그 redaction(`backend/app/logging_config.py`의 `_redact`)·감사 필터
  (`backend/app/audit.py`의 `_sanitize_metadata`)가 `secret_access_key`/`access_key_id`/`token`/
  `authorization`/`private_key` 등을 마스킹·제거. `credential_crypto.py`는 예외 메시지에 키·평문을
  넣지 않도록 설계.
- STS 세션 이름(`multicloud-ops-{credential_id}`)이 **고객 계정 CloudTrail**에 찍히도록 구성
  (`session.py:38`) — 고객이 "누가 왜 들어왔는지" 추적 가능.
- **AI 에이전트 외부 전송 점검**: `backend/app/agent.py`의 `build_user_context`는 OpenAI로
  리소스 이름/리전/상태/추정비용·프로비저닝 job 상태만 보내며, **자격증명·키는 전송하지 않는다**
  (`agent.py:62-133`). OpenAI 키는 서버측 Bearer 헤더로만 사용. 단, 사용자 인프라 인벤토리가
  제3자(OpenAI)로 나가는 점은 데이터 공유 관점에서 인지 필요.

## 4. 배포 환경 — 정적 키 제거가 "가능"하도록 설계됨

- `_platform_sts_client()`가 **PLATFORM 키가 비어 있으면 boto3 기본 자격증명 체인**(EC2 instance
  role / ECS task role / 환경변수)으로 폴백한다(`session.py:56-66`, `config.py:49-53` 주석). →
  서버를 **AWS 컴퓨트 위에 올리고 두 키를 비우면 장기 플랫폼 키가 완전히 사라진다.** 프롬프트가
  원한 "정적 키 원천 제거" 구조가 코드에 준비돼 있다.
- 그러나 **프로덕션 호스팅/실행 신원이 저장소에 정의돼 있지 않다**(ECS/EC2/k8s/배포 매니페스트·CD
  워크플로 0건, `docker-compose.yml`은 로컬/데모용). → 실제로 AWS 컴퓨트 위에서 도는지는
  **코드로 확인 불가.** 능력은 있으나 배포 결정은 레포에 없다.
- `CREDENTIAL_ENCRYPTION_KEY`는 컴퓨트 역할로 대체할 성질이 아니라 Secrets Manager/SSM Parameter
  Store 주입 + compose 폴백 제거가 정답(README:169가 이미 그 방향을 적어둠).

## 5. 탐지·모니터링 — 없음

- `cloudtrail|guardduty|securityhub|cloudwatch alarm|anomaly` 스캔 결과 저장소/문서 전체 **0건.**
- STS 세션 이름이 고객 CloudTrail에 남도록 배려는 돼 있으나, 그건 고객 쪽 추적용이고 **우리 플랫폼
  쪽의 이상행위 탐지·알림은 부재.** 티빙 사건 세 번째 원인(실시간 탐지 부재)과 동일 상태.

---

## 6. 최종 결론 (4버킷)

| 항목 | 판정 |
|---|---|
| **하드코딩/커밋 이력** | **대체로 없음** — 실 키·히스토리 유출 0. **예외**: `docker-compose.yml:27` `CREDENTIAL_ENCRYPTION_KEY`(+`JWT_SECRET_KEY`) 실동작 폴백 기본값 커밋 → 레거시 AWS·전체 Azure/GCP 자격증명을 여는 사실상 마스터키. |
| **권한 범위** | **설계는 안전(역할 이름 접두사 + ExternalId), 단 조건부** — ① 범위 제한이 **코드가 아닌 콘솔 IAM 정책에만** 존재 → **콘솔 확인 필수**; ② 빌리는 역할이 FullAccess 4종이라 최소권한 아님; ③ Azure/GCP는 위임 없이 장기 키 병존. |
| **정적 키 제거 가능성** | **가능(설계됨)** — PLATFORM 키를 비우면 컴퓨트 IAM 역할로 대체되도록 폴백 구현됨. 단 실제 배포가 AWS 컴퓨트인지는 레포 미정의 → **확인 불가**. 암호화 키는 별도로 Secrets Manager 주입 + compose 폴백 제거 필요. |
| **탐지 체계** | **없음** — CloudTrail/GuardDuty/알림 코드 0건. |

**티빙 사건과의 관계:** 이번 AWS 위임 도입으로 **핵심 축(과대권한 마스터키)에서 티빙과 정반대**가
됐다 — 유출 시에도 접두사 제한 + ExternalId로 마스터키가 되지 않게 설계됨(단, 콘솔 IAM이
가이드대로 설정됐다는 전제). 다만 **남은 티빙형 리스크 3가지**: (1) 커밋된 암호화 키 폴백이
레거시·타 CSP 자격증명의 마스터키, (2) 실시간 탐지 부재, (3) 빌리는 역할이 최소권한 아님.

---

## 7. 우선 조치 (높은 순)

1. **[높음] 암호화 키 폴백 제거 + 로테이션.** `docker-compose.yml`의 `CREDENTIAL_ENCRYPTION_KEY`/
   `JWT_SECRET_KEY` 폴백 기본값 제거(미설정 시 기동 실패). 커밋된 키는 이미 노출된 것으로 간주해
   로테이션하고 레거시·Azure·GCP 자격증명을 재암호화. 운영은 Secrets Manager/Vault 런타임 주입.
2. **[높음] 콘솔 확인.** `mcp-platform-caller` IAM 사용자 정책이 실제로 `sts:AssumeRole` +
   `Resource: .../MultiCloudOps*` 뿐인지, 다른(넓은) 권한이 섞여 있지 않은지 IAM 콘솔에서 직접 확인.
3. **[중간] 코드 레벨 이중 방어.** `assume_role()` 호출 전 `role_arn`을
   `PLATFORM_AWS_ASSUMABLE_ROLE_PATTERN`과 대조해 벗어나면 거부(콘솔 IAM 의존을 이중화).
4. **[중간] 탐지 도입.** CloudTrail + GuardDuty, 최소한 플랫폼 IAM 사용자의 비정상 AssumeRole
   사용 알림.
5. **[중기] 최소권한화.** 온보딩 역할을 FullAccess 4종 → 실제 호출 API(`resource_actions.py`/각
   러너/`providers/*`) 기반 최소권한으로 축소. Azure/GCP도 위임(Workload Identity/Lighthouse)
   전환 검토(→ `docs/change_iam.md` §2·§4 참고).

---

> 본 문서는 점검 결과 기록이다. "확인 불가"로 표시한 항목(콘솔 IAM 정책 실제 내용, 프로덕션 실행
> 신원)은 코드 밖에서 별도 확인이 필요하며, 확정된 것처럼 인용하지 않는다.
