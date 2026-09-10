# dev-tools

팀 확정 화면(`frontend/`)과는 별개인 **개발/QA용 임시 도구** 모음. 배포 대상이 아니고,
`docs/01_API_명세서_v1.1.md` 화면 목록에도 포함되지 않는다.

## `azure-vm-provisioning-test.html`

Azure VM 프로비저닝(`POST /provisioning/azure/vm`)이 실제로 동작하는지 눈으로 확인하기
위한 페이지. 빌드 없이 브라우저에서 파일을 직접 열면 된다(`open dev-tools/azure-vm-provisioning-test.html`
또는 더블클릭).

사용 순서(페이지 안에 그대로 안내돼 있음):

1. 테스트 사용자 생성(`POST /api/v1/dev/users` — dev 전용, 정식 스펙 아님. 회원가입 API가
   생기면 이 단계와 라우터 자체를 없앤다)
2. Azure credential 등록(`POST /api/v1/credentials/azure`) — 실제 Azure Service Principal
   (tenant_id/client_id/client_secret/subscription_id)이 필요하다
3. VM 생성 요청(`POST /provisioning/azure/vm`)
4. job 상태 폴링(`GET /provisioning/jobs/{id}`) — `success`/`failed`로 끝날 때까지 3초 간격

⚠️ 3단계에서 실제 Azure 구독에 VM 생성을 시도한다(과금 가능). 진짜로 리소스가 생겼는지는
Azure 포털에서 직접 확인해야 한다 — 이 페이지는 우리 API가 올바르게 호출되고 상태를
반영하는지까지만 보여준다.

API 서버는 `docker compose up`으로 띄워둔 상태여야 한다(`http://localhost:8000`).
CORS는 개발 편의상 전체 허용(`app/main.py`)돼 있으니 운영 배포 전 반드시 좁혀야 한다.
