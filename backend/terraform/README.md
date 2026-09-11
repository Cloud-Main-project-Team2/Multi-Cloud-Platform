# terraform

프로비저닝 "생성" 전용 Terraform 모듈 모음. 기존 리소스의 조회·시작·중지·삭제에는 쓰지 않는다
(`docs/01_API_명세서_v1.1.md` 10절).

```
terraform/
  azure/vm/   # POST /provisioning/azure/vm 이 사용하는 모듈 (azurerm)
```

각 프로비저닝 요청(job)마다 `app/services/terraform_runner.py`가 이 모듈을
`TERRAFORM_RUNS_DIR`(기본 `terraform/.runs/`) 아래 job 전용 디렉터리로 복사해 격리 실행한다.
격리 디렉터리 하나 = job 하나 = Terraform state 하나. 이 디렉터리와 그 안의 `*.tfstate`,
`*.tfvars`, `.terraform/`는 `.gitignore`로 커밋에서 제외된다.

## 인증

CSP 자격 증명은 파일로 쓰지 않고, 실행 직전에 복호화해 provider별 표준 환경변수로만
subprocess에 주입한다(azurerm: `ARM_CLIENT_ID`/`ARM_CLIENT_SECRET`/`ARM_TENANT_ID`/
`ARM_SUBSCRIPTION_ID`). 프로세스 종료 후 환경변수는 사라지며 디스크에 남지 않는다.

VM의 OS 접속 비밀번호(`admin_password`)도 같은 원칙을 따른다 — CSP 계정 자격증명은
아니지만 여전히 비밀값이라 tfvars 파일에 쓰지 않고 `TF_VAR_admin_password` 환경변수로만
전달한다(`app/services/provisioning/azure_vm.py`). 배경은 CLAUDE.md
"Azure VM `admin_password` 정책" 참고.

## 알려진 한계 (MVP)

- **state backend가 로컬 파일**이다. `provisioning_jobs.terraform_state_ref`에는 이 로컬 경로만
  참조로 저장한다(state 본문은 저장하지 않음 — 정책대로). 컨테이너 재시작·replica 증설 시
  state가 유실될 수 있으므로, 운영 배포 전 Azure Storage 같은 원격 backend로 교체해야 한다.
- **worker 없이 FastAPI `BackgroundTasks`로 실행**한다. API 프로세스가 죽으면 `running`
  상태로 멈춘 job이 남을 수 있다. 별도 워커(Celery/RQ 등) 도입 전까지의 임시 구현이다.
