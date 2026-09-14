# Azure Storage Account / Database 프로비저닝 구현 결정사항 (2026-09-14)

> 작성자: 이승현(`seunghyun/be-azure-db-storage`). AWS(S3/RDS)·GCP(Cloud SQL/Cloud Storage)
> 러너 구현 패턴과 `docs/멀티클라우드 3사 기능 맵핑 — 설정값 입력 범위 (2026-09-10).md`를 기준으로
> Azure Storage Account(`storage_account`)·Database(`sql_database`, MySQL/PostgreSQL/SQL Server
> 3엔진)를 구현하면서, **팀 문서에 명시되지 않아 직접 판단해서 정한 것들**을 정리한다. 문제가
> 있다고 판단되면 팀 리뷰에서 지적해달라 — 확정된 팀 정책이 아니라 "일단 이렇게 정하고 진행했다"는
> 기록이다.

## 공통 원칙(기존 러너에서 그대로 가져온 것 — 새로 정한 게 아님)

- flat 계약(`validate_spec`/`build_tfvars`/`run`/`SENSITIVE_PROVIDER_SPEC_FIELDS`)
- job마다 자기 `azurerm_resource_group` 생성, `ARM_*` 환경변수 인증
- 비밀값(비밀번호)은 `TF_VAR_*` 환경변수로만 전달, DB(`spec_json`)엔 저장 안 함

아래부터가 이번 세션에서 **제가 새로 정한 것**이다.

---

## Storage Account (`azure`, `storage_account`)

### 1. 리전 허용 목록을 새로 추가함
기존 `azure_provisioning.py`(VM 러너)는 리전 검증이 아예 없다(`region: str`만 있고 allow-list
없음). 하지만 S3(`aws_s3_provisioning.py`)/RDS/Cloud SQL/Cloud Storage 러너는 전부
"실제 과금이 발생하는 리소스라 임의 리전을 그대로 Terraform에 넘기지 않는다"는 원칙으로 allow-list를
쓰고 있어, 새 러너도 이 원칙을 따라 프론트 `REGIONS.azure`와 동일한
`("koreacentral", "eastus", "koreasouth", "canadacentral")`을 추가했다.

**남는 질문**: VM 러너에도 같은 검증을 소급 적용할지는 이번 범위에서 결정하지 않았다(기존 구현은
안 건드림).

### 2. 계정 이름 조립 방식을 새로 만듦
Azure Storage Account 이름은 S3/GCS 버킷과 달리 **하이픈을 허용하지 않는다**(소문자/숫자만,
3~24자, 전역 유일). 공용 프론트 입력 필드(placeholder `my-unique-bucket`)는 하이픈을 전제하지만,
Azure 조합만 하이픈 없는 이름을 요구하도록 별도 정규식(`^[a-z0-9]{2,21}$`)을 만들고, 전역
유일성을 위해 `mcp{name}{job_id}`(하이픈 없이 이어붙임, 24자 초과 시 자름)로 조립했다.

### 3. Blob 컨테이너를 만들지 않기로 함
Storage Account만 생성하고 그 안에 컨테이너(=S3의 버킷/GCS의 버킷에 해당하는 실사용 단위)는 만들지
않았다. **AWS/GCP는 이 문제 자체가 없다** — S3 버킷, GCS 버킷은 그 자체가 이미 "안에 객체를 바로
넣을 수 있는" 최상위 저장 단위라 별도 하위 리소스가 필요 없다. Azure만 "Storage Account(계정) →
Container(실사용 단위)"로 한 단계 더 나뉘는 구조라, GCS 러너가 버킷 하나만 만드는 것과 결과물의
"단순함"을 맞추려고 컨테이너까지는 안 만들기로 했다. 실사용 시 컨테이너가 없으면 바로 데이터를
못 넣는다는 단점이 있다 — S3/GCS는 생성 직후 바로 쓸 수 있는데 Azure만 그렇지 않다는 뜻이므로,
필요하면 후속 세션에서 기본 컨테이너 하나를 추가하는 걸 검토해야 한다.

### 4. 중복성/버전관리를 변수화하지 않고 하드코딩함
`account_replication_type = "LRS"`, 버전관리 비활성을 변수가 아니라 Terraform 리소스에 직접 썼다
(맵핑 문서 "완전 제외" 필드라 사용자 입력을 안 받는 건 확정 사항이지만, 그걸 변수 기본값으로 둘지
하드코딩할지는 제 선택). 참고한 두 선례가 서로 다르게 처리한다:
- **GCS 러너**: `uniform_bucket_level_access=true`처럼 안전 기본값을 리소스에 직접 하드코딩(제가
  따른 방식).
- **S3 러너**: 별도 리소스(`aws_s3_bucket_public_access_block`)를 무조건 추가로 만들어 강제(변수가
  아니라 "존재 자체"로 강제 — 더 명시적이지만 리소스가 하나 더 늘어남).

Azure Storage Account는 퍼블릭 차단이 계정 리소스 자체의 속성(`allow_nested_items_to_be_public`)이라
별도 리소스 없이 GCS 방식(속성 하드코딩)이 구조적으로 더 맞았다.

---

## Database (`azure`, `sql_database` — MySQL/PostgreSQL/SQL Server)

### 5. 시딩된 서비스 코드 하나로 3개 엔진을 처리하는 구조로 설계함
`service_catalog`엔 `(azure, sql_database)` 한 행만 있는데, 프론트가 실제로 보내는 DB 엔진은
MySQL/PostgreSQL/SQL Server 3종이다(Azure에서 이 셋은 서로 다른 리소스 타입 —
`azurerm_mysql_flexible_server`/`azurerm_postgresql_flexible_server`/`azurerm_mssql_server`).
새 `service_catalog` 행이나 마이그레이션을 추가하지 않기 위해, **러너 하나가 `engine` 값에 따라
`backend/terraform/azure/database/{mysql,postgresql,sql_server}/` 중 하나의 Terraform 서브모듈을
선택해서 실행**하는 구조로 결정했다. `terraform_runner.py`(공용 코어)는 수정하지 않았다 — 모듈
경로를 호출부가 파라미터로 넘기는 기존 구조를 그대로 활용했다.

**AWS/GCP는 왜 이 문제가 없었나**: AWS RDS(`aws_db_instance`)와 GCP Cloud SQL
(`google_sql_database_instance`)은 둘 다 **하나의 Terraform 리소스 타입 안에서 `engine`
파라미터만 바꿔** MySQL/PostgreSQL(+RDS는 그 외 엔진들)을 전부 처리한다 — 그래서 러너 하나·모듈
하나로 충분했다. **Azure만 구조적으로 다르다**: MySQL/PostgreSQL Flexible Server와 Azure SQL(mssql)이
애초에 서로 다른 ARM 리소스 타입이라, AWS·GCP가 쓴 "한 모듈 + param 분기" 패턴을 그대로 못 쓰고
"러너 하나 + 모듈 3개 분기"라는 제3의 패턴을 새로 만들어야 했다. 세 개의 `service_catalog` 행으로
나누는 안도 있었지만, "이미 시딩된 걸 안 건드린다"는 원칙을 우선해 지금 구조를 택했다.

### 6. 접근 방식을 "비공개(VNet 통합/Private Endpoint)"로 최종 확정함 (2026-09-14 팀 논의 후 변경)

**처음엔 GCP Cloud SQL과 같은 "퍼블릭 IP + 방화벽 전체 허용" 데모 구성을 따르려 했으나, 팀(사용자)
확인 후 뒤집었다.** 논의 경과:

- **AWS RDS는 정반대를 택했다** — 항상 `publicly_accessible=false` + 기본 VPC 안에서만 접근 가능한
  비공개 구성이다("이 리소스는 항상 비공개"가 팀 결정 사항으로 이미 문서화돼 있음).
- 처음엔 "Azure에서 같은 수준으로 하려면 VNet/서브넷 delegation이 추가로 필요해 범위가 커진다"는
  이유로 GCP의 느슨한 방식을 따르려 했다.
- **하지만 이 플랫폼은 데모 목업이 아니라 사용자의 진짜 클라우드 계정 + 진짜 자격증명으로 실제
  리소스를 만드는 도구다.** 지금까지 만든 모든 서비스(S3 퍼블릭 차단, RDS 비공개, CloudFront도
  오리진만 받고 나머진 제한)가 전부 **"사고 나면 되돌릴 수 없으니 기본값은 항상 안전하게"** 원칙을
  따르고 있다 — 이 원칙 앞에서는 "GCP가 이미 느슨하게 했으니 따라간다"는 근거가 부족하다는 게
  최종 판단이었다.
- 그래서 **AWS RDS와 같은 보안 수준(비공개, 인터넷 노출 없음)으로 최종 확정**했다. MySQL/PostgreSQL
  Flexible Server는 각자 VNet + 위임된 서브넷 + Private DNS Zone을, Azure SQL(mssql)은
  `public_network_access_enabled=false` + Private Endpoint + Private DNS Zone
  (`privatelink.database.windows.net`)을 구성한다.

**결과적으로 3사 DB 보안 수준**: AWS·Azure(비공개) vs **GCP만 여전히 퍼블릭+전체허용 데모**로 남아
불일치가 있다. 이건 제가 새로 만든 불일치가 아니라 GCP 세션에서 이미 있던 타협인데, 이번에 Azure를
따라 고치지 않고 그대로 뒀다 — **GCP Cloud SQL도 같은 원칙으로 고칠지는 이 브랜치 범위 밖이라 팀이
별도로 판단해야 한다.**

**남는 한계**: AWS는 "같은 기본 VPC의 EC2에서 RDS 접속 가능"까지 되지만, Azure는 VM 모듈이 job마다
독립된 VNet을 만드는 구조라 **VM↔DB 자동 연결까지는 안 된다**(원래 없던 기능이라 손해는 아니지만,
AWS와 완전히 같은 수준의 "실사용 편의성"은 아니다). 우리 서비스엔 VPN/Bastion 같은 "만든 리소스
안으로 들어가게 해주는" 기능이 없으므로, 실제로 접속하려면 사용자가 같은 VNet 안에 별도 리소스를
직접 구성해야 한다 — 이는 AWS RDS도 이미 동일한 처지다.

### 7. 엔진별 SKU/버전/스토리지 고정값을 직접 고름
맵핑 문서는 "사양은 완전 제외(사용자 입력 안 받음)"까지만 정하고 구체적인 값은 안 정해서, 각 엔진이
지원하는 가장 작은/저렴한 사양으로 제가 직접 골랐다:

| 엔진 | SKU | 버전 | 비고 |
|---|---|---|---|
| MySQL | `B_Standard_B1s` | `8.0.21` | Burstable 최소 사양 |
| PostgreSQL | `B_Standard_B1ms` | `15` | PostgreSQL Flexible Server는 B1s가 없어 B1ms가 최소 |
| SQL Server | DB SKU `Basic` | 서버 버전 `12.0` | Standard/Premium 대비 가장 저렴 |

### 8. 서버 이름에 `job_id`를 붙여 전역 유일성을 확보함
RDS(`instance_name`)와 Cloud SQL(`instance_name`)은 계정/프로젝트 범위에서만 유일하면 되지만,
Azure의 Database 서버 이름(MySQL/PostgreSQL Flexible Server, SQL 논리 서버)은 **Azure 전역에서
유일**해야 한다고 판단해, S3 버킷과 같은 이유로 `mcp-{name}-{job_id}` 패턴(하이픈 허용, 서버 이름은
하이픈 가능)을 썼다.

### 9. `master_username` 예약어 목록과 형식 규칙을 직접 작성함
**AWS RDS는 이 문제 자체를 회피했다** — `aws_rds_provisioning.py`는 `master_username`을 사용자
입력으로 아예 안 받고 서버가 `mcp_admin`으로 고정한다. 이유가 정확히 이 문제 때문이라고 코드
주석에 적혀있다: "AWS는 engine별로 admin 등 예약어를 master_username으로 못 쓰게 막는데, 엔진마다
예약어 목록이 달라 사전 검증이 번거롭다." GCP Cloud SQL도 비슷하게 관리자 계정명을 엔진별
고정값(`root`/`postgres`/`sqlserver`)으로 서버가 정한다(`_ENGINE_CONFIG`) — 사용자가 직접 고르지
않는다.

**저는 이 회피를 못 썼다** — 프론트(`provisioning.js`의 `dbBoxEl()`)가 **Azure만** 마스터
사용자명을 사용자 입력으로 받도록 이미 구현돼 있다(AWS/GCP는 프론트에서도 비밀번호만 받음). 그래서
AWS·GCP가 피해간 "엔진별 예약어 검증"을 Azure에서는 제가 직접 만들어야 했다 — 공통으로 안전한
보수적인 차단 목록(`admin`/`administrator`/`root`/`guest`/`public`/`sa`/`azure_superuser` 등)과
형식 정규식(`^[A-Za-z][A-Za-z0-9_]{0,62}$`)을 적용했다. 실제 Azure API의 정확한 예약어 목록과
100% 일치한다고 보장하지 않는다 — 최종 검증은 여전히 Azure가 한다.

### 10. `master_password` 복잡도 검증을 새로 추가함(3사 중 제일 엄격)
세 러너의 비밀번호 검증 수준이 다 다르다:
- **AWS RDS**: 금지 문자(`/`, `"`, `@`, 공백)만 체크, 복잡도(대소문자/숫자/특수문자 조합)는 안 봄.
- **GCP Cloud SQL**: 최소 길이(8자)만 체크.
- **기존 Azure VM**(`azure_provisioning.py`)의 `admin_password`: 최소 길이(12자)만 체크.

Database 쪽은 Azure Database 서비스들이 실제로 "8자 이상 + 대문자/소문자/숫자/특수문자 중 3종
이상"을 요구하므로, 세 선례 중 어디에도 없던 **복잡도(문자 종류 조합) 검증을 새로 추가**했다 —
apply 단계에서 실패하는 대신 `validate_spec()` 단계(422)에서 미리 걸러지도록 하기 위해서다. 결과적으로
지금 세 러너 중 이게 제일 엄격한 비밀번호 검증이 된다. 기존 VM 러너는 건드리지 않았다.

### 11. 인벤토리 표시용 리소스 타입 이름을 새로 지음
`_resource_attrs()`가 엔진별로 다른 `original_resource_type`
(`"Azure Database for MySQL"`/`"Azure Database for PostgreSQL"`/`"Azure SQL Database"`)을 반환하도록
했다 — 기존 관례(`"RDS Instance"`, `"Cloud SQL Instance"` 같은 사람이 읽는 이름)를 따른 제 네이밍이며,
Azure 공식 리소스 타입 문자열(ARM 타입 등)과는 다르다.

### 12. GCP Cloud SQL과 달리 서버뿐 아니라 실제 데이터베이스 객체까지 생성함
GCP Cloud SQL 러너는 인스턴스만 만들고 그 안의 개별 데이터베이스는 안 만드는데(관리자 계정 생성까지만
범위), 이번 Azure 러너는 AWS RDS 방식을 따라 서버/인스턴스 생성과 함께 **실제 이름 붙은 데이터베이스
객체**(`azurerm_mysql_flexible_database` 등)까지 같이 만든다 — "생성 직후 바로 쓸 수 있는 결과물"이
더 유용하다고 판단했다.

---

## 다른 팀원이 만든 테스트 파일을 2곳 수정함 (불가피한 충돌)

`(azure, sql_database)`에 러너를 등록하면서, 다른 팀원이 이미 이 조합을 **"provisionable하지만
러너가 없는 예시"**로 쓰던 기존 테스트 2개가 깨졌다(제가 등록한 순간 501이 아니라 실제
validate_spec이 도는 422로 바뀌기 때문 — 회피 불가능한 충돌):

- `backend/tests/test_aws_provisioning_api.py::test_create_job_without_runner_is_501`
- `backend/tests/test_provisioning_api.py::test_create_job_returns_501_when_no_runner_registered`

두 테스트 모두 **예시로 쓴 provider/service_code를 `(azure, sql_database)` → `(azure, cdn)`으로만
교체**했다(`cdn`은 여전히 러너가 없는 조합). 테스트 로직·검증 내용은 전혀 안 건드렸다 — 예시 하나
바꾼 것뿐이다. 다른 수정은 없다.

## 아직 이번 범위에 안 넣은 것 (참고 — 기존 팀 결정과 동일 선상)

- `app/providers/azure.py`의 discover/action 어댑터(Storage Account/Database SDK 기반 조회·시작·
  중지·삭제)는 만들지 않았다. `CLAUDE.md`에 이미 "Azure SQL Database/Storage Account/CDN,
  GCP Cloud SQL/Storage/CDN은 어댑터가 없어서가 아니라 의도적으로 범위 밖에 둔 것"이라고 기록돼 있고,
  GCP Cloud SQL/Storage도 마찬가지라 같은 선을 따랐다.
- 프론트(`provisioning.js`)의 `SERVICE_CODE`/`buildProviderSpec()`에 Azure Storage/DB 실 연동을
  추가하는 것도 이번 백엔드 전용 브랜치 범위 밖이다(GCP Cloud SQL/Storage도 같은 상태로 남아있음 —
  프론트는 여전히 `simulateTarget()` 시뮬레이션).
